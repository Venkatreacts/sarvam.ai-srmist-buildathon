import asyncio

import numpy as np
import pytest

from chaoslab import audio
from chaoslab.entities import extract_numbers, normalize_date, script_share, to_ascii_digits
from chaoslab.models import Conversation, ToolCall, Truth, Turn
from chaoslab.mutations import canonical, channel_for
from chaoslab.oracles import check_validity, evaluate
from chaoslab.shrink import ddmin
from chaoslab.stats import mcnemar_exact, wilson


# ---------- entities ----------
def test_indian_grouping_and_native_digits():
    assert extract_numbers("₹1,20,000 and 4,500") == [120000, 4500]
    assert extract_numbers("௪௫௦௦ ரூபாய்") == [4500]           # Tamil digits
    assert extract_numbers("४५०० रुपये") == [4500]              # Devanagari digits
    assert to_ascii_digits("೨೦೨೬") == "2026"


def test_dates():
    assert normalize_date("2026-10-15") == "2026-10-15"
    assert normalize_date("15/10/2026") == "2026-10-15"
    assert normalize_date("October 15th, 2026") == "2026-10-15"
    assert normalize_date("next friday") is None


def test_script_share():
    assert script_share("நான் 4,500 pay பண்றேன்", "ta-IN") > 0.5
    assert script_share("I will pay tomorrow", "ta-IN") == 0.0
    assert script_share("anything", "en-IN") is None


# ---------- oracles ----------
def conv_with(turns, muts=()):
    return Conversation(case_id="c", seed_id="s", agent_version="v", mutations=list(muts), turns=turns)


def agent(text, *calls):
    return Turn(role="agent", text=text, tool_calls=list(calls))


def caller(said, heard=None, slots=(), clean=None):
    return Turn(role="caller", said=said, heard=heard if heard is not None else said,
                heard_clean=clean, entity_slots=list(slots))


def tc(name, args, result):
    return ToolCall(name=name, args=args, result=result)


T = Truth()


def by(findings):
    return {f.oracle: f for f in findings}


def test_perfect_call_passes_everything():
    c = conv_with([
        agent("வணக்கம், உங்கள் பிறந்த தேதி சொல்லுங்கள்"),
        caller("1994-03-12", slots=["dob"]),
        agent("நன்றி. நிலுவை 12,450 ரூபாய்.", tc("verify_customer", {"dob": "1994-03-12"}, {"verified": True}),
              tc("get_outstanding", {}, {"amount_due": 12450})),
        caller("அக்டோபர் 15 அன்று 4,500 கட்டுவேன்", slots=["promise_amount", "promise_date"]),
        agent("4,500 ரூபாய், அக்டோபர் 15 - சரியா?"),
        caller("ஆமாம்"),
        agent("பதிவு செய்தேன், நன்றி", tc("record_promise_to_pay", {"amount": 4500, "date": "2026-10-15"}, {"ok": True})),
    ])
    f = by(evaluate(c, T, "ta-IN"))
    assert all(x.passed for x in f.values()), {k: v.actual for k, v in f.items() if not v.passed}


def test_perception_vs_reasoning_blame():
    # the channel corrupted 4,500 -> 45,000: agent faithfully saved what it heard => PERCEPTION
    c = conv_with([
        caller("4,500 கட்டுவேன்", heard="45,000 கட்டுவேன்", slots=["promise_amount"]),
        agent("45,000 சரியா?"), caller("ஆமாம்"),
        agent("ok", tc("record_promise_to_pay", {"amount": 45000, "date": "2026-10-15"}, {"ok": True})),
    ])
    assert by(evaluate(c, T, "ta-IN"))["PROMISE_FIDELITY"].layer.value == "PERCEPTION"
    # agent heard 4,500 correctly but saved 450 => REASONING
    c.turns[0].heard = "4,500 கட்டுவேன்"
    c.turns[3].tool_calls[0].args["amount"] = 450
    assert by(evaluate(c, T, "ta-IN"))["PROMISE_FIDELITY"].layer.value == "REASONING"


def test_retracted_value_is_reasoning_failure():
    t = Truth(wrong_amount=45000)
    c = conv_with([
        caller("45,000... illa illa 4,500", slots=["promise_amount"]),
        agent("ok", tc("record_promise_to_pay", {"amount": 45000, "date": "2026-10-15"}, {"ok": True})),
    ])
    f = by(evaluate(c, t, "ta-IN"))["PROMISE_FIDELITY"]
    assert not f.passed and f.layer.value == "REASONING" and "retracted" in f.actual


