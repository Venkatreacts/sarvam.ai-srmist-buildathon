export type Layer = "PERCEPTION" | "REASONING" | "POLICY" | "LANGUAGE" | "TASK";
export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export interface Rate { k: number; n: number; rate: number | null; ci95: [number, number] }

export interface ToolCall { name: string; args: Record<string, unknown>; result: Record<string, unknown> | null }

export interface Turn {
  role: "caller" | "agent";
  said?: string | null; heard?: string | null; heard_clean?: string | null;
  audio_said?: string | null; audio_heard?: string | null; entity_slots: string[];
  text?: string | null; tool_calls: ToolCall[]; latency_ms?: number | null;
}

export interface Finding {
  oracle: string; passed: boolean; severity: Severity; layer: Layer | null;
  expected: string; actual: string; evidence: string[]; turn_index: number | null; recommended_fix: string | null;
}

export interface Conversation {
  case_id: string; seed_id: string; agent_version: string; mutations: string[]; repetition: number;
  turns: Turn[]; findings: Finding[]; error: string | null;
}

export interface Cluster { id: string; oracle: string; layer: Layer | null; minimal: string[]; label: string; seeds: string[]; members: string[] }
export interface Shrink { case_id: string; seed_id: string; oracle: string; layer: Layer | null; flaky: boolean; original: string[]; minimal: string[] | null; probes: number; log?: { mutations: string[]; fails: boolean }[] }
export interface Transfer extends Rate { cluster: string; mutations: string[]; oracle: string; by_seed: Record<string, boolean> }

export interface Summary {
  cases: number; valid: number; invalid: number; pass: Rate; oracles: Record<string, Rate>;
  failures_by_layer: Record<string, number>; latency_ms: { p50: number | null; p95: number | null; n: number };
}

export interface Comparison {
  pairs: number; fixed: string[]; regressed: string[]; a_pass: Rate; b_pass: Rate; mcnemar_p: number;
  per_oracle: Record<string, { a: Rate; b: Rate }>;
  latency: { a: Summary["latency_ms"]; b: Summary["latency_ms"] };
}

export interface Run {
  source?: "recorded" | "live";
  id: string; kind: "attack" | "retest" | "fixture"; version: string; created: number;
  conversations: Conversation[]; summary: Summary;
  shrinks?: Shrink[]; clusters?: Cluster[]; transfer?: Transfer[];
  regression_suite?: { seed_id: string; mutations: string[]; oracle: string; cluster: string }[];
  baseline_run?: string; comparison?: Comparison; regression_suite_results?: { case_id: string; passed: boolean }[];
}

export interface RunListItem { source?: "recorded" | "live"; id: string; kind: Run["kind"]; version: string; created: number; summary: Summary; baseline_run?: string }

export interface MutationInfo { id: string; kind: string; label: string; description: string }
export interface SeedInfo { id: string; title: string; lang: string; truth: { promise_amount: number; promise_date: string } }
export interface Status { live: boolean; sarvam_key: boolean; agents: string[]; seeds: SeedInfo[]; mutations: Record<string, MutationInfo> }

export type LabEvent =
  | { kind: "plan"; cases: string[]; version: string }
  | { kind: "case_done"; done: number; total: number; case_id: string; version: string; passed: boolean; error: string | null; failing: string[] }
  | { kind: "shrink_start"; case_id: string; oracle: string }
  | { kind: "probe"; case_id: string; oracle: string; fails: number; trials: number }
  | { kind: "shrink_done"; case_id: string; oracle: string; minimal: string[]; probes: number }
  | { kind: "shrink_flaky"; case_id: string; oracle: string }
  | { kind: "done"; run_id: string; run: Run }
  | { kind: "error"; message: string }
  | { kind: "heartbeat" };

/** API origin. Empty = same origin (local dev via Next rewrites). Public URL only; the Sarvam key never reaches the browser. */
export const API_BASE = (process.env.NEXT_PUBLIC_CHAOSLAB_API ?? "").replace(/\/$/, "");
const u = (path: string) => `${API_BASE}${path}`;

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail ?? msg; } catch {}
    throw new Error(`${res.status}: ${msg}`);
  }
  return res.json();
}

export const api = {
  status: () => fetch(u("/api/status")).then(j<Status>),
  runs: () => fetch(u("/api/runs")).then(j<RunListItem[]>),
  run: (id: string) => fetch(u(`/api/runs/${id}`)).then(j<Run>),
  agent: (v: string) => fetch(u(`/api/agents/${v}`)).then(j<{ version: string; system_prompt: string; gate_tools_on_verification: boolean }>),
  attack: (version: string, seeds: string[] | undefined, onEvent: (e: LabEvent) => void) =>
    stream("/api/attack", { version, seeds }, onEvent),
  retest: (baseline: Run, version: string, config: unknown, onEvent: (e: LabEvent) => void) =>
    stream("/api/retest", { baseline_run: baseline.id, baseline, version, config }, onEvent),
  remediate: (run: Run, config?: unknown) =>
    fetch(u(`/api/remediate/${run.id}`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ run, config }) })
      .then(j<{ version: string; rules: string[]; rationale: Record<string, string>; structural: { gate_tools_on_verification: boolean; pin_language?: boolean }; config: unknown }>),
  exportUrl: (runId: string) => u(`/api/runs/${runId}/sarvam-tests`),
};

export const pct = (r: number | null | undefined) => (r == null ? "—" : `${Math.round(r * 100)}%`);

/** POST and read an NDJSON event stream. The job runs inside the request, so it works on serverless too. */
async function stream(url: string, body: unknown, onEvent: (e: LabEvent) => void): Promise<void> {
  const res = await fetch(u(url), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok || !res.body) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail ?? msg; } catch {}
    throw new Error(`${res.status}: ${msg}`);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let finished = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      const ev = JSON.parse(line) as LabEvent;
      if (ev.kind === "done" || ev.kind === "error") finished = true;
      if (ev.kind !== "heartbeat") onEvent(ev);
    }
  }
  if (!finished) onEvent({ kind: "error", message: "The connection closed before the run finished (server time limit?)." });
}
