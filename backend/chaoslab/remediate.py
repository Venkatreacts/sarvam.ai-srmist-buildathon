"""Turn clusters of minimal repros into a concrete next agent version.

Two kinds of fix, chosen by the layer the failure was blamed on:
  structural  POLICY failures on account data -> enforce in the tool backend (a prompt is not a control)
  prompt      REASONING / LANGUAGE / TASK / PERCEPTION-recovery -> a minimal, reviewable prompt patch
The patch is written by sarvam-105b from the evidence, then applied as an append-only diff so a human
can review exactly what changed before the same suite is re-run.
"""
from __future__ import annotations

from dataclasses import replace

from .oracles import FIXES
from .models import Layer
from .sarvam import Sarvam, _parse_json
from .target import AgentConfig

STRUCTURAL = {"VERIFY_BEFORE_DISCLOSE", "THIRD_PARTY"}   # enforce in the tool backend
PIN_LANGUAGE = {"LANGUAGE_FIDELITY"}                     # enforce in the agent config


async def propose(cfg: AgentConfig, clusters: list[dict], examples: list[dict], client: Sarvam) -> dict:
    structural = any(c["oracle"] in STRUCTURAL for c in clusters)
    pin = any(c["oracle"] in PIN_LANGUAGE for c in clusters)
    guidance = sorted({FIXES.get((c["oracle"], Layer(c["layer"])), "") for c in clusters if c.get("layer")} - {""})
    evidence = "\n\n".join(
        f"[{e['oracle']} / {e['layer']}] minimal trigger: {', '.join(e['minimal']) or 'none'}\n"
        f"expected: {e['expected']}\nactual: {e['actual']}\nevidence:\n- " + "\n- ".join(e["evidence"][:4])
        for e in examples)
    prompt = (
        "You maintain the system prompt of a production voice agent. Below is its current prompt and "
        "evidence-backed failures found by an adversarial test harness, each reduced to its minimal trigger.\n"
        "Write the SMALLEST set of new rules (bullet lines) to append to the prompt that would prevent these "
        "failures. Rules must be concrete and behavioural, apply to every language, and must not remove any "
        "existing behaviour. Do not mention tests. Max 6 bullets.\n\n"
        f"CURRENT PROMPT:\n{cfg.system_prompt}\n\nFIX DIRECTIONS:\n- " + "\n- ".join(guidance) +
        f"\n\nFAILURES:\n{evidence}\n\n"
        "Return a JSON object: 'rules' is an array of the new rules as complete sentences, and 'rationale' "
        "is an array saying which failure each rule addresses.")
    schema = {"type": "json_schema", "json_schema": {"name": "prompt_patch", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "required": ["rules", "rationale"],
        "properties": {"rules": {"type": "array", "items": {"type": "string"}},
                       "rationale": {"type": "array", "items": {"type": "string"}}}}}}
    raw = await client.chat([{"role": "user", "content": prompt}], model="sarvam-105b", response_format=schema,
                            reasoning_effort=None, max_tokens=2000)
    msg = raw["choices"][0]["message"]
    out = _parse_json(msg.get("content") or msg.get("reasoning_content") or "")
    # drop echoed placeholders or fragments; a rule must be a real sentence
    rules = [r.strip().lstrip("-• ").strip() for r in out.get("rules", []) if r and len(r.strip()) > 12][:6]
    if not rules:
        raise ValueError("model returned no usable rules")
    patch = "\n".join(f"- {r}" for r in rules)
    new_version = f"v{int(cfg.version.lstrip('v') or 1) + 1}"
    new_cfg = replace(cfg, version=new_version,
                      system_prompt=cfg.system_prompt + ("\n\nRules:\n" + patch.replace("{", "{{").replace("}", "}}") if rules else ""),
                      gate_tools_on_verification=cfg.gate_tools_on_verification or structural,
                      pin_language=cfg.pin_language or pin)
    return {"config": new_cfg, "rules": rules, "rationale": out.get("rationale", {}),
            "structural": {"gate_tools_on_verification": new_cfg.gate_tools_on_verification and not cfg.gate_tools_on_verification,
                           "pin_language": new_cfg.pin_language and not cfg.pin_language}}
