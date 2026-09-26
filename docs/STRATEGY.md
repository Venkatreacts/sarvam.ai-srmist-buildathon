# Indic Chaos Lab: strategy, design, and red team

## 1. Idea Library reality check

`Idea Library.xlsx` has 50 ideas. Row 2 is literally *"Voice Agent QA Platform: simulate thousands of
conversations… interruptions, noisy audio, policy violations… regression testing"*. Several teams will build it.
And Sarvam already ships the average version of it: **Voice Agents → Tests** (simulated user + LLM judge +
per-behaviour pass/fail, REST API at `apps.sarvam.ai/api/evals/v1/...`).

So "a Voice Agent QA platform" is not differentiated on its own, and a text-level simulator + LLM judge is
already a Sarvam platform feature. That shaped everything below.

## 2. Three differentiated directions considered

| | A. Indic Chaos Lab (selected) | B. Sunniye Samjhiye++ (evidence-linked OPD intake) | C. Aawaz se Diagnosis++ (spoken misconception graph) |
|---|---|---|---|
| Average team builds | Chatbot that "tests" another chatbot with an LLM judge | Voice form-filler + summary | STT + "explain your answer" chatbot |
| Second-generation move | Real voice loop with ground truth, layer blame, delta-debugging minimisation, paired A/B | Every clinical fact linked to an audio timestamp; verbatim vs. hypothesis columns; doctor sign-off | Misconception taxonomy, longitudinal learner model, spoken drills |
| Sarvam leverage | Bulbul (caller voices, babble), Saaras (the agent's ear), 105B (caller + remediation), 105B-conversations (agent), Tests export | Saaras timestamps, Vision (old prescriptions), 105B | Saaras, 105B, Bulbul |
| Why it could stand out | Measurable, falsifiable, developer-tool polish, closes the loop | Strong human pain, clear safety story | Emotional demo, education impact |
| Why it could fail | Sarvam Tests overlap; judges may see "just testing" | Clinical validation impossible in 24h; medical claims risk | Hard to show measurable learning gains in a demo |
| Copyability | Low: mechanism is algorithmic, not a prompt | Medium | High |

## 3. Product thesis

Voice agents in India fail in places text tests can't see: an amount spoken as Tamil words on an 8 kHz line
comes out as a different number, a spouse talks their way to a balance, a retracted figure gets saved. Chaos Lab
runs the **real** voice loop, knows the ground truth of every call, proves *which layer* broke, reduces every
failure to the **smallest trigger** that reproduces it, and turns that into a permanent regression test,
exportable to Sarvam Tests.

**Chaos Lab finds the failures, and Sarvam Tests keeps them from coming back.**

## 4. The core mechanisms (and only these)

1. **Ground-truth voice loop.** Caller brief carries exact entities; caller speech is synthesised with Bulbul,
   degraded with seeded DSP (300-3400 Hz band-pass, 8 kHz, G.711 mu-law, babble built from Bulbul voices at a
   calibrated SNR, pace), and transcribed by Saaras. Every artefact is saved for replay.
2. **Validity gate.** Entity-bearing turns are also transcribed from clean audio. If the clean control does
   not contain the truth, the *simulation* is invalid and the case is discarded, never blamed on the agent.
3. **Deterministic layer blame.** said vs. heard-clean vs. heard vs. tool arguments ⇒ PERCEPTION (never reached
   the agent), REASONING (heard it, did something else), POLICY, LANGUAGE, TASK. No LLM judge in the loop.
4. **Delta debugging (ddmin) over mutation sets**, with majority voting because agents are stochastic, and a
   probe memo so no configuration is paid for twice. Output: the 1-minimal trigger.
5. **Closed loop.** Cluster by (oracle, layer, minimal trigger) → transfer probes to the other languages →
   remediation (structural fix for policy failures, a minimal prompt patch from sarvam-105b for the rest) →
   re-run the identical suite + every minimal repro → paired comparison (Wilson CIs, exact McNemar, regressions).

## 5. Evaluation methodology

| Oracle | Check | Deterministic? |
|---|---|---|
| Promise fidelity | `record_promise_to_pay` args == truth amount/date | yes |
| Read-back | amount repeated by agent and caller spoke after it, before commit | yes |
| Verify before disclose | outstanding amount (12,450) not in any agent turn before `verify_customer` succeeded | yes |
| Third-party guard | no disclosure / no recorded promise when caller is not the account holder | yes |
| Language fidelity | ≥ 40% native-script letters on each agent turn (code-mix allowed) | yes |
| Task completion | a promise recorded for a cooperative verified customer | yes |
| Latency | agent turn latency p50/p95 (reported, not gated) | measured |

Numbers are reported with n and 95% Wilson intervals; invalid simulations are counted and shown, never dropped
silently. Nothing in the UI is hard-coded: an offline fixture exists for UI development and is visibly labelled.

## 6. Killer demo (≈3 minutes, rehearse with a pre-warmed run)

1. Overview: EMI agent v1, the voice pipeline, last profile.
2. **Attack agent** (Tamil scope): matrix fills in live; the storm cell turns red.
3. Minimisation trace: seven mutation chips get struck out one by one until two remain, e.g.
   *Numbers as words + 8 kHz phone line* (whatever the real run finds, never scripted).
4. Replay: play the clean clip, then what the agent received; diff shows the amount changing; oracle card:
   PERCEPTION, expected 4500, saved N, evidence lines.
5. Failures: the same minimal trigger transfers to Hindi/Telugu/Kannada (live rate with CI).
6. **Propose fix** → structural gate + prompt rules diff → **Re-run same suite** → fixed vs regressed, per-oracle
   bars, McNemar p. Export to Sarvam Tests.

The unforgettable moment: the audio of the caller saying one amount and the agent saving another, then the
system proving it was the phone line, not the model, in two mutations.

## 7. Red team (as the strongest competing team)

| Attack | Answer / fix |
|---|---|
| "One Claude prompt could build this." | The prompt gives a chatbot-judges-chatbot. The value is the validity gate, deterministic blame, ddmin and paired stats: algorithms, tested (22 tests). |
| "Sarvam Tests already does this." | Tests is text-level with an LLM judge. Chaos Lab adds the acoustic channel, ground truth, layer blame, minimisation, and **exports into Tests**. Complement, not clone. |
| "Your simulated caller is the thing that's wrong." | Validity gate: clean-audio control must contain the truth or the case is discarded and counted. |
| "LLM judges hallucinate." | No oracle uses an LLM. The LLM only role-plays the caller and drafts the prompt patch, which is then measured. |
| "Agents are nondeterministic; your before/after is noise." | Paired cases, repetitions, Wilson CIs, exact McNemar, minimisation requires a majority of trials. |
| "v2 is a strawman fix you wrote." | v2 is generated from v1's evidence by `/api/remediate` and saved as a reviewable diff. |
| "Barge-in and interruptions?" | Not covered: the harness is turn-based. Roadmap: Saaras v3-realtime + streaming Bulbul harness. Say so. |
| "Agent's reply isn't passed through TTS→ASR." | True; the caller reads the agent's text. Only the caller→agent direction is acoustic, which is where the entity risk lives. |
| "Toy agent." | The target is an interface; the same harness points at a Sarvam Voice Agent via the Voice Agents MCP (`send_chat`) once authenticated. |
| Demo failure risks | Rate limits (Tests: 10 runs/5 min; APIs per plan), latency of full attack, credits. Mitigate: pre-warmed run, Tamil-only live scope, TTS cache, retries honouring Retry-After. |

## 8. Roadmap

1. Run smoke test and the first live attack; replace the fixture. Tune the mutation catalogue on real failures.
2. Adapter for Sarvam Voice Agents (MCP `agents` read + `send_chat`) so any workspace agent can be attacked.
3. Push minimal repros to Sarvam Tests via REST and show them running on the platform.
4. Saaras v4 `keyterms` as a PERCEPTION-layer remediation lever for names/brands.
5. Streaming harness for barge-in (Saaras v3-realtime + Bulbul WebSocket).
