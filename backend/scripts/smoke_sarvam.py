"""First thing to run once SARVAM_API_KEY is set: one call to each Sarvam API this project depends on,
plus a clean vs. phone-line round trip of a Tamil amount. Prints real outputs; exits non-zero on failure."""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT.parent / ".env")

from chaoslab import audio  # noqa: E402
from chaoslab.mutations import channel_for  # noqa: E402
from chaoslab.sarvam import Sarvam  # noqa: E402


async def main():
    c = Sarvam(cache_dir=ROOT / ".cache")
    try:
        r = await c.chat([{"role": "user", "content": "ஒரு வரியில் பதில்: இந்தியாவின் தலைநகரம் எது?"}],
                         model="sarvam-105b-conversations", reasoning_effort=None, max_tokens=100)
        print("chat  :", repr(r["choices"][0]["message"].get("content")), f"{r['_latency_ms']:.0f} ms")
        text = "நான் அக்டோபர் பதினைந்தாம் தேதி நாலாயிரத்து ஐநூறு ரூபாய் கட்டுவேன்"
        wav = await c.tts(text, lang="ta-IN", speaker="kavitha", sample_rate=16000)
        print("tts   :", len(wav), "bytes")
        clean = await c.stt(wav, lang="ta-IN")
        print("stt   :", clean.get("transcript"), f"{clean['_latency_ms']:.0f} ms")
        phone = audio.degrade(wav, channel_for(("telephony", "noise_10db"), seed=1), None)
        noisy = await c.stt(phone, lang="ta-IN")
        print("phone :", noisy.get("transcript"))
    finally:
        await c.aclose()

asyncio.run(main())
