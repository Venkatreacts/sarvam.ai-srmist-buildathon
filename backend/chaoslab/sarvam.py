"""Thin async client for the Sarvam APIs this project uses.

Signatures follow docs.sarvam.ai (Sept 2026):
  POST /v1/chat/completions   sarvam-105b | sarvam-105b-conversations, tools, response_format, seed
  POST /text-to-speech        bulbul:v3, language_code, speaker, pace, speech_sample_rate
  POST /speech-to-text        saaras:v3 (mode) | saaras:v4 (keyterms), multipart file
Auth header everywhere: api-subscription-key.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

BASE_URL = os.environ.get("SARVAM_API_BASE_URL", "https://api.sarvam.ai")


class SarvamError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Sarvam API {status}: {body[:500]}")
        self.status = status
        self.body = body


class Sarvam:
    def __init__(self, api_key: str | None = None, cache_dir: str | Path = ".cache", max_concurrency: int = 6):
        self.api_key = api_key or os.environ.get("SARVAM_API_KEY", "")
        if not self.api_key:
            raise SarvamError(401, "SARVAM_API_KEY is not set")
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self._sem = asyncio.Semaphore(max_concurrency)
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=httpx.Timeout(90.0, connect=15.0),
                                       headers={"api-subscription-key": self.api_key})

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, path: str, *, json_body: dict | None = None, files=None, data=None) -> dict:
        for attempt in range(5):
            async with self._sem:
                r = await self._http.post(path, json=json_body, files=files, data=data)
            if r.status_code == 429 or r.status_code >= 500:
                wait = float(r.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(min(wait, 30))
                continue
            if r.status_code >= 400:
                raise SarvamError(r.status_code, r.text)
            return r.json()
        raise SarvamError(r.status_code, r.text)

    # ---------- LLM ----------
    async def chat(self, messages: list[dict], *, model: str = "sarvam-105b", tools: list[dict] | None = None,
                   response_format: dict | None = None, temperature: float | None = None,
                   reasoning_effort: str | None = "low", max_tokens: int = 2048, seed: int | None = None) -> dict:
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        # reasoning is on by default; an explicit null disables it (docs: "set to None")
        body["reasoning_effort"] = reasoning_effort
        if tools:
            body["tools"] = tools
        if response_format:
            body["response_format"] = response_format
        if temperature is not None:
            body["temperature"] = temperature
        if seed is not None:
            body["seed"] = seed
        t0 = time.perf_counter()
        out = await self._post("/v1/chat/completions", json_body=body)
        out["_latency_ms"] = (time.perf_counter() - t0) * 1000
        return out

    async def chat_json(self, messages: list[dict], **kw) -> dict:
        """Chat call that must return a JSON object. Retries once if the reply holds no parseable object."""
        last = ""
        for _ in range(2):
            out = await self.chat(messages, response_format={"type": "json_object"}, **kw)
            msg = out["choices"][0]["message"]
            for raw in (msg.get("content"), msg.get("reasoning_content")):
                if raw:
                    last = raw
                    try:
                        return _parse_json(raw)
                    except ValueError:
                        continue
        raise ValueError(f"model did not return JSON: {last[:200]}")

    # ---------- TTS ----------
    async def tts(self, text: str, *, lang: str, speaker: str = "shubh", pace: float = 1.0,
                  sample_rate: int = 16000) -> bytes:
        key = _hash("tts", text, lang, speaker, pace, sample_rate)
        path = self.cache / f"tts_{key}.wav"
        if path.exists():
            return path.read_bytes()
        out = await self._post("/text-to-speech", json_body={
            "text": text, "language_code": lang, "model": "bulbul:v3", "speaker": speaker,
            "pace": pace, "speech_sample_rate": sample_rate, "output_audio_codec": "wav"})
        audio = base64.b64decode("".join(out["audios"]))
        path.write_bytes(audio)
        return audio

    # ---------- Translate ----------
    async def translate(self, text: str, *, source: str, target: str = "en-IN") -> str:
        out = await self._post("/translate", json_body={"input": text[:2000], "source_language_code": source,
                                                         "target_language_code": target, "model": "sarvam-translate:v1"})
        return out.get("translated_text", "")

    # ---------- STT ----------
    async def stt(self, wav: bytes, *, lang: str = "unknown", model: str = "saaras:v3", mode: str = "transcribe",
                  keyterms: list[str] | None = None) -> dict:
        data: dict[str, Any] = {"model": model, "language_code": lang}
        if model == "saaras:v3":
            data["mode"] = mode
        if keyterms and model == "saaras:v4":
            data["keyterms"] = json.dumps(keyterms, ensure_ascii=False)
        t0 = time.perf_counter()
        out = await self._post("/speech-to-text", files={"file": ("audio.wav", wav, "audio/wav")}, data=data)
        out["_latency_ms"] = (time.perf_counter() - t0) * 1000
        return out


def _hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def _parse_json(raw: str) -> dict:
    """First complete JSON object in the text (models sometimes add prose or a second object after it)."""
    dec = json.JSONDecoder()
    i = raw.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(raw, i)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        i = raw.find("{", i + 1)
    raise ValueError(f"model did not return JSON: {raw[:200]}")
