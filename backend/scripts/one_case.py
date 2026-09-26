"""Run a single live case and print the trace (dev tool)."""
import asyncio, sys, time
from pathlib import Path
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); load_dotenv(ROOT.parent / ".env")
from chaoslab.lab import load_seeds
from chaoslab.runner import Runner
from chaoslab.sarvam import Sarvam
from chaoslab.target import AgentConfig

async def main(seed_id, muts, version):
    c = Sarvam(cache_dir=ROOT / ".cache")
    seed = next(s for s in load_seeds(ROOT / "seeds" / "emi.json") if s.id == seed_id)
    t0 = time.time()
    conv = await Runner(c, ROOT / "runs" / "_dev").run_case(seed, tuple(m for m in muts.split(",") if m), AgentConfig.load(ROOT / "agents" / f"emi-agent-{version}.json"))
    await c.aclose()
    for t in conv.turns:
        if t.role == "caller": print(f"CALLER said : {t.said}\n       heard: {t.heard}  slots={t.entity_slots}")
        else: print(f"AGENT {int(t.latency_ms or 0)}ms: {t.text}  tools={[(x.name, x.args, x.result) for x in t.tool_calls]}")
    print("ERROR:", conv.error)
    for f in conv.findings: print(("PASS " if f.passed else "FAIL ") + f.oracle, f.layer, "|", f.actual)
    print(f"{time.time()-t0:.0f}s")

asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "", sys.argv[3] if len(sys.argv) > 3 else "v1"))
