"use client";

import { useEffect, useRef } from "react";
import { Ear, Mic, Wrench } from "lucide-react";

import type { Conversation, Finding, Status, Turn } from "@/lib/api";
import { wordDiff } from "@/lib/diff";
import { cn } from "@/lib/utils";
import { LayerBadge, MutationChip, ORACLE_LABEL, PassDot } from "./bits";

function Heard({ said, heard }: { said: string; heard: string }) {
  if (said.trim() === heard.trim()) return <span>{heard}</span>;
  return (
    <span>
      {wordDiff(said, heard).map((d, i) =>
        d.op === "same" ? <span key={i}>{d.t} </span>
          : d.op === "add" ? <mark key={i} className="rounded bg-fail/20 px-0.5 text-fail">{d.t} </mark>
          : <del key={i} className="text-muted-foreground/60 decoration-fail/60">{d.t} </del>)}
    </span>
  );
}

function CallerTurn({ t, runBase, flagged }: { t: Turn; runBase: string; flagged: boolean }) {
  const changed = (t.said ?? "").trim() !== (t.heard ?? "").trim();
  return (
    <div className={cn("rounded-lg border bg-card p-3", flagged && "border-fail/60 ring-1 ring-fail/30")}>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Mic className="size-3.5" /> Caller said
        {t.entity_slots.map((s) => <span key={s} className="rounded bg-muted px-1.5 font-mono text-[10px]">{s}</span>)}
      </div>
      <p className="mt-1 text-[15px] leading-relaxed">{t.said}</p>
      <div className={cn("mt-3 flex items-center gap-2 text-xs", changed ? "text-fail" : "text-muted-foreground")}>
        <Ear className="size-3.5" /> Agent heard {changed ? "(changed by channel / ASR)" : "(identical)"}
      </div>
      <p className="mt-1 text-[15px] leading-relaxed"><Heard said={t.said ?? ""} heard={t.heard ?? ""} /></p>
      {t.heard_clean != null && t.heard_clean !== t.heard && (
        <p className="mt-2 text-xs text-muted-foreground">Clean-audio control heard: <span className="text-foreground/80">{t.heard_clean}</span></p>
      )}
      {(t.audio_said || t.audio_heard) && (
        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {t.audio_said && <label className="text-[11px] text-muted-foreground">Clean<audio className="mt-1 h-8 w-full" controls preload="none" src={`${runBase}/${t.audio_said}`} /></label>}
          {t.audio_heard && <label className="text-[11px] text-muted-foreground">What the agent received<audio className="mt-1 h-8 w-full" controls preload="none" src={`${runBase}/${t.audio_heard}`} /></label>}
        </div>
      )}
    </div>
  );
}

function AgentTurn({ t, flagged }: { t: Turn; flagged: boolean }) {
  return (
    <div className={cn("ml-8 rounded-lg border border-transparent bg-muted/40 p-3", flagged && "border-fail/60 ring-1 ring-fail/30")}>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>Agent</span>
        {t.latency_ms != null && <span className="font-mono tabular-nums">{Math.round(t.latency_ms)} ms</span>}
      </div>
      {t.tool_calls.map((c, i) => (
        <div key={i} className="mt-2 flex flex-wrap items-center gap-2 font-mono text-xs">
          <Wrench className="size-3.5 text-chaos" />
          <span className="text-chaos">{c.name}</span>
          <span className="text-muted-foreground">({JSON.stringify(c.args)})</span>
          <span className="text-muted-foreground">→ {JSON.stringify(c.result)}</span>
        </div>
      ))}
      {t.text && <p className="mt-1 text-[15px] leading-relaxed">{t.text}</p>}
    </div>
  );
}

export function FindingCard({ f, active, onClick }: { f: Finding; active?: boolean; onClick?: () => void }) {
  return (
    <button onClick={onClick} className={cn("w-full rounded-lg border bg-card p-3 text-left transition-colors hover:bg-accent/50", active && "border-foreground/30")}>
      <div className="flex items-center gap-2">
        <PassDot passed={f.passed} />
        <span className="text-sm font-medium">{ORACLE_LABEL[f.oracle] ?? f.oracle}</span>
        <span className="ml-auto flex items-center gap-1.5">
          {!f.passed && <span className="font-mono text-[10px] text-muted-foreground">{f.severity}</span>}
          <LayerBadge layer={f.layer} />
        </span>
      </div>
      {!f.passed && (
        <div className="mt-2 space-y-1.5 text-xs">
          <div><span className="text-muted-foreground">Expected </span>{f.expected}</div>
          <div><span className="text-muted-foreground">Actual </span><span className="text-fail">{f.actual}</span></div>
          {f.evidence.length > 0 && (
            <ul className="mt-1 space-y-0.5 border-l pl-2 font-mono text-[11px] text-muted-foreground">
              {f.evidence.map((e, i) => <li key={i} className="break-words">{e}</li>)}
            </ul>
          )}
          {f.recommended_fix && <div className="mt-1 rounded bg-pass/10 px-2 py-1.5 text-pass">Fix: {f.recommended_fix}</div>}
        </div>
      )}
    </button>
  );
}

export function Replay({ conv, runId, status, focus }: { conv: Conversation; runId: string; status: Status | null; focus?: string }) {
  const fails = conv.findings.filter((f) => !f.passed);
  const focused = fails.find((f) => f.oracle === focus) ?? fails[0];
  const flagged = new Set<number>();
  if (focused?.turn_index != null) flagged.add(focused.turn_index);
  if (focused?.layer === "PERCEPTION") conv.turns.forEach((t, i) => t.role === "caller" && t.entity_slots.includes("promise_amount") && flagged.add(i));
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const first = Math.min(...Array.from(flagged));
    if (Number.isFinite(first)) ref.current?.querySelector(`[data-turn="${first}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [conv.case_id, focus]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_360px]">
      <div>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm">{conv.case_id}</span>
          <span className="text-xs text-muted-foreground">agent {conv.agent_version}{conv.repetition ? ` · rep ${conv.repetition}` : ""}</span>
          <span className="ml-auto flex flex-wrap gap-1">{conv.mutations.map((m) => <MutationChip key={m} id={m} info={status?.mutations[m]} />)}</span>
        </div>
        {conv.error && <div className="mb-3 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">{conv.error}</div>}
        <div ref={ref} className="max-h-[70vh] space-y-2 overflow-y-auto pr-1">
          {conv.turns.map((t, i) => (
            <div key={i} data-turn={i}>
              {t.role === "caller" ? <CallerTurn t={t} runBase={`/runs/${runId}`} flagged={flagged.has(i)} /> : <AgentTurn t={t} flagged={flagged.has(i)} />}
            </div>
          ))}
        </div>
      </div>
      <div className="space-y-2">
        <div className="text-xs font-medium text-muted-foreground">Oracles</div>
        {conv.findings.map((f) => <FindingCard key={f.oracle} f={f} active={f === focused} />)}
      </div>
    </div>
  );
}
