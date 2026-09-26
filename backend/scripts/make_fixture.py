"""Offline UI fixture: runs the real lab code against the scripted test double (tests/test_pipeline.py).
Saved with kind="fixture"; the UI labels it as NOT Sarvam output. Never use it for reported numbers."""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from chaoslab.lab import Lab, compare, load_seeds, summarize  # noqa: E402
from chaoslab.models import Conversation  # noqa: E402
from chaoslab.runner import Runner  # noqa: E402
from chaoslab.target import AgentConfig  # noqa: E402
from test_pipeline import FakeSarvam  # noqa: E402


async def main():
    seeds = load_seeds(ROOT / "seeds" / "emi.json")[:1]
    v1 = AgentConfig.load(ROOT / "agents" / "emi-agent-v1.json")
    a_id, b_id = "fixture-attack", "fixture-retest"
    lab = Lab(runner=Runner(FakeSarvam(readback=False), ROOT / "runs" / a_id))
    res = await lab.attack(seeds, v1, max_shrinks=3, trials=1)
    res.update(id=a_id, kind="fixture", created=time.time())
    (ROOT / "runs" / a_id / "run.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")

    v2 = AgentConfig(version="v2", system_prompt=v1.system_prompt, gate_tools_on_verification=True)
    lab2 = Lab(runner=Runner(FakeSarvam(readback=True), ROOT / "runs" / b_id))
    base = [Conversation(**c) for c in res["conversations"]]
    plan = sorted({(c.seed_id, tuple(c.mutations)) for c in base})
    by = {s.id: s for s in seeds}
    convs = await lab2.execute([(by[s], m) for s, m in plan], v2)
    out = {"id": b_id, "kind": "fixture", "created": time.time() + 1, "version": "v2", "baseline_run": a_id,
           "conversations": [c.model_dump() for c in convs], "summary": summarize(convs), "comparison": compare(base, convs)}
    (ROOT / "runs" / b_id / "run.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("attack:", res["summary"]["pass"], "clusters:", [(c["id"], c["oracle"], c["minimal"]) for c in res["clusters"]])
    print("retest:", out["comparison"]["a_pass"]["rate"], "->", out["comparison"]["b_pass"]["rate"], "fixed", len(out["comparison"]["fixed"]))

asyncio.run(main())
