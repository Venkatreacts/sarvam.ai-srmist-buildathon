# Indic Chaos Lab

**Sarvam provides the voice-agent foundation. Indic Chaos Lab discovers the edge cases you didn't think to test.**

SRMIST Sarvam AI Campus challenge · PS #2 Voice Agent QA Platform

- App: https://indic-chaos-lab.vercel.app
- API: https://chaos-lab-api.vercel.app/api/status

## Standard testing vs. Chaos Lab

Sarvam already ships native voice-agent **Tests**: *defined scenario → run → grade*. That finds the failures you
thought to write down. Chaos Lab does not replace it; it feeds it:

```
seed → mutate → acoustic stress → execute → discover → MINIMISE → EXPLAIN (layer) → regression test → export to Sarvam Tests
```

## How it works

Every call runs through a real voice loop:

```
simulated caller (sarvam-105b) → Bulbul v3 → phone line / babble / pace (seeded DSP) → Saaras v4 → agent under test
(sarvam-105b-conversations + real tool calls) → deterministic oracles
```

- **Ground truth + validity gate.** The caller holds exact entities. Entity turns are also transcribed from clean
  audio (Saaras v4 + a translate-mode reading parsed deterministically); if the clean control loses the entity the
  case is discarded, never blamed on the agent.
- **Layer attribution without an LLM judge.** said vs. clean control vs. heard vs. tool arguments ⇒ PERCEPTION,
  REASONING, POLICY, LANGUAGE, TASK.
- **Minimisation.** Delta debugging over mutation sets, majority-voted over repeated trials (agents are stochastic).
  "No mutation needed" is a result too: a baseline bug.
- **Closed loop.** Remediation proposes structural fixes (tool gate for policy failures, pinned call language) plus a
  minimal prompt patch written by sarvam-105b from the evidence; the identical suite is re-run and compared pairwise
  (Wilson intervals, exact McNemar, regressions listed).

## Recorded live results (Tamil EMI-collections agent)

| Version | What happened (real Sarvam calls) |
|---|---|
| v1 | Replies to Tamil callers in English; saves promises without reading the amount back. Minimiser: both are baseline bugs. |
| v2 | Prompt patch fixed read-back (1/4 → 4/4) but the regression gate caught a **CRITICAL** new failure: balance read to an unverified spouse; language drifted to Hindi. |
| v3 | Tool gate + pinned language + evidence-based rules: 5/5 pass vs 2/5, 0 regressions (McNemar p=0.25 at n=5, not yet significant). |

Runs are in `backend/recorded/` and replay in the UI with audio. An offline fixture generator
(`backend/scripts/make_fixture.py`) exists for UI development and is always labelled as not-Sarvam output.

## Run locally

```bash
cp .env.example .env            # SARVAM_API_KEY=...
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
python -m pytest -q backend/tests
python -m uvicorn chaoslab.server:app --app-dir backend --port 8765
npm --prefix web install && npm --prefix web run dev -- --port 3100
```

## Honest limits

- Turn-based harness: barge-in / interruptions are not covered yet (roadmap: Saaras realtime + streaming Bulbul).
- Only the caller → agent direction is acoustic; the simulated caller reads the agent's text.
- The target is a sample agent on sarvam-105b-conversations; attacking workspace agents needs the Sarvam Voice Agents MCP.
- The public API spends the configured Sarvam credits; there is no auth in front of it.
