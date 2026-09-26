"use client";

import { cn } from "@/lib/utils";
import type { Layer, MutationInfo, Rate } from "@/lib/api";
import { pct } from "@/lib/api";

export const LAYER_STYLE: Record<Layer, string> = {
  PERCEPTION: "text-perception border-perception/30 bg-perception/10",
  REASONING: "text-reasoning border-reasoning/30 bg-reasoning/10",
  POLICY: "text-policy border-policy/30 bg-policy/10",
  LANGUAGE: "text-language border-language/30 bg-language/10",
  TASK: "text-task border-task/30 bg-task/10",
};

export const LAYER_HELP: Record<Layer, string> = {
  PERCEPTION: "The truth never reached the agent: the channel or ASR changed it.",
  REASONING: "The agent heard the truth and still acted on something else.",
  POLICY: "The agent did something it must never do.",
  LANGUAGE: "The agent left the caller's language.",
  TASK: "The call ended without the goal being met.",
};

export const ORACLE_LABEL: Record<string, string> = {
  PROMISE_FIDELITY: "Promise fidelity",
  READBACK: "Read-back before commit",
  VERIFY_BEFORE_DISCLOSE: "Verify before disclose",
  THIRD_PARTY: "Third-party guard",
  LANGUAGE_FIDELITY: "Language fidelity",
  TASK_COMPLETION: "Task completion",
  HARNESS: "Harness error",
};

export function LayerBadge({ layer, className }: { layer: Layer | null; className?: string }) {
  if (!layer) return null;
  return (
    <span title={LAYER_HELP[layer]} className={cn("inline-flex h-5 items-center rounded border px-1.5 font-mono text-[10px] font-medium tracking-wide", LAYER_STYLE[layer], className)}>
      {layer}
    </span>
  );
}

export function MutationChip({ id, info, dim, struck, className }: { id: string; info?: MutationInfo; dim?: boolean; struck?: boolean; className?: string }) {
  const kind = info?.kind ?? "linguistic";
  const tone = kind === "acoustic" ? "border-perception/30 text-perception" : kind === "identity" ? "border-policy/30 text-policy" : "border-reasoning/30 text-reasoning";
  return (
    <span title={info?.description} className={cn("inline-flex h-6 items-center rounded-md border bg-card px-2 text-xs transition-all duration-500", tone, dim && "opacity-30", struck && "line-through opacity-25 scale-95", className)}>
      {info?.label ?? id}
    </span>
  );
}

export function PassDot({ passed, className }: { passed: boolean | null; className?: string }) {
  return <span className={cn("inline-block size-2 rounded-full", passed == null ? "bg-muted-foreground/40" : passed ? "bg-pass" : "bg-fail", className)} />;
}

/** A rate with its 95% Wilson interval drawn as a whisker, so small-n numbers never look more certain than they are. */
export function RateBar({ r, tone = "pass", className }: { r: Rate; tone?: "pass" | "fail" | "chaos"; className?: string }) {
  const color = tone === "pass" ? "bg-pass" : tone === "fail" ? "bg-fail" : "bg-chaos";
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <div className="relative h-2 flex-1 rounded-full bg-muted">
        <div className={cn("absolute inset-y-0 left-0 rounded-full", color)} style={{ width: `${(r.rate ?? 0) * 100}%` }} />
        <div className="absolute top-1/2 h-3 -translate-y-1/2 border-x border-foreground/50" style={{ left: `${r.ci95[0] * 100}%`, width: `${(r.ci95[1] - r.ci95[0]) * 100}%` }} />
      </div>
      <span className="w-24 text-right font-mono text-xs tabular-nums text-muted-foreground">
        <span className="text-foreground">{pct(r.rate)}</span> {r.k}/{r.n}
      </span>
    </div>
  );
}

export function Stat({ label, value, sub, className }: { label: string; value: React.ReactNode; sub?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-lg border bg-card px-4 py-3", className)}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 font-mono text-2xl tabular-nums tracking-tight">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

export function SectionTitle({ title, desc, right }: { title: string; desc?: string; right?: React.ReactNode }) {
  return (
    <div className="mb-5 flex items-end justify-between gap-4">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {desc && <p className="mt-0.5 max-w-2xl text-sm text-muted-foreground">{desc}</p>}
      </div>
      {right}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-dashed px-6 py-12 text-center text-sm text-muted-foreground">{children}</div>;
}
