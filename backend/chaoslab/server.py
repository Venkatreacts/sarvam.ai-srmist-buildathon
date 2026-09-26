"""FastAPI surface for the UI. Long jobs run in the background and stream events over SSE."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .lab import Lab, compare, load_seeds, to_sarvam_tests
from .models import Conversation
from .mutations import CATALOG
from .remediate import propose
from .runner import Runner
from .sarvam import Sarvam, SarvamError
from .target import AgentConfig

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT.parent / ".env")
AGENTS, RUNS, SEEDS = ROOT / "agents", ROOT / "runs", ROOT / "seeds" / "emi.json"
RUNS.mkdir(exist_ok=True)

app = FastAPI(title="Indic Chaos Lab")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/runs", StaticFiles(directory=RUNS), name="runs")

JOBS: dict[str, dict] = {}


def client() -> Sarvam:
    try:
        return Sarvam(cache_dir=ROOT / ".cache")
    except SarvamError as e:
        raise HTTPException(503, str(e))


def agent_cfg(version: str) -> AgentConfig:
    p = AGENTS / f"emi-agent-{version}.json"
    if not p.exists():
        raise HTTPException(404, f"no agent version {version}")
    return AgentConfig.load(p)


def run_path(run_id: str) -> Path:
    return RUNS / run_id / "run.json"


def load_run(run_id: str) -> dict:
    p = run_path(run_id)
    if not p.exists():
        raise HTTPException(404, "run not found")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/status")
def status():
    return {"sarvam_key": bool(os.environ.get("SARVAM_API_KEY")),
            "agents": sorted(p.stem.replace("emi-agent-", "") for p in AGENTS.glob("emi-agent-*.json")),
            "seeds": json.loads(SEEDS.read_text(encoding="utf-8")),
            "mutations": {k: asdict(v) for k, v in CATALOG.items()}}


@app.get("/api/agents/{version}")
def get_agent(version: str):
    return asdict(agent_cfg(version))


@app.get("/api/runs")
def list_runs():
    out = []
    for p in sorted(RUNS.glob("*/run.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        r = json.loads(p.read_text(encoding="utf-8"))
        out.append({k: r.get(k) for k in ("id", "kind", "version", "created", "summary", "baseline_run")})
    return out


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


def _start(kind: str, coro_factory) -> str:
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    (RUNS / run_id).mkdir(parents=True)
    job = {"id": run_id, "kind": kind, "status": "running", "events": [], "queue": asyncio.Queue()}
    JOBS[run_id] = job

    async def on_event(ev):
        job["events"].append(ev)
        await job["queue"].put(ev)

    async def main():
        c = client()
        try:
            lab = Lab(runner=Runner(c, RUNS / run_id), on_event=on_event)
            result = await coro_factory(lab, c)
            result.update(id=run_id, kind=kind, created=time.time())
            run_path(run_id).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            job["status"] = "done"
            await on_event({"kind": "done", "run_id": run_id})
        except Exception as e:
            job["status"] = "error"
            await on_event({"kind": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            await c.aclose()

    asyncio.get_running_loop().create_task(main())
    return run_id


@app.post("/api/attack")
async def attack(req: AttackReq):
    cfg = agent_cfg(req.version)
    seeds = [s for s in load_seeds(SEEDS) if not req.seeds or s.id in req.seeds]
    client()  # fail fast without a key

    async def go(lab: Lab, _c):
        return await lab.attack(seeds, cfg, max_shrinks=req.max_shrinks, trials=req.trials)
    return {"run_id": _start("attack", go)}


@app.post("/api/retest")
async def retest(req: RetestReq):
    base = load_run(req.baseline_run)
    cfg = agent_cfg(req.version)
    seeds = {s.id: s for s in load_seeds(SEEDS)}
    base_convs = [Conversation(**c) for c in base["conversations"]]
    plan = sorted({(c.seed_id, tuple(c.mutations)) for c in base_convs})
    reg = sorted({(t["seed_id"], tuple(t["mutations"])) for t in base.get("regression_suite", [])} - set(plan))
    client()

    async def go(lab: Lab, _c):
        await lab.emit("plan", cases=[f"{s}:{'+'.join(m) or 'baseline'}" for s, m in plan + reg], version=cfg.version)
        convs = await lab.execute([(seeds[s], m) for s, m in plan + reg], cfg)
        main = [c for c in convs if (c.seed_id, tuple(c.mutations)) in set(plan)]
        from .lab import summarize
        return {"version": cfg.version, "baseline_run": req.baseline_run,
                "conversations": [c.model_dump() for c in convs], "summary": summarize(main),
                "comparison": compare(base_convs, main),
                "regression_suite_results": [{"case_id": c.case_id, "passed": c.passed} for c in convs
                                             if (c.seed_id, tuple(c.mutations)) in set(reg)]}
    return {"run_id": _start("retest", go)}


@app.post("/api/remediate/{run_id}")
async def remediate(run_id: str):
    run = load_run(run_id)
    cfg = agent_cfg(run["version"])
    convs = {c["case_id"]: c for c in run["conversations"]}
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
    finally:
        await c.aclose()
    new = out["config"]
    (AGENTS / f"emi-agent-{new.version}.json").write_text(json.dumps(asdict(new), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"version": new.version, "rules": out["rules"], "rationale": out["rationale"], "structural": out["structural"]}


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str):
    job = JOBS.get(run_id)
    if not job:
        raise HTTPException(404, "no live job")

    async def gen():
        for ev in list(job["events"]):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        while job["status"] == "running" or not job["queue"].empty():
            try:
                ev = await asyncio.wait_for(job["queue"].get(), timeout=15)
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/runs/{run_id}/sarvam-tests")
def export(run_id: str):
    run = load_run(run_id)
    return to_sarvam_tests(run.get("regression_suite", []), load_seeds(SEEDS))
