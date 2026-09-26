"""Deterministic acoustic degradations. Every transform is seeded so a failing case replays bit-exact."""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, resample_poly, sosfilt


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        sr, n, ch, width = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {width * 8}-bit")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def write_wav(x: np.ndarray, sr: int) -> bytes:
    pcm = np.clip(np.round(x * 32768.0), -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)) + 1e-12))


def mix_at_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """Add noise scaled so that 20*log10(rms(speech)/rms(noise)) == snr_db exactly."""
    if len(noise) < len(speech):
        noise = np.tile(noise, int(np.ceil(len(speech) / len(noise))))
    noise = noise[: len(speech)]
    scale = rms(speech) / (rms(noise) * 10 ** (snr_db / 20))
    return speech + noise * scale


def telephony(x: np.ndarray, sr: int, seed: int = 0) -> tuple[np.ndarray, int]:
    """Narrowband phone line: 300-3400 Hz band-pass, 8 kHz, G.711 mu-law companding."""
    sos = butter(4, [300, 3400], btype="bandpass", fs=sr, output="sos")
    y = sosfilt(sos, x)
    y = resample_poly(y, 8000, sr) if sr != 8000 else y
    mu = 255.0
    comp = np.sign(y) * np.log1p(mu * np.abs(np.clip(y, -1, 1))) / np.log1p(mu)
    q = np.round(comp * 127) / 127
    y = np.sign(q) * (np.power(1 + mu, np.abs(q)) - 1) / mu
    return y.astype(np.float32), 8000


def pink_noise(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    f = np.fft.rfft(white)
    f /= np.sqrt(np.arange(1, len(f) + 1))
    return np.fft.irfft(f, n).astype(np.float32)


@dataclass(frozen=True)
class ChannelSpec:
    """Acoustic conditions for one conversation. Built from the mutation set."""
    telephony: bool = False
    snr_db: float | None = None
    noise: str = "babble"         # babble | pink
    pace: float = 1.0
    seed: int = 0

    @property
    def is_clean(self) -> bool:
        return not self.telephony and self.snr_db is None and self.pace == 1.0


def degrade(clean_wav: bytes, spec: ChannelSpec, babble: np.ndarray | None) -> bytes:
    x, sr = read_wav(clean_wav)
    if spec.snr_db is not None:
        if spec.noise == "babble" and babble is not None:
            rng = np.random.default_rng(spec.seed)
            off = int(rng.integers(0, max(1, len(babble) - 1)))
            noise = np.roll(babble, -off)
        else:
            noise = pink_noise(len(x), spec.seed)
        x = mix_at_snr(x, noise, spec.snr_db)
    if spec.telephony:
        x, sr = telephony(x, sr, spec.seed)
        # the information is gone after the 8 kHz line; upsample so the ASR sees a standard 16 kHz WAV,
        # exactly as a telephony bridge would hand audio to a voice-agent runtime
        x, sr = resample_poly(x, 16000, 8000).astype(np.float32), 16000
    peak = float(np.max(np.abs(x))) or 1.0
    if peak > 0.99:
        x = x * (0.99 / peak)
    return write_wav(x, sr)


def build_babble(clips: list[bytes], target_sr: int = 16000) -> np.ndarray:
    """Overlay several unrelated utterances (Bulbul voices) with offsets: a crowded-room / call-centre bed."""
    tracks = []
    for i, c in enumerate(clips):
        x, sr = read_wav(c)
        if sr != target_sr:
            x = resample_poly(x, target_sr, sr).astype(np.float32)
        tracks.append(np.roll(x, i * target_sr // 3))
    n = max(len(t) for t in tracks)
    bed = np.zeros(n, dtype=np.float32)
    for t in tracks:
        bed[: len(t)] += t / (rms(t) + 1e-9)
    return bed / (rms(bed) + 1e-9) * 0.1
