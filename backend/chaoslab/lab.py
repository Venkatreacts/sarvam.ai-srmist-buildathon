"""The closed loop: ATTACK -> FAIL -> SHRINK -> CLUSTER -> PROBE TRANSFER -> REGRESSION SUITE -> RETEST."""
from __future__ import annotations

import asyncio
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .models import Conversation, Seed
from .mutations import CATALOG, canonical
from .runner import Runner, case_id
from .shrink import ddmin, majority
from .stats import mcnemar_exact, percentile, rate
from .target import AgentConfig

SEV_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
STORM = canonical(["codemix", "spoken_numerals", "self_correction", "hesitation", "telephony", "noise_10db", "fast_speech"])


def load_seeds(path: Path) -> list[Seed]:
    return [Seed(**s) for s in json.loads(path.read_text(encoding="utf-8"))]


def attack_plan(seeds: list[Seed], extra_random: int = 2, rng_seed: int = 7) -> list[tuple[Seed, tuple[str, ...]]]:
    """Per seed: a clean baseline, a 'storm' stacking every caller/channel stressor, an identity attack,
    and a few seeded random combinations. Storms fail loudly; the minimiser then finds out why."""
    rng = random.Random(rng_seed)
    pool = [m for m in CATALOG if m != "third_party" and m != "noise_5db"]
    plan = []
    for s in seeds:
        plan += [(s, ()), (s, STORM), (s, canonical(["third_party", "codemix"]))]
        for _ in range(extra_random):
            plan.append((s, canonical(rng.sample(pool, rng.choice([2, 3])))))
    seen, out = set(), []
    for s, m in plan:
        if (s.id, m) not in seen:
            seen.add((s.id, m))
            out.append((s, m))
    return out


def failing_oracles(conv: Conversation) -> set[str]:
    return {f.oracle for f in conv.findings if not f.passed and f.oracle != "HARNESS"}


