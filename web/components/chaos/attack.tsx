"use client";

import type { Conversation, LabEvent, Run, Shrink, Status } from "@/lib/api";
import { cn } from "@/lib/utils";
import { MutationChip, ORACLE_LABEL } from "./bits";

type CellState = { passed: boolean | null; failing: string[]; invalid?: boolean };

export function parseCase(id: string): { seed: string; muts: string } {
  const i = id.indexOf(":");
  return { seed: id.slice(0, i), muts: id.slice(i + 1) };
}

/** Seeds x mutation-sets grid. Fills in live from SSE events, or from a finished run. */
export function AttackMatrix({ cases, cells, status, onOpen }: {
  cases: string[]; cells: Record<string, CellState>; status: Status | null; onOpen?: (caseId: string) => void;
}) {
  const seeds = Array.from(new Set(cases.map((c) => parseCase(c).seed)));
  const cols = Array.from(new Set(cases.map((c) => parseCase(c).muts)));
  const seedInfo = (id: string) => status?.seeds.find((s) => s.id === id);
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b bg-muted/30">
            <th className="sticky left-0 z-10 bg-card px-3 py-2 text-left font-medium text-muted-foreground">Caller</th>
            {cols.map((c) => (
              <th key={c} className="min-w-28 px-2 py-2 text-left align-bottom font-normal">
                <div className="flex flex-wrap gap-1">
                  {c === "baseline" ? <span className="text-muted-foreground">baseline</span>
                    : c.split("+").map((m) => <MutationChip key={m} id={m} info={status?.mutations[m]} className="h-5 px-1.5 text-[10px]" />)}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {seeds.map((s) => (
            <tr key={s} className="border-b last:border-0">
              <td className="sticky left-0 z-10 bg-card px-3 py-2 whitespace-nowrap">
                <div className="font-medium">{seedInfo(s)?.title ?? s}</div>
                <div className="font-mono text-[10px] text-muted-foreground">{seedInfo(s)?.lang}</div>
              </td>
              {cols.map((c) => {
                const id = `${s}:${c}`;
                if (!cases.includes(id)) return <td key={c} className="px-2 py-2" />;
                const st = cells[id];
                return (
                  <td key={c} className="px-2 py-2">
                    <button disabled={!st || st.passed == null || !onOpen} onClick={() => onOpen?.(id)}
                      className={cn("flex h-9 w-full items-center justify-center rounded-md border text-[10px] font-medium transition-all",
                        !st ? "animate-pulse border-dashed text-muted-foreground" :
                          st.invalid ? "border-warn/40 bg-warn/10 text-warn" :
                          st.passed ? "border-pass/30 bg-pass/10 text-pass hover:bg-pass/20" :
                          "border-fail/40 bg-fail/15 text-fail hover:bg-fail/25")}>
                      {!st ? "running" : st.invalid ? "invalid" : st.passed ? "pass" : st.failing.map((f) => ORACLE_LABEL[f]?.split(" ")[0] ?? f).join(" · ")}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function cellsFromRun(convs: Conversation[]): Record<string, CellState> {
  const out: Record<string, CellState> = {};
  for (const c of convs) {
    if (c.repetition) continue;
    out[c.case_id] = {
      passed: !c.error && c.findings.every((f) => f.passed),
      failing: c.findings.filter((f) => !f.passed).map((f) => f.oracle),
      invalid: c.error?.startsWith("INVALID"),
    };
  }
  return out;
}

export function cellsFromEvents(events: LabEvent[]): Record<string, CellState> {
  const out: Record<string, CellState> = {};
  for (const e of events)
    if (e.kind === "case_done" && !(e.case_id in out))
      out[e.case_id] = { passed: e.passed, failing: e.failing, invalid: e.error?.startsWith("INVALID") };
  return out;
}

/** The delta-debugging trace: every probed subset, whether it still failed, and the minimal set it converged to. */
export function ShrinkTrace({ s, status }: { s: Shrink; status: Status | null }) {
  const minimal = new Set(s.minimal ?? []);
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{ORACLE_LABEL[s.oracle] ?? s.oracle}</span>
        <span className="font-mono text-xs text-muted-foreground">{s.case_id}</span>
        <span className="ml-auto font-mono text-xs text-muted-foreground">{s.probes} probes</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {s.original.map((m) => <MutationChip key={m} id={m} info={status?.mutations[m]} struck={!s.flaky && !minimal.has(m)} className={cn(minimal.has(m) && "ring-1 ring-fail/60")} />)}
      </div>
      {s.flaky ? (
        <p className="mt-3 text-xs text-warn">Did not reproduce in a majority of trials: reported as flaky, not minimised.</p>
      ) : (
        <>
          <div className="mt-3 space-y-1">
            {(s.log ?? []).map((p, i) => (
              <div key={i} className="flex items-center gap-2 font-mono text-[11px]">
                <span className="w-5 text-right text-muted-foreground">{i + 1}</span>
                <span className={cn("w-12 rounded px-1 text-center", p.fails ? "bg-fail/15 text-fail" : "bg-pass/10 text-pass")}>{p.fails ? "FAILS" : "passes"}</span>
                <span className="text-muted-foreground">{p.mutations.length ? p.mutations.map((m) => status?.mutations[m]?.label ?? m).join(" + ") : "seed only"}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 rounded-md bg-fail/10 px-3 py-2 text-xs">
            <span className="text-muted-foreground">Minimal trigger: </span>
            <span className="font-medium text-fail">{s.minimal?.length ? s.minimal.map((m) => status?.mutations[m]?.label ?? m).join(" + ") : "none. The baseline call already fails"}</span>
          </div>
        </>
      )}
    </div>
  );
}

export function LiveFeed({ events }: { events: LabEvent[] }) {
  const lines = events.filter((e) => e.kind !== "case_done").slice(-14);
  return (
    <div className="h-64 overflow-y-auto rounded-lg border bg-black/30 p-3 font-mono text-[11px] leading-5">
      {lines.map((e, i) => (
        <div key={i} className={cn(e.kind === "error" ? "text-fail" : e.kind === "shrink_done" ? "text-chaos" : "text-muted-foreground")}>
          {e.kind === "plan" && `planned ${e.cases.length} cases against ${e.version}`}
          {e.kind === "shrink_start" && `minimising ${e.case_id} for ${e.oracle}`}
          {e.kind === "probe" && `  probe ${e.case_id} -> fails ${e.fails}/${e.trials}`}
          {e.kind === "shrink_done" && `  minimal: ${e.minimal.join(" + ") || "(seed alone)"} in ${e.probes} probes`}
          {e.kind === "shrink_flaky" && `  ${e.case_id} did not reproduce: flaky`}
          {e.kind === "done" && `done: ${e.run_id}`}
          {e.kind === "error" && e.message}
        </div>
      ))}
      {lines.length === 0 && <div className="text-muted-foreground">waiting for events…</div>}
    </div>
  );
}

export function isShrinking(events: LabEvent[]) {
  return events.some((e) => e.kind === "shrink_start") && !events.some((e) => e.kind === "done");
}

export type { CellState, Run };
