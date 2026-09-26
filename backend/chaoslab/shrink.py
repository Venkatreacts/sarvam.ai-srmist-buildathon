"""Delta debugging (Zeller & Hildebrandt, ddmin) over mutation sets, adapted to a nondeterministic target.

Given a failing case with mutations {codemix, spoken_numerals, telephony, noise_10db, hesitation}, find a
1-minimal subset that still reproduces the SAME failure (same oracle): removing any single remaining
mutation makes the failure go away. Because agents are stochastic, a subset "reproduces" only if it fails
in a majority of `trials` executions; every probe is memoised so no configuration is paid for twice.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

Probe = Callable[[tuple[str, ...]], Awaitable[bool]]


@dataclass
class ShrinkResult:
    original: tuple[str, ...]
    minimal: tuple[str, ...]
    probes: int
    log: list[dict] = field(default_factory=list)


async def ddmin(items: tuple[str, ...], fails: Probe, *, order: list[str] | None = None) -> ShrinkResult:
    memo: dict[frozenset, bool] = {}
    log: list[dict] = []

    def key(xs):
        return tuple(sorted(xs, key=order.index)) if order else tuple(sorted(xs))

    async def test(xs: list[str]) -> bool:
        fk = frozenset(xs)
        if fk not in memo:
            memo[fk] = await fails(key(xs))
            log.append({"mutations": list(key(xs)), "fails": memo[fk]})
        return memo[fk]

    cur = list(items)
    n = 2
    while len(cur) >= 2:
        chunk = max(1, len(cur) // n)
        subsets = [cur[i:i + chunk] for i in range(0, len(cur), chunk)]
        reduced = False
        for s in subsets:                                   # reduce to a failing subset
            if await test(s):
                cur, n, reduced = s, 2, True
                break
        if not reduced:
            for s in subsets:                               # reduce to a failing complement
                comp = [x for x in cur if x not in s]
                if comp and await test(comp):
                    cur, n, reduced = comp, max(n - 1, 2), True
                    break
        if not reduced:
            if n >= len(cur):
                break
            n = min(len(cur), n * 2)
    # a single remaining mutation might itself be unnecessary (the seed alone fails)
    if len(cur) == 1 and await test([]):
        cur = []
    return ShrinkResult(original=key(items), minimal=key(cur), probes=len(memo), log=log)


def majority(results: list[bool]) -> bool:
    return sum(results) * 2 > len(results)