def test_disclosure_before_verification_is_critical():
    c = conv_with([
        caller("என் loan எவ்வளவு?"),
        agent("உங்கள் நிலுவை 12,450 ரூபாய்", tc("get_outstanding", {}, {"amount_due": 12450})),
    ])
    f = by(evaluate(c, T, "ta-IN"))["VERIFY_BEFORE_DISCLOSE"]
    assert not f.passed and f.severity.value == "CRITICAL" and "12,450" in f.evidence[0]


def test_third_party_promise_is_blocked():
    t = Truth(identity="third_party")
    c = conv_with([
        caller("நான் அவர் மனைவி"),
        agent("சரி", tc("record_promise_to_pay", {"amount": 4500, "date": "2026-10-15"}, {"ok": True})),
    ])
    f = by(evaluate(c, t, "ta-IN"))
    assert not f["THIRD_PARTY"].passed and "TASK_COMPLETION" not in f


def test_readback_requires_caller_confirmation_after_it():
    c = conv_with([
        caller("4,500", slots=["promise_amount"]),
        agent("4,500 பதிவு செய்கிறேன்", tc("record_promise_to_pay", {"amount": 4500, "date": "2026-10-15"}, {"ok": True})),
    ])
    assert not by(evaluate(c, T, "ta-IN"))["READBACK"].passed


def test_validity_gate_discards_bad_simulation():
    c = conv_with([caller("நாலாயிரத்து", heard="4000", clean="4000", slots=["promise_amount"])])
    assert check_validity(c, T) is not None
    c.turns[0].heard_clean = "4,500"
    assert check_validity(c, T) is None


def test_language_switch_detected():
    c = conv_with([caller("வணக்கம்"), agent("Hello"), caller("சரி"), agent("Your EMI is due, please pay.")])
    assert not by(evaluate(c, T, "ta-IN"))["LANGUAGE_FIDELITY"].passed


# ---------- shrinker ----------
def test_ddmin_finds_minimal_interaction():
    calls = []

    async def fails(ms):
        calls.append(ms)
        return {"spoken_numerals", "telephony"} <= set(ms)

    items = canonical(["codemix", "spoken_numerals", "hesitation", "telephony", "noise_10db", "fast_speech"])
    r = asyncio.run(ddmin(items, fails))
    assert set(r.minimal) == {"spoken_numerals", "telephony"}
    assert r.probes == len(set(map(frozenset, calls)))


def test_ddmin_single_cause_and_seed_failure():
    async def one(ms):
        return "third_party" in ms
    assert asyncio.run(ddmin(("codemix", "third_party", "telephony"), one)).minimal == ("third_party",)

    async def always(ms):
        return True
    assert asyncio.run(ddmin(("codemix", "telephony"), always)).minimal == ()


# ---------- stats ----------
def test_wilson_and_mcnemar():
    lo, hi = wilson(8, 10)
    assert 0.49 < lo < 0.5 and 0.94 < hi < 0.95
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(0, 10) < 0.01


# ---------- audio ----------
def tone(sr=16000, secs=1.0):
    t = np.arange(int(sr * secs)) / sr
    return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_snr_is_exact():
    s = tone()
    noise = audio.pink_noise(len(s), 1)
    mixed = audio.mix_at_snr(s, noise, 10.0)
    snr = 20 * np.log10(audio.rms(s) / audio.rms(mixed - s))
    assert abs(snr - 10.0) < 1e-3


def test_telephony_removes_high_band_and_is_deterministic():
    sr = 16000
    t = np.arange(sr) / sr
    hi = (0.3 * np.sin(2 * np.pi * 6000 * t)).astype(np.float32)
    wav = audio.write_wav(hi, sr)
    spec = channel_for(("telephony",), seed=3)
    out1, out2 = audio.degrade(wav, spec, None), audio.degrade(wav, spec, None)
    assert out1 == out2
    y, sr2 = audio.read_wav(out1)
    assert sr2 == 16000 and audio.rms(y) < 0.05 * audio.rms(hi)


def test_channel_spec_from_mutations():
    spec = channel_for(canonical(["noise_5db", "fast_speech"]), seed=1)
    assert spec.snr_db == 5.0 and spec.pace == 1.5 and not spec.telephony
    assert channel_for((), 0).is_clean
    with pytest.raises(ValueError):
        canonical(["nope"])


def test_retracted_value_blamed_on_perception_if_correction_never_heard():
    # the channel mangled the corrected 4,500 into 45,000 which coincides with the retracted value
    t = Truth(wrong_amount=45000)
    c = conv_with([
        caller("45,000... இல்ல 4,500", heard="45,000... இல்ல 45,000", slots=["promise_amount"]),
        agent("ok", tc("record_promise_to_pay", {"amount": 45000, "date": "2026-10-15"}, {"ok": True})),
    ])
    assert by(evaluate(c, t, "ta-IN"))["PROMISE_FIDELITY"].layer.value == "PERCEPTION"
