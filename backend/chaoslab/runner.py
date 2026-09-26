"""Executes one test case end to end through a real voice loop:

    simulated caller (sarvam-105b) -> Bulbul v3 TTS -> channel (DSP) -> Saaras STT -> agent under test -> tools

Every intermediate artefact (clean audio, degraded audio, both transcripts, tool calls, latencies) is kept,
so any failure can be replayed and blamed on the layer where it happened.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import numpy as np

from . import audio
from .models import Conversation, Finding, Seed, Severity, Turn
from .mutations import apply_truth, caller_brief, canonical, channel_for
from .entities import script_share
from .oracles import check_validity, evaluate
from .sarvam import Sarvam, SarvamError
from .target import Agent, AgentConfig

CALLER_VOICES = ["kavitha", "anand", "priya", "rahul", "gokul", "shruti"]
BABBLE_LINES = [("en-IN", "Sir your order number is being checked, please hold the line for one minute."),
                ("hi-IN", "मैंने कल ही पेमेंट कर दिया था, फिर भी मैसेज आ रहा है।"),
                ("ta-IN", "நாளைக்கு காலையில் பத்து மணிக்கு வாங்க, டாக்டர் இருப்பார்."),
                ("te-IN", "మా ఇంటికి డెలివరీ ఇంకా రాలేదు, దయచేసి చూడండి."),
                ("kn-IN", "ಬಸ್ ಇನ್ನೂ ಬಂದಿಲ್ಲ, ನಾನು ಸ್ವಲ್ಪ ತಡವಾಗಿ ಬರುತ್ತೇನೆ.")]
MAX_TURNS = 9


def case_id(seed_id: str, mutations: tuple[str, ...]) -> str:
    return f"{seed_id}:{'+'.join(mutations) or 'baseline'}"


def stable_int(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class Runner:
    def __init__(self, client: Sarvam, out_dir: Path):
        self.client = client
        self.out = out_dir
        (self.out / "audio").mkdir(parents=True, exist_ok=True)
        self._babble: np.ndarray | None = None
        self._babble_lock = asyncio.Lock()

    async def babble(self) -> np.ndarray:
        async with self._babble_lock:
            if self._babble is None:
                clips = await asyncio.gather(*[self.client.tts(t, lang=l, speaker=v, sample_rate=16000)
                                               for (l, t), v in zip(BABBLE_LINES, CALLER_VOICES)])
                self._babble = audio.build_babble(list(clips))
        return self._babble

    async def _caller_turn(self, brief: str, history: list[dict], seed_n: int) -> dict:
        msgs = [{"role": "system", "content": brief}] + history
        try:
            out = await self.client.chat_json(msgs, model="sarvam-105b", reasoning_effort=None, temperature=0.7,
                                              max_tokens=400, seed=seed_n)
        except ValueError:
            out = {"utterance": "", "carries": [], "done": True}
        return out

    async def _translate_agent_turns(self, conv: Conversation, lang: str) -> None:
        """Agents may say amounts as native-language words; translate those turns so oracles can read them."""
        todo = [t for t in conv.turns if t.role == "agent" and t.text and (script_share(t.text, lang) or 0) > 0.3]
        if not todo or lang == "en-IN":
            return
        outs = await asyncio.gather(*[self.client.translate(t.text, source=lang) for t in todo], return_exceptions=True)
        for t, o in zip(todo, outs):
            t.text_en = o if isinstance(o, str) else None

    async def run_case(self, seed: Seed, mutations: tuple[str, ...], cfg: AgentConfig, repetition: int = 0,
                       on_turn=None) -> Conversation:
        mutations = canonical(mutations)
        cid = case_id(seed.id, mutations)
        truth = apply_truth(seed, mutations)
        n = stable_int(cid, repetition)
        spec = channel_for(mutations, seed=n)
        voice = CALLER_VOICES[stable_int(seed.id) % len(CALLER_VOICES)]
        brief = caller_brief(seed, mutations, truth)
        conv = Conversation(case_id=cid, seed_id=seed.id, agent_version=cfg.version,
                            mutations=list(mutations), repetition=repetition)
        agent = Agent(cfg, self.client, lang=seed.lang)
        caller_history: list[dict] = []
        babble = await self.babble() if spec.snr_db is not None else None
        try:
            text, calls, lat = await agent.respond("(call connected)")
            conv.turns.append(Turn(role="agent", text=text, tool_calls=calls, latency_ms=lat))
            caller_history.append({"role": "user", "content": text})
            for step in range(MAX_TURNS):
                c = await self._caller_turn(brief, caller_history, n + step)
                said = (c.get("utterance") or "").strip()
                if not said:
                    break
                slots = [s for s in c.get("carries", []) if s in ("dob", "promise_amount", "promise_date")]
                tag = f"{stable_int(cid) % 10**8}_{cfg.version}_r{repetition}_t{len(conv.turns)}"
                clean = await self.client.tts(said, lang=seed.lang, speaker=voice, pace=spec.pace, sample_rate=16000)
                heard_wav = clean if spec.is_clean else audio.degrade(clean, spec, babble)
                (self.out / "audio" / f"{tag}_said.wav").write_bytes(clean)
                (self.out / "audio" / f"{tag}_heard.wav").write_bytes(heard_wav)
                stt_kw = dict(lang=seed.lang, model=cfg.stt_model, mode=cfg.stt_mode, keyterms=cfg.stt_keyterms)
                heard = (await self.client.stt(heard_wav, **stt_kw)).get("transcript", "")
                heard_clean, heard_en, clean_en = heard, None, None
                if slots:
                    # measurement instruments (not what the agent sees): a v4 transcript of clean audio as the
                    # validity control, and translate-mode readings of both, so spoken-word numbers become parseable
                    en_kw = dict(lang=seed.lang, model="saaras:v3", mode="translate")
                    if spec.is_clean:
                        heard_en = clean_en = (await self.client.stt(clean, **en_kw)).get("transcript", "")
                    else:
                        clean_in = await self.client.tts(said, lang=seed.lang, speaker=voice, sample_rate=16000)
                        heard_clean, heard_en, clean_en = [r.get("transcript", "") for r in await asyncio.gather(
                            self.client.stt(clean_in, lang=seed.lang, model="saaras:v4"),
                            self.client.stt(heard_wav, **en_kw), self.client.stt(clean_in, **en_kw))]
                conv.turns.append(Turn(role="caller", said=said, heard=heard, heard_clean=heard_clean,
                                       heard_en=heard_en, clean_en=clean_en,
                                       entity_slots=slots, audio_said=f"audio/{tag}_said.wav",
                                       audio_heard=f"audio/{tag}_heard.wav"))
                caller_history.append({"role": "assistant", "content": said})
                if on_turn:
                    await on_turn(conv)
                text, calls, lat = await agent.respond(heard)
                conv.turns.append(Turn(role="agent", text=text, tool_calls=calls, latency_ms=lat))
                caller_history.append({"role": "user", "content": text or "(silence)"})
                if agent.backend.ended or c.get("done"):
                    break
        except SarvamError as e:
            if e.status in (401, 402, 403):  # account problems invalidate the whole run: stop, don't score
                raise
            conv.error = f"{type(e).__name__}: {e}"
        except Exception as e:  # a crashed conversation is a result too, never a silent pass
            conv.error = f"{type(e).__name__}: {e}"
        if conv.error is None:
            await self._translate_agent_turns(conv, seed.lang)
        invalid = check_validity(conv, truth)
        if invalid:
            conv.error = f"INVALID: {invalid}"
        elif conv.error is None:
            conv.findings = evaluate(conv, truth, seed.lang)
        elif not conv.error.startswith("INVALID"):
            conv.findings = [Finding(oracle="HARNESS", passed=False, severity=Severity.LOW, layer=None,
                                     expected="conversation completes", actual=conv.error)]
        return conv
