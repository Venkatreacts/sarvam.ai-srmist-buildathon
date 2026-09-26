"""End-to-end loop against a scripted Sarvam double: no network, deterministic, exercises every stage."""
import asyncio
import json
import re

import numpy as np

from chaoslab import audio
from chaoslab.lab import Lab, compare, to_sarvam_tests
from chaoslab.models import Seed, Truth
from chaoslab.runner import Runner
from chaoslab.target import AgentConfig

SEED = Seed(id="ta", title="t", lang="ta-IN", persona="p", truth=Truth())


class FakeSarvam:
    """Caller says DOB then amount. The fake ASR corrupts 4,500 -> 45,000 whenever the audio was degraded
    by noise or the phone line. The fake agent verifies, discloses nothing, and saves what it heard."""

    def __init__(self, readback: bool):
        self.readback = readback
        self.texts: list[str] = []
        self.clean: dict[int, bytes] = {}

    async def chat(self, messages, tools=None, **kw):
        last = messages[-1]
        if last["role"] == "tool":
            res = json.loads(last["content"])
            txt = "சரி, பதிவு செய்தேன்." if "ok" in res else "நன்றி, சரிபார்த்தேன். எவ்வளவு கட்டுவீர்கள்?"
            return self._msg(txt)
        u = last["content"]
        if "1994-03-12" in u:
            return self._tool("verify_customer", {"dob": "1994-03-12"})
        nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", u) if int(n.replace(",", "")) > 100]
        if nums:
            prev = [m for m in messages if m["role"] == "assistant" and m.get("content")]
            if self.readback and not any(str(f"{nums[0]:,}") in (m["content"] or "") for m in prev):
                return self._msg(f"{nums[0]:,} ரூபாய், சரியா?")
            return self._tool("record_promise_to_pay", {"amount": nums[0], "date": "2026-10-15"})
        if "ஆமாம்" in u:
            amt = [m["content"] for m in messages if m["role"] == "assistant" and m.get("content") and "சரியா" in m["content"]]
            n = int(re.findall(r"\d[\d,]*", amt[-1])[0].replace(",", ""))
            return self._tool("record_promise_to_pay", {"amount": n, "date": "2026-10-15"})
        return self._msg("வணக்கம், உங்கள் பிறந்த தேதி சொல்லுங்கள்.")

    async def chat_json(self, messages, **kw):
        agent_said = [m["content"] for m in messages if m["role"] == "user"]
        last = agent_said[-1]
        if "பதிவு செய்தேன்" in last:
            return {"utterance": "நன்றி", "carries": [], "done": True}
        if "சரியா" in last:
            return {"utterance": "ஆமாம்", "carries": [], "done": False}
        if "எவ்வளவு" in last:
            return {"utterance": "அக்டோபர் 15 அன்று 4,500 கட்டுவேன்", "carries": ["promise_amount", "promise_date"], "done": False}
        return {"utterance": "என் பிறந்த தேதி 1994-03-12", "carries": ["dob"], "done": False}

    async def tts(self, text, lang, speaker="x", pace=1.0, sample_rate=16000):
        if text not in self.texts:
            self.texts.append(text)
        idx = self.texts.index(text)
        t = np.arange(16000 + idx * 2) / 16000
        wav = audio.write_wav((0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000)
        self.clean[idx] = wav
        return wav

    async def stt(self, wav, lang="unknown", model="", mode="", keyterms=None):
        x, _ = audio.read_wav(wav)
        idx = (len(x) - 16000) // 2
        text = self.texts[idx]
        if wav != self.clean[idx]:
            text = text.replace("4,500", "45,000")
        return {"transcript": text}

    async def translate(self, text, source, target="en-IN"):
        return text

    def _msg(self, content):
        return {"_latency_ms": 5.0, "choices": [{"message": {"content": content}}]}

    def _tool(self, name, args):
        return {"_latency_ms": 5.0, "choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


CFG = AgentConfig(version="v1", system_prompt="agent {customer_name} {today}")


def lab_for(fake, tmp_path):
    return Lab(runner=Runner(fake, tmp_path), concurrency=2)


def test_clean_call_passes(tmp_path):
    fake = FakeSarvam(readback=True)
    conv = asyncio.run(Runner(fake, tmp_path).run_case(SEED, (), CFG))
    assert conv.error is None, conv.error
    assert conv.passed, [f.model_dump() for f in conv.findings if not f.passed]
    assert (tmp_path / conv.turns[1].audio_heard).exists()


def test_storm_fails_with_perception_and_shrinks_to_channel(tmp_path):
    fake = FakeSarvam(readback=True)
    lab = lab_for(fake, tmp_path)
    storm = ("codemix", "hesitation", "telephony", "noise_10db", "fast_speech")
    conv = asyncio.run(lab.runner.run_case(SEED, storm, CFG))
    f = {x.oracle: x for x in conv.findings}["PROMISE_FIDELITY"]
    assert not f.passed and f.layer.value == "PERCEPTION"
    assert any("45,000" in e for e in f.evidence)
    r = asyncio.run(lab.shrink(SEED, conv, "PROMISE_FIDELITY", CFG, trials=1))
    assert len(r.minimal) == 1 and r.minimal[0] in ("telephony", "noise_10db")


def test_readback_fix_measured_by_paired_compare(tmp_path):
    plan = [(SEED, ()), (SEED, ("hesitation",))]
    lab_a = lab_for(FakeSarvam(readback=False), tmp_path / "a")
    lab_b = lab_for(FakeSarvam(readback=True), tmp_path / "b")
    a = asyncio.run(lab_a.execute(plan, CFG))
    b = asyncio.run(lab_b.execute(plan, AgentConfig(version="v2", system_prompt=CFG.system_prompt)))
    cmp = compare(a, b)
    assert cmp["pairs"] == 2 and len(cmp["fixed"]) == 2 and cmp["regressed"] == []
    assert cmp["per_oracle"]["READBACK"]["a"]["rate"] == 0 and cmp["per_oracle"]["READBACK"]["b"]["rate"] == 1


def test_export_matches_sarvam_tests_shape():
    body = to_sarvam_tests([{"seed_id": "ta", "mutations": ["telephony"], "oracle": "PROMISE_FIDELITY", "cluster": "C1"}], [SEED])
    case = body["test_cases"][0]
    assert set(case) == {"name", "category", "user_scenario", "expected_behaviors"}
    assert "4500" in case["user_scenario"] and "Reply as JSON" not in case["user_scenario"]
