---
name: vibe-coding
description: >-
  Keep an agent disciplined while vibe coding: small shippable slices,
  verify before done, prefer lean skills over long prompts, and separate UI
  from API layers. Use this skill when scoping work, starting a vibe-coding
  session, or the agent is thrashing — for any stack. Not for Sarvam API
  signatures (use sarvam-mcp, translate, speech-to-text, text-to-speech, chat,
  or voice-agents).
license: Apache-2.0
metadata:
  author: sarvam-ai
  version: "2.1"
---

# Vibe coding

**Vibe coding** means you steer; the agent helps you ship. You do not need to master everything upfront—you need **tight loops**, **honest checks**, and **the right small context** at the right time. This applies to any project.

## Habits

1. **Slice** the smallest shippable change. One feature, one failure mode, one screen—whatever keeps the next step obvious.
2. **Implement** with the **right reference** in view: a project skill, framework docs, or an official API page—not a wall of ad-hoc rules in the chat.
3. **Verify** before you call it done: run tests, a script, a linter, or a reproduction the user can repeat. If nothing ran, say what should run.
4. **Iterate**. Avoid giant diffs that mix auth, UI, and data in one go unless the user asked for that explicitly.

## Skills vs. long prompts

- Prefer a **short domain skill** (or a link to a canonical doc) over pasting the same long instructions every session.
- If the agent keeps guessing a library’s API, **read the current docs** (or a dedicated skill) instead of improvising from training data.

## Ecosystem: separate your layers

- **App and UX** (framework, components, a11y, design, routing): use skills or docs for *that* layer. Browsable examples include [skills.sh](https://skills.sh) and whatever your team ships.
- **Data and APIs** (databases, HTTP clients, model/speech/translation providers): use **that product’s** official reference or a provider-specific skill. Do not copy example HTTP calls from a UI skill into your real client without matching them to the real spec.
- **Model, speech, translation, or vision** features: treat them like any other API—**vendor docs and provider skills** win over generic chat guesses.

## Gotchas

| Issue | What to do |
|-------|------------|
| Stale or invented APIs | Open the **current** project docs or provider documentation; do not trust memory alone. |
| One skill’s example bleeding into the wrong layer | A React skill’s `fetch` snippet is not automatically your model API—reconcile with the right spec. |
| Pasting a novel into every request | Shrink to a skill, a doc link, or a few bullets; refresh when the library changes. |
| “Big bang” refactors | Split, verify, repeat. |
