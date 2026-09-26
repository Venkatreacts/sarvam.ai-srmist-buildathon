"""FastAPI surface for the UI. Long jobs run in the background and stream events over SSE."""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .lab import Lab, compare, load_seeds, summarize, to_sarvam_tests
from .models import Conversation
from .mutations import CATALOG
from .remediate import propose
from .runner import Runner
from .sarvam import Sarvam, SarvamError
from .target import AgentConfig

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT.parent / ".env")
AGENTS, SEEDS = ROOT / "agents", ROOT / "seeds" / "emi.json"
# writable scratch for new runs (on Vercel only /tmp is writable); recorded = bundled real runs, read-only
RUNS = Path(os.environ.get("CHAOSLAB_RUNS_DIR", ROOT / "runs"))
RECORDED = ROOT / "recorded"
RUNS.mkdir(parents=True, exist_ok=True)
RUN_DIRS = [RUNS, RECORDED]

app = FastAPI(title="Indic Chaos Lab")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])



def client() -> Sarvam:
    try:
        return Sarvam(cache_dir=os.environ.get("CHAOSLAB_CACHE_DIR", ROOT / ".cache"))
    except SarvamError as e:
        raise HTTPException(503, str(e))


def agent_cfg(version: str) -> AgentConfig:
    for d in (AGENTS, RUNS / "agents"):
        p = d / f"emi-agent-{version}.json"
        if p.exists():
            return AgentConfig.load(p)
    raise HTTPException(404, f"no agent version {version}")


def run_path(run_id: str) -> Path:
    return RUNS / run_id / "run.json"


def _run_file(folder: Path) -> Path | None:
    for name in ("run.json", "run.json.gz"):  # recorded runs ship gzipped to keep the deployment small
        if (folder / name).is_file():
            return folder / name
    return None


def _read_run(p: Path) -> dict:
    raw = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
    return json.loads(raw.decode("utf-8"))


def load_run(run_id: str) -> dict:
    for d in RUN_DIRS:
        p = _run_file(d / run_id)
        if p:
            r = _read_run(p)
            r["source"] = "recorded" if d == RECORDED else "live"
            return r
    raise HTTPException(404, "run not found")


@app.get("/runs/{run_id}/{path:path}")
def run_file(run_id: str, path: str):
    for d in RUN_DIRS:
        base = (d / run_id).resolve()
        f = (base / path).resolve()
        if f.is_file() and base in f.parents:
            return FileResponse(f)
    raise HTTPException(404, "file not found")


@app.get("/api/status")
def status():
    return {"sarvam_key": bool(os.environ.get("SARVAM_API_KEY")),
            "agents": sorted({p.stem.replace("emi-agent-", "") for d in (AGENTS, RUNS / "agents")
                              for p in d.glob("emi-agent-*.json")}),
            "live": os.environ.get("CHAOSLAB_LIVE", "1") == "1",
            "seeds": json.loads(SEEDS.read_text(encoding="utf-8")),
            "mutations": {k: asdict(v) for k, v in CATALOG.items()}}


@app.get("/api/agents/{version}")
def get_agent(version: str):
    return asdict(agent_cfg(version))


@app.get("/api/runs")
def list_runs():
    out = []
    for d in RUN_DIRS:
        for p in [f for f in (_run_file(x) for x in d.glob("*") if x.is_dir()) if f]:
            r = _read_run(p)
            out.append({**{k: r.get(k) for k in ("id", "kind", "version", "created", "summary", "baseline_run")},
                        "source": "recorded" if d == RECORDED else "live"})
    return sorted(out, key=lambda r: r.get("created") or 0, reverse=True)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return load_run(run_id)


class AttackReq(BaseModel):
    version: str = "v1"
    seeds: list[str] | None = None
    max_shrinks: int = 3
    trials: int = 2  # a subset reproduces only if it fails in every trial


class RetestReq(BaseModel):
    baseline_run: str
    version: str
    config: dict | None = None      # inline agent config (from /api/remediate), so no shared disk is needed
    baseline: dict | None = None    # inline baseline run, same reason


def _stream(kind: str, factory) -> StreamingResponse:
    """Run a job inside the request and stream NDJSON events; the last line carries the whole run.
    No background tasks and no shared disk needed, so it behaves the same locally and on serverless."""
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(ev):
        await queue.put(ev)

    async def main():
        c = client()
        try:
            lab = Lab(runner=Runner(c, RUNS / run_id), on_event=on_event)
            result = await factory(lab)
            result.update(id=run_id, kind=kind, created=time.time())
            try:
                run_path(run_id).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
            await queue.put({"kind": "done", "run_id": run_id, "run": result})
        except SarvamError as e:
            msg = {402: "Sarvam credits exhausted (402). Top up the Sarvam wallet, then run again.",
                   401: "Sarvam rejected the API key (401).", 403: "Sarvam denied access (403)."}.get(e.status, str(e))
            await queue.put({"kind": "error", "message": msg})
        except Exception as e:
            await queue.put({"kind": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            await c.aclose()

    async def gen():
        task = asyncio.create_task(main())
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=10)
            except asyncio.TimeoutError:
                yield json.dumps({"kind": "heartbeat"}) + "\n"
                continue
            yield json.dumps(ev, ensure_ascii=False) + "\n"
            if ev["kind"] in ("done", "error"):
                break
        await task
    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})