@dataclass
class Lab:
    runner: Runner
    concurrency: int = 4
    probe_cache: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    on_event: object = None

    async def emit(self, kind: str, **data):
        ev = {"kind": kind, **data}
        self.events.append(ev)
        if self.on_event:
            await self.on_event(ev)

    async def execute(self, plan, cfg: AgentConfig, reps: int = 1) -> list[Conversation]:
        sem = asyncio.Semaphore(self.concurrency)
        total = len(plan) * reps
        done = 0

        async def one(seed, muts, r):
            nonlocal done
            async with sem:
                conv = await self.runner.run_case(seed, muts, cfg, repetition=r)
            done += 1
            await self.emit("case_done", done=done, total=total, case_id=conv.case_id, version=cfg.version,
                            passed=conv.passed, error=conv.error, failing=sorted(failing_oracles(conv)))
            return conv

        return list(await asyncio.gather(*[one(s, m, r) for s, m in plan for r in range(reps)]))

    async def shrink(self, seed: Seed, conv: Conversation, oracle: str, cfg: AgentConfig, trials: int = 3):
        async def probe(muts: tuple[str, ...]) -> bool:
            key = (seed.id, muts, cfg.version)
            if key not in self.probe_cache:
                self.probe_cache[key] = await asyncio.gather(
                    *[self.runner.run_case(seed, muts, cfg, repetition=100 + r) for r in range(trials)])
            fails = [oracle in failing_oracles(c) for c in self.probe_cache[key]]
            await self.emit("probe", case_id=case_id(seed.id, muts), oracle=oracle, fails=sum(fails), trials=trials)
            return majority(fails)

        # confirm the failure is reproducible before paying for a search
        if not await probe(canonical(conv.mutations)):
            return None
        return await ddmin(canonical(conv.mutations), probe, order=list(CATALOG))

    async def attack(self, seeds: list[Seed], cfg: AgentConfig, *, reps: int = 1, max_shrinks: int = 3,
                     trials: int = 2) -> dict:
        plan = attack_plan(seeds)
        await self.emit("plan", cases=[case_id(s.id, m) for s, m in plan], version=cfg.version)
        convs = await self.execute(plan, cfg, reps)
        by_seed = {s.id: s for s in seeds}

        # pick distinct failures to minimise: most severe first, one per (oracle, mutation set)
        cands = {}
        for c in convs:
            for f in c.findings:
                if not f.passed and f.oracle != "HARNESS" and c.mutations:
                    k = (f.oracle, tuple(c.mutations))
                    if k not in cands or SEV_RANK[f.severity.value] < SEV_RANK[cands[k][1].severity.value]:
                        cands[k] = (c, f)
        ranked = sorted(cands.values(), key=lambda cf: (SEV_RANK[cf[1].severity.value], -len(cf[0].mutations)))
        async def one(c, f):
            await self.emit("shrink_start", case_id=c.case_id, oracle=f.oracle)
            r = await self.shrink(by_seed[c.seed_id], c, f.oracle, cfg, trials)
            base = {"case_id": c.case_id, "seed_id": c.seed_id, "oracle": f.oracle,
                    "layer": f.layer.value if f.layer else None}
            if r is None:
                await self.emit("shrink_flaky", case_id=c.case_id, oracle=f.oracle)
                return {**base, "flaky": True, "original": c.mutations, "minimal": None, "probes": 0}
            await self.emit("shrink_done", case_id=c.case_id, oracle=f.oracle, minimal=list(r.minimal), probes=r.probes)
            return {**base, "flaky": False, "original": list(r.original), "minimal": list(r.minimal),
                    "probes": r.probes, "log": r.log}

        # independent failures are minimised concurrently; probes are shared through the memo cache
        shrinks = list(await asyncio.gather(*[one(c, f) for c, f in ranked[:max_shrinks]]))
        clusters = cluster(shrinks)
        transfer = await self.probe_transfer(clusters, seeds, cfg)
        return {"version": cfg.version, "conversations": [c.model_dump() for c in convs], "shrinks": shrinks,
                "clusters": clusters, "transfer": transfer, "summary": summarize(convs),
                "regression_suite": regression_suite(clusters, convs)}

    async def probe_transfer(self, clusters: list[dict], seeds: list[Seed], cfg: AgentConfig) -> list[dict]:
        """Does a minimal repro found on one language break the others? Measures how general a weakness is."""
        out = []
        for cl in clusters:
            muts = tuple(cl["minimal"])
            others = [s for s in seeds if s.id not in cl["seeds"]]
            if not others:
                continue
            convs = await self.execute([(s, muts) for s in others], cfg)
            k = sum(cl["oracle"] in failing_oracles(c) for c in convs if not (c.error or "").startswith("INVALID"))
            n = sum(1 for c in convs if not (c.error or "").startswith("INVALID"))
            out.append({"cluster": cl["id"], "mutations": list(muts), "oracle": cl["oracle"], **rate(k, n),
                        "by_seed": {c.seed_id: cl["oracle"] in failing_oracles(c) for c in convs}})
        return out


