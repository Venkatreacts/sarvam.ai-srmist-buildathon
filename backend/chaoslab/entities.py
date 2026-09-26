"""Deterministic entity normalisation for Indic text: native digits, Indian grouping, scripts."""
from __future__ import annotations

import re
from datetime import date, datetime

# Zero code point of each Indic digit block (digits are contiguous 0..9 in Unicode).
_DIGIT_ZEROS = [0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66]
_DIGIT_MAP = {chr(z + i): str(i) for z in _DIGIT_ZEROS for i in range(10)}

# Script blocks for the 11 Bulbul/Sarvam-105B languages.
SCRIPT_RANGES: dict[str, tuple[int, int]] = {
    "hi-IN": (0x0900, 0x097F), "mr-IN": (0x0900, 0x097F),
    "bn-IN": (0x0980, 0x09FF), "pa-IN": (0x0A00, 0x0A7F),
    "gu-IN": (0x0A80, 0x0AFF), "od-IN": (0x0B00, 0x0B7F),
    "ta-IN": (0x0B80, 0x0BFF), "te-IN": (0x0C00, 0x0C7F),
    "kn-IN": (0x0C80, 0x0CFF), "ml-IN": (0x0D00, 0x0D7F),
}

_NUM_RE = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{2,3})+(?![\d,])|\d+(?:\.\d+)?")


def to_ascii_digits(text: str) -> str:
    return "".join(_DIGIT_MAP.get(ch, ch) for ch in text)


def extract_numbers(text: str) -> list[int]:
    """All integer amounts in text. Handles 4,500 / 45,000 / 1,20,000 / native digits / ₹."""
    out: list[int] = []
    for m in _NUM_RE.finditer(to_ascii_digits(text or "")):
        tok = m.group(0).replace(",", "")
        try:
            out.append(int(float(tok)))
        except ValueError:
            continue
    return out


def mentions_amount(text: str, amount: int) -> bool:
    return amount in extract_numbers(text)


_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y")


def normalize_date(value: str | None) -> str | None:
    """Tool-call date argument -> ISO date, or None if it is not a date we can read unambiguously."""
    if not value:
        return None
    v = to_ascii_digits(str(value)).strip()
    v = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", v)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def script_share(text: str, lang: str) -> float | None:
    """Fraction of letters in `text` that belong to `lang`'s native script. None for English/unknown."""
    rng = SCRIPT_RANGES.get(lang)
    if rng is None:
        return None
    letters = [c for c in text or "" if c.isalpha()]
    if not letters:
        return None
    native = sum(1 for c in letters if rng[0] <= ord(c) <= rng[1])
    return native / len(letters)


def iso(d: date) -> str:
    return d.isoformat()


_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100000, "lakhs": 100000, "lac": 100000,
           "crore": 10000000, "crores": 10000000, "million": 1000000}


def english_number_words(text: str) -> list[int]:
    """'four thousand five hundred' -> 4500, 'twelve thousand four hundred and fifty' -> 12450.
    Used on Saaras translate-mode output, which renders spoken Indic numbers as English words."""
    toks = re.findall(r"[a-z]+|\d[\d,]*", (text or "").lower().replace("-", " "))
    out, total, cur, active = [], 0, 0, False
    for t in toks + ["<end>"]:
        if t in _UNITS or t in _TENS:
            cur += _UNITS.get(t, 0) + _TENS.get(t, 0)
            active = True
        elif t in _SCALES and active:
            if _SCALES[t] == 100:
                cur *= 100
            else:
                total += cur * _SCALES[t]
                cur = 0
        elif t == "and" and active:
            continue
        else:
            if active:
                out.append(total + cur)
            total, cur, active = 0, 0, False
    return out


def amounts_in(*texts: str | None) -> set[int]:
    """Every amount mentioned in any of the texts, as digits (incl. native digits) or English number words."""
    found: set[int] = set()
    for t in texts:
        if t:
            found.update(extract_numbers(t))
            found.update(english_number_words(t))
    return found
