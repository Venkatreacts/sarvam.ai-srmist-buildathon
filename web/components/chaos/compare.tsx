"use client";

import { ArrowRight } from "lucide-react";

import type { Comparison } from "@/lib/api";
import { pct } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ORACLE_LABEL, RateBar, Stat } from "./bits";

export function CompareView({ cmp, a, b }: { cmp: Comparison; a: string; b: string }) {
  const delta = (cmp.b_pass.rate ?? 0) - (cmp.a_pass.rate ?? 0);
  const ms = (x: number | null) => (x == null ? "—" : `${Math.round(x)} ms`);
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label={`Pass rate ${a}`} value={pct(cmp.a_pass.rate)} sub={`95% CI ${pct(cmp.a_pass.ci95[0])}–${pct(cmp.a_pass.ci95[1])}`} />
        <Stat label={`Pass rate ${b}`} value={<span className={cn(delta > 0 ? "text-pass" : delta < 0 ? "text-fail" : "")}>{pct(cmp.b_pass.rate)}</span>}
          sub={`95% CI ${pct(cmp.b_pass.ci95[0])}–${pct(cmp.b_pass.ci95[1])}`} />
        <Stat label="Fixed / regressed" value={<><span className="text-pass">{cmp.fixed.length}</span> <span className="text-muted-foreground">/</span> <span className={cn(cmp.regressed.length && "text-fail")}>{cmp.regressed.length}</span></>}
          sub={`${cmp.pairs} paired cases · McNemar p=${cmp.mcnemar_p.toFixed(3)}`} />
        <Stat label="Agent latency p50 / p95" value={<span className="text-lg">{ms(cmp.latency.b.p50)} / {ms(cmp.latency.b.p95)}</span>}
          sub={`${a}: ${ms(cmp.latency.a.p50)} / ${ms(cmp.latency.a.p95)}`} />
      </div>

      <div className="rounded-lg border">
        <div className="grid grid-cols-[180px_1fr_24px_1fr] items-center gap-3 border-b bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
          <span>Oracle</span><span>{a}</span><span /><span>{b}</span>
        </div>
        {Object.entries(cmp.per_oracle).map(([o, r]) => (
          <div key={o} className="grid grid-cols-[180px_1fr_24px_1fr] items-center gap-3 border-b px-4 py-2.5 last:border-0">
            <span className="text-sm">{ORACLE_LABEL[o] ?? o}</span>
            <RateBar r={r.a} />
            <ArrowRight className="size-3.5 text-muted-foreground" />
            <RateBar r={r.b} tone={(r.b.rate ?? 0) < (r.a.rate ?? 0) ? "fail" : "pass"} />
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <CaseList title="Fixed" tone="pass" ids={cmp.fixed} />
        <CaseList title="Regressed" tone="fail" ids={cmp.regressed} empty="No regressions on the paired suite." />
      </div>
      <p className="text-xs text-muted-foreground">
        Same cases, same mutation seeds, same channel audio. Whiskers are 95% Wilson intervals; McNemar&apos;s exact test is on discordant pairs only.
        With small suites, treat a non-significant p-value as &ldquo;not yet shown&rdquo;, not as &ldquo;no effect&rdquo;.
      </p>
    </div>
  );
}

function CaseList({ title, ids, tone, empty }: { title: string; ids: string[]; tone: "pass" | "fail"; empty?: string }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className={cn("text-sm font-medium", tone === "pass" ? "text-pass" : "text-fail")}>{title} · {ids.length}</div>
      <ul className="mt-2 space-y-1 font-mono text-xs text-muted-foreground">
        {ids.map((i) => <li key={i}>{i}</li>)}
        {ids.length === 0 && <li>{empty ?? "—"}</li>}
      </ul>
    </div>
  );
}