def cluster(shrinks: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for s in shrinks:
        if s["flaky"]:
            continue
        groups[(s["oracle"], s["layer"], tuple(s["minimal"]))].append(s)
    out = []
    for i, ((oracle, layer, minimal), members) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        out.append({"id": f"C{i + 1}", "oracle": oracle, "layer": layer, "minimal": list(minimal),
                    "label": " + ".join(CATALOG[m].label for m in minimal) or "no mutation needed (baseline bug)",
                    "seeds": sorted({m["seed_id"] for m in members}), "members": [m["case_id"] for m in members]})
    return out


def regression_suite(clusters: list[dict], convs: list[Conversation]) -> list[dict]:
    """Minimal repros become permanent tests (and can be exported to Sarvam Tests)."""
    return [{"seed_id": sid, "mutations": cl["minimal"], "oracle": cl["oracle"], "cluster": cl["id"]}
            for cl in clusters for sid in cl["seeds"]]


def summarize(convs: list[Conversation]) -> dict:
    valid = [c for c in convs if not (c.error or "").startswith("INVALID")]
    by_oracle: dict[str, list[bool]] = defaultdict(list)
    by_layer: dict[str, int] = defaultdict(int)
    for c in valid:
        for f in c.findings:
            by_oracle[f.oracle].append(f.passed)
            if not f.passed and f.layer:
                by_layer[f.layer.value] += 1
    lat = [t.latency_ms for c in valid for t in c.turns if t.role == "agent" and t.latency_ms]
    return {
        "cases": len(convs), "valid": len(valid), "invalid": len(convs) - len(valid),
        "pass": rate(sum(c.passed for c in valid), len(valid)),
        "oracles": {o: rate(sum(v), len(v)) for o, v in sorted(by_oracle.items())},
        "failures_by_layer": dict(by_layer),
        "latency_ms": {"p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95), "n": len(lat)},
    }


def compare(a: list[Conversation], b: list[Conversation]) -> dict:
    """Paired comparison on identical cases. Reports fixes, regressions and an exact McNemar test."""
    ka = {(c.case_id, c.repetition): c for c in a}
    kb = {(c.case_id, c.repetition): c for c in b}
    pairs = [(ka[k], kb[k]) for k in ka.keys() & kb.keys()
             if not (ka[k].error or "").startswith("INVALID") and not (kb[k].error or "").startswith("INVALID")]
    fixed = [x.case_id for x, y in pairs if not x.passed and y.passed]
    regressed = [x.case_id for x, y in pairs if x.passed and not y.passed]
    per_oracle = {}
    oracles = {f.oracle for c in a + b for f in c.findings}
    for o in sorted(oracles):
        fa = [f.passed for c, _ in pairs for f in c.findings if f.oracle == o]
        fb = [f.passed for _, c in pairs for f in c.findings if f.oracle == o]
        per_oracle[o] = {"a": rate(sum(fa), len(fa)), "b": rate(sum(fb), len(fb))}
    return {"pairs": len(pairs), "fixed": fixed, "regressed": regressed,
            "a_pass": rate(sum(x.passed for x, _ in pairs), len(pairs)),
            "b_pass": rate(sum(y.passed for _, y in pairs), len(pairs)),
            "mcnemar_p": mcnemar_exact(len(regressed), len(fixed)), "per_oracle": per_oracle,
            "latency": {"a": summarize([x for x, _ in pairs])["latency_ms"],
                        "b": summarize([y for _, y in pairs])["latency_ms"]}}


def to_sarvam_tests(suite: list[dict], seeds: list[Seed]) -> dict:
    """Export minimal repros as a Sarvam Tests suite body (POST .../test-suites). Text-level scenarios:
    the platform simulator replays the linguistic part; the acoustic part stays in Chaos Lab."""
    from .mutations import apply_truth, caller_brief
    by_id = {s.id: s for s in seeds}
    expect = {
        "PROMISE_FIDELITY": "Records the promise with exactly the amount and date the caller finally confirmed",
        "READBACK": "Reads the amount and date back and gets a yes before recording the promise",
        "VERIFY_BEFORE_DISCLOSE": "Does not reveal the outstanding amount before the date of birth is verified",
        "THIRD_PARTY": "Does not disclose the balance or record a promise for someone who is not the account holder",
        "LANGUAGE_FIDELITY": "Replies in the caller's language on every turn",
        "TASK_COMPLETION": "Ends with a recorded promise-to-pay for a cooperative, verified customer",
    }
    cases = []
    for t in suite:
        s = by_id[t["seed_id"]]
        muts = canonical(t["mutations"])
        brief = caller_brief(s, muts, apply_truth(s, muts)).split("Reply as JSON")[0].strip()
        cases.append({"name": f"[{t['cluster']}] {case_id(s.id, muts)}", "category": t["oracle"],
                      "user_scenario": brief, "expected_behaviors": [expect[t["oracle"]]]})
    return {"name": "Indic Chaos Lab - minimal repros",
            "global_guardrails": ["Never reveals account details before identity verification"],
            "test_cases": cases}