@app.post("/api/attack")
async def attack(req: AttackReq):
    cfg = agent_cfg(req.version)
    seeds = [s for s in load_seeds(SEEDS) if not req.seeds or s.id in req.seeds]
    client()  # fail fast without a key

    async def go(lab: Lab):
        return await lab.attack(seeds, cfg, max_shrinks=req.max_shrinks, trials=req.trials)
    return _stream("attack", go)


@app.post("/api/retest")
async def retest(req: RetestReq):
    base = req.baseline or load_run(req.baseline_run)
    cfg = AgentConfig(**req.config) if req.config else agent_cfg(req.version)
    seeds = {s.id: s for s in load_seeds(SEEDS)}
    base_convs = [Conversation(**c) for c in base["conversations"]]
    plan = sorted({(c.seed_id, tuple(c.mutations)) for c in base_convs})
    reg = sorted({(t["seed_id"], tuple(t["mutations"])) for t in base.get("regression_suite", [])} - set(plan))
    client()

    async def go(lab: Lab):
        await lab.emit("plan", cases=[f"{s}:{'+'.join(m) or 'baseline'}" for s, m in plan + reg], version=cfg.version)
        convs = await lab.execute([(seeds[s], m) for s, m in plan + reg], cfg)
        main = [c for c in convs if (c.seed_id, tuple(c.mutations)) in set(plan)]
        return {"version": cfg.version, "baseline_run": req.baseline_run,
                "conversations": [c.model_dump() for c in convs], "summary": summarize(main),
                "comparison": compare(base_convs, main),
                "regression_suite_results": [{"case_id": c.case_id, "passed": c.passed} for c in convs
                                             if (c.seed_id, tuple(c.mutations)) in set(reg)]}
    return _stream("retest", go)


class RemediateReq(BaseModel):
    run: dict | None = None      # inline run, for runs that only exist in the browser (serverless)
    config: dict | None = None   # inline config of the version that produced the run


@app.post("/api/remediate/{run_id}")
async def remediate(run_id: str, req: RemediateReq | None = None):
    run = (req.run if req and req.run else None) or load_run(run_id)
    cfg = AgentConfig(**req.config) if req and req.config else agent_cfg(run["version"])
    convs = {c["case_id"]: c for c in run["conversations"]}
    if not run.get("clusters"):
        # a retest run has no minimisation; group its failures by (oracle, layer) with the observed mutations
        groups: dict = {}
        for c in run["conversations"]:
            for f in c["findings"]:
                if not f["passed"] and f["oracle"] != "HARNESS":
                    groups.setdefault((f["oracle"], f["layer"]), []).append(c)
        run = {**run, "clusters": [{"id": f"R{i + 1}", "oracle": o, "layer": l, "minimal": cs[0]["mutations"],
                                    "members": [x["case_id"] for x in cs], "seeds": sorted({x["seed_id"] for x in cs})}
                                   for i, ((o, l), cs) in enumerate(groups.items())]}
    examples = []
    for cl in run["clusters"]:
        conv = convs.get(cl["members"][0])
        f = next((f for f in (conv or {}).get("findings", []) if f["oracle"] == cl["oracle"] and not f["passed"]), None)
        if f:
            examples.append({**f, "minimal": cl["minimal"], "layer": cl["layer"]})
    if not run["clusters"]:
        raise HTTPException(400, "no failure clusters to remediate")
    c = client()
    try:
        out = await propose(cfg, run["clusters"], examples, c)
    except (ValueError, SarvamError) as e:
        raise HTTPException(502, f"remediation failed: {e}")
    finally:
        await c.aclose()
    new = out["config"]
    body = json.dumps(asdict(new), ensure_ascii=False, indent=2)
    for d in (AGENTS, RUNS / "agents"):
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"emi-agent-{new.version}.json").write_text(body, encoding="utf-8")
            break
        except OSError:
            continue
    return {"version": new.version, "rules": out["rules"], "rationale": out["rationale"],
            "structural": out["structural"], "config": asdict(new)}


@app.get("/api/runs/{run_id}/sarvam-tests")
def export(run_id: str):
    run = load_run(run_id)
    return to_sarvam_tests(run.get("regression_suite", []), load_seeds(SEEDS))
