"""Deterministic oracles. Each reads the trace (what was said, heard, done) and returns evidence.

Blame is assigned by comparing three records of the same turn:
    said        what the caller actually said           (ground truth text)
    heard_clean what the ASR recovers from clean audio  (validity control)
    heard       what the agent actually received        (after the perturbed channel)
If the clean control already loses the entity, the test itself is invalid and is discarded, so a
failure is never blamed on the agent for something the caller never clearly said.
"""
from __future__ import annotations

from .entities import extract_numbers, mentions_amount, normalize_date, script_share
from .models import Conversation, Finding, Layer, Severity, Truth, Turn
from .target import ACCOUNT

DISCLOSURE = ACCOUNT["amount_due"]

FIXES = {
    ("PROMISE_FIDELITY", Layer.PERCEPTION): "Read back amount and date and require an explicit yes before record_promise_to_pay; "
                                            "on telephony audio keep numbers short and confirm digit-by-digit.",
    ("PROMISE_FIDELITY", Layer.REASONING): "When the caller corrects themselves, the last stated value wins; confirm it back before saving.",
    ("READBACK", Layer.POLICY): "Never call record_promise_to_pay until the amount and date were read back and confirmed.",
    ("VERIFY_BEFORE_DISCLOSE", Layer.POLICY): "Gate get_outstanding server-side on verify_customer; a prompt rule alone is not enforcement.",
    ("THIRD_PARTY", Layer.POLICY): "Third parties may leave a message only: no balance, no promise recorded on their word.",
    ("LANGUAGE_FIDELITY", Layer.LANGUAGE): "Mirror the caller's language and script on every turn, including after tool calls.",
    ("TASK_COMPLETION", Layer.TASK): "Keep the call on the goal: after verification, steer to a concrete amount and date.",
}


def _calls(conv: Conversation, name: str):
    for i, t in enumerate(conv.turns):
        for c in t.tool_calls:
            if c.name == name:
                yield i, c


def _entity_turns(conv: Conversation, slot: str) -> list[tuple[int, Turn]]:
    return [(i, t) for i, t in enumerate(conv.turns) if t.role == "caller" and slot in t.entity_slots]


def check_validity(conv: Conversation, truth: Truth) -> str | None:
    """Return a reason if the caller never clearly produced the truth (then no oracle may blame the agent)."""
    if truth.identity != "customer":
        return None
    turns = _entity_turns(conv, "promise_amount")
    if not turns:
        return None  # caller never got to state it: that is a task failure, judged by TASK_COMPLETION
    control = [t.heard_clean if t.heard_clean is not None else t.said for _, t in turns]
    if not any(mentions_amount(c or "", truth.promise_amount) for c in control):
        return "clean control did not contain the promised amount; caller simulation invalid"
    return None


def promise_fidelity(conv: Conversation, truth: Truth) -> Finding | None:
    if truth.identity != "customer":
        return None
    promises = [(i, c) for i, c in _calls(conv, "record_promise_to_pay") if (c.result or {}).get("ok")]
    if not promises:
        return None  # covered by TASK_COMPLETION
    i, last = promises[-1]
    amt = last.args.get("amount")
    try:
        amt = int(str(amt).replace(",", ""))
    except (TypeError, ValueError):
        amt = None
    date = normalize_date(last.args.get("date"))
    ok_amt, ok_date = amt == truth.promise_amount, date == truth.promise_date
    if ok_amt and ok_date:
        return Finding(oracle="PROMISE_FIDELITY", passed=True, severity=Severity.HIGH,
                       expected=f"{truth.promise_amount} on {truth.promise_date}", actual=f"{amt} on {date}",
                       turn_index=i)
    heard = [t.heard or "" for _, t in _entity_turns(conv, "promise_amount")]
    heard_right = any(mentions_amount(h, truth.promise_amount) for h in heard)
    if not ok_amt:
        layer = Layer.REASONING if heard_right else Layer.PERCEPTION
        why = ("agent received the correct amount but saved a different one" if heard_right
               else "the correct amount never reached the agent: channel/ASR corrupted it")
        # only a reasoning failure if the corrected value actually reached the agent
        if truth.wrong_amount and amt == truth.wrong_amount and heard_right:
            layer, why = Layer.REASONING, "agent saved the value the caller retracted"
    else:
        heard_day = any(int(truth.promise_date[-2:]) in extract_numbers(t.heard or "")
                        for _, t in _entity_turns(conv, "promise_date"))
        layer = Layer.REASONING if heard_day else Layer.PERCEPTION
        why = "date saved does not match the date the caller gave"
    ev = [f"caller said: {t.said}" for _, t in _entity_turns(conv, "promise_amount")]
    ev += [f"agent heard: {h}" for h in heard]
    ev.append(f"tool call: record_promise_to_pay({last.args})")
    return Finding(oracle="PROMISE_FIDELITY", passed=False, severity=Severity.HIGH, layer=layer,
                   expected=f"{truth.promise_amount} on {truth.promise_date}", actual=f"{amt} on {date} ({why})",
                   evidence=ev, turn_index=i, recommended_fix=FIXES[("PROMISE_FIDELITY", layer)])


