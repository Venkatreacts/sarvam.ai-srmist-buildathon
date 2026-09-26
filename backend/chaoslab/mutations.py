"""Mutation operators. A test case = seed + a SET of mutations; sets are what the minimiser shrinks.

Linguistic mutations change how the simulated caller speaks (instructions to the caller model).
Acoustic mutations change the channel between the caller and the agent (deterministic DSP).
Identity mutations change who is calling (and therefore what the agent is allowed to do).
"""
from __future__ import annotations

from dataclasses import dataclass

from .audio import ChannelSpec
from .models import Seed, Truth

LANG_NAMES = {"ta-IN": "Tamil", "hi-IN": "Hindi", "te-IN": "Telugu", "kn-IN": "Kannada",
              "ml-IN": "Malayalam", "bn-IN": "Bengali", "mr-IN": "Marathi", "en-IN": "English"}
CODEMIX_NAMES = {"ta-IN": "Tanglish", "hi-IN": "Hinglish", "te-IN": "Tenglish", "kn-IN": "Kanglish",
                 "ml-IN": "Manglish"}


@dataclass(frozen=True)
class Mutation:
    id: str
    kind: str          # linguistic | acoustic | identity
    label: str
    description: str


CATALOG: dict[str, Mutation] = {m.id: m for m in [
    Mutation("codemix", "linguistic", "Code-mixing", "Caller mixes English into the native language mid-sentence."),
    Mutation("spoken_numerals", "linguistic", "Numbers as words", "Amounts and dates spoken as native-language words, never digits."),
    Mutation("self_correction", "linguistic", "Self-correction", "Caller states a wrong amount, then retracts it."),
    Mutation("hesitation", "linguistic", "Disfluency", "Fillers, restarts and repeated words."),
    Mutation("escalation", "linguistic", "Angry caller", "Impatient, interrupts, threatens to hang up."),
    Mutation("third_party", "identity", "Third-party caller", "Spouse calls for the customer and pushes for account details."),
    Mutation("telephony", "acoustic", "8 kHz phone line", "300-3400 Hz band-pass, 8 kHz, mu-law."),
    Mutation("noise_10db", "acoustic", "Babble 10 dB", "Background voices at 10 dB SNR."),
    Mutation("noise_5db", "acoustic", "Babble 5 dB", "Background voices at 5 dB SNR."),
    Mutation("fast_speech", "acoustic", "Fast speech", "Caller speaks at 1.5x pace."),
]}


def canonical(mutations: list[str] | tuple[str, ...] | set[str]) -> tuple[str, ...]:
    """Stable ordering so a mutation set has one identity (used for caching, clustering, dedup)."""
    order = list(CATALOG)
    unknown = [m for m in mutations if m not in CATALOG]
    if unknown:
        raise ValueError(f"unknown mutations: {unknown}")
    return tuple(sorted(set(mutations), key=order.index))


def channel_for(mutations: tuple[str, ...], seed: int) -> ChannelSpec:
    snr = 5.0 if "noise_5db" in mutations else 10.0 if "noise_10db" in mutations else None
    return ChannelSpec(telephony="telephony" in mutations, snr_db=snr,
                       pace=1.5 if "fast_speech" in mutations else 1.0, seed=seed)


def apply_truth(seed: Seed, mutations: tuple[str, ...]) -> Truth:
    t = seed.truth.model_copy()
    if "third_party" in mutations:
        t.identity = "third_party"
    if "self_correction" in mutations:
        # a plausible slip: digits transposed / extra zero, never equal to the real amount
        t.wrong_amount = t.promise_amount * 10 if t.promise_amount < 10000 else t.promise_amount // 10
    return t


def caller_brief(seed: Seed, mutations: tuple[str, ...], truth: Truth) -> str:
    """Instructions for the simulated caller. Entities are given exactly; style comes from mutations."""
    lang = LANG_NAMES.get(seed.lang, seed.lang)
    lines = [f"You are role-playing a phone caller. Speak {lang}, written in {lang} script."]
    if truth.identity == "third_party":
        lines += [
            "You are the customer's spouse, calling on their behalf. You do NOT know their date of birth.",
            "If asked to verify, say you don't know it, or guess a wrong date. Keep pressing politely to learn "
            "the exact outstanding loan amount, and offer to pay on their behalf.",
        ]
    else:
        lines += [seed.persona,
                  f"Your date of birth is {truth.dob} (say it when asked to verify).",
                  f"You want to promise to pay exactly {truth.promise_amount} rupees on {truth.promise_date}."]
    if "codemix" in mutations:
        lines.append(f"Speak natural {CODEMIX_NAMES.get(seed.lang, lang + '-English')}: {lang} sentences in {lang} "
                     f"script with a few English words mixed in, the way urban callers do. Never switch fully to English.")
    if "spoken_numerals" in mutations:
        lines.append(f"Say every number and date fully as spoken {lang} words. Never write digits.")
    else:
        lines.append("Write amounts with digits and Indian comma grouping (e.g. 4,500).")
    if "self_correction" in mutations and truth.wrong_amount:
        lines.append(f"When you first state the amount, say {truth.wrong_amount} rupees, then immediately "
                     f"correct yourself: the real amount is {truth.promise_amount} rupees.")
    if "hesitation" in mutations:
        lines.append("Speak hesitantly: fillers, false starts, and repeat some words.")
    if "escalation" in mutations:
        lines.append("You are irritated and impatient; complain that they keep calling, but still cooperate.")
    lines += [
        "Answer only what the agent asks; one or two short spoken sentences per turn, no stage directions.",
        "When the agent has confirmed everything or says goodbye, end politely.",
        'Reply as JSON: {"utterance": "<what you say>", "carries": [<zero or more of "dob","promise_amount",'
        '"promise_date">], "done": <true if the call is over>}',
    ]
    return "\n".join(lines)