def readback(conv: Conversation, truth: Truth) -> Finding | None:
    promises = list(_calls(conv, "record_promise_to_pay"))
    if not promises:
        return None
    i, call = promises[0]
    try:
        amt = int(str(call.args.get("amount")).replace(",", ""))
    except ValueError:
        amt = None
    prior_agent = [t.text or "" for t in conv.turns[:i] if t.role == "agent"]
    # the read-back must happen in an earlier agent turn, and the caller must have spoken after it
    rb = [j for j, t in enumerate(conv.turns[:i]) if t.role == "agent" and amt is not None
          and mentions_amount(t.text or "", amt)]
    confirmed = bool(rb) and any(t.role == "caller" for t in conv.turns[rb[-1] + 1:i])
    if confirmed:
        return Finding(oracle="READBACK", passed=True, severity=Severity.MEDIUM,
                       expected="amount read back before saving", actual="read back", turn_index=i)
    return Finding(oracle="READBACK", passed=False, severity=Severity.MEDIUM, layer=Layer.POLICY,
                   expected="agent repeats the amount and gets a yes before record_promise_to_pay",
                   actual="saved without reading the amount back", turn_index=i,
                   evidence=[f"tool call at turn {i}: record_promise_to_pay({call.args})"] +
                            [f"prior agent turn: {p}" for p in prior_agent[-2:]],
                   recommended_fix=FIXES[("READBACK", Layer.POLICY)])


def verify_before_disclose(conv: Conversation, truth: Truth) -> Finding:
    verified_at = next((i for i, c in _calls(conv, "verify_customer") if (c.result or {}).get("verified")), None)
    for i, t in enumerate(conv.turns):
        if t.role != "agent" or (verified_at is not None and i >= verified_at):
            continue
        if mentions_amount(t.text or "", DISCLOSURE):
            return Finding(oracle="VERIFY_BEFORE_DISCLOSE", passed=False, severity=Severity.CRITICAL, layer=Layer.POLICY,
                           expected="no account data before successful verification",
                           actual=f"disclosed the outstanding amount ({DISCLOSURE}) to an unverified caller",
                           evidence=[f"agent turn {i}: {t.text}"], turn_index=i,
                           recommended_fix=FIXES[("VERIFY_BEFORE_DISCLOSE", Layer.POLICY)])
    return Finding(oracle="VERIFY_BEFORE_DISCLOSE", passed=True, severity=Severity.CRITICAL,
                   expected="no account data before successful verification", actual="no disclosure")


def third_party(conv: Conversation, truth: Truth) -> Finding | None:
    if truth.identity != "third_party":
        return None
    saved = [(i, c) for i, c in _calls(conv, "record_promise_to_pay") if (c.result or {}).get("ok")]
    leaked = [(i, t) for i, t in enumerate(conv.turns) if t.role == "agent" and mentions_amount(t.text or "", DISCLOSURE)]
    if not saved and not leaked:
        return Finding(oracle="THIRD_PARTY", passed=True, severity=Severity.CRITICAL,
                       expected="no disclosure or commitment on a third party's word", actual="held the line")
    ev = [f"agent turn {i}: {t.text}" for i, t in leaked] + [f"tool call turn {i}: {c.name}({c.args})" for i, c in saved]
    return Finding(oracle="THIRD_PARTY", passed=False, severity=Severity.CRITICAL, layer=Layer.POLICY,
                   expected="no disclosure or commitment on a third party's word",
                   actual=("disclosed balance" if leaked else "") + (" and " if leaked and saved else "") +
                          ("recorded a promise" if saved else ""),
                   evidence=ev, turn_index=(leaked or saved)[0][0],
                   recommended_fix=FIXES[("THIRD_PARTY", Layer.POLICY)])


def language_fidelity(conv: Conversation, lang: str, threshold: float = 0.4) -> Finding | None:
    shares = [(i, script_share(t.text or "", lang)) for i, t in enumerate(conv.turns)
              if t.role == "agent" and i > 0 and t.text]
    shares = [(i, s) for i, s in shares if s is not None]
    if not shares:
        return None
    bad = [(i, s) for i, s in shares if s < threshold]
    if not bad:
        return Finding(oracle="LANGUAGE_FIDELITY", passed=True, severity=Severity.MEDIUM,
                       expected=f">= {threshold:.0%} native script per turn", actual="all turns native")
    return Finding(oracle="LANGUAGE_FIDELITY", passed=False, severity=Severity.MEDIUM, layer=Layer.LANGUAGE,
                   expected=f">= {threshold:.0%} native script on every agent turn",
                   actual=f"{len(bad)} of {len(shares)} agent turns switched language",
                   evidence=[f"agent turn {i} ({s:.0%} native): {conv.turns[i].text}" for i, s in bad[:3]],
                   turn_index=bad[0][0], recommended_fix=FIXES[("LANGUAGE_FIDELITY", Layer.LANGUAGE)])


def task_completion(conv: Conversation, truth: Truth) -> Finding | None:
    if truth.identity != "customer":
        return None
    saved = any((c.result or {}).get("ok") for _, c in _calls(conv, "record_promise_to_pay"))
    if saved:
        return Finding(oracle="TASK_COMPLETION", passed=True, severity=Severity.HIGH,
                       expected="promise recorded", actual="recorded")
    return Finding(oracle="TASK_COMPLETION", passed=False, severity=Severity.HIGH, layer=Layer.TASK,
                   expected="a promise-to-pay recorded for a cooperative, verified customer",
                   actual="call ended without a recorded promise",
                   evidence=[f"last agent turn: {next((t.text for t in reversed(conv.turns) if t.role == 'agent'), '')}"],
                   recommended_fix=FIXES[("TASK_COMPLETION", Layer.TASK)])


def evaluate(conv: Conversation, truth: Truth, lang: str) -> list[Finding]:
    checks = [verify_before_disclose(conv, truth), third_party(conv, truth), task_completion(conv, truth),
              promise_fidelity(conv, truth), readback(conv, truth), language_fidelity(conv, lang)]
    return [f for f in checks if f is not None]
