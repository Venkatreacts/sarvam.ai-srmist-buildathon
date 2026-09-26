"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Bug, Download, FlaskConical, GitCompareArrows, LayoutDashboard, Loader2, PlayCircle, Sparkles, Zap } from "lucide-react";

import { Button } from "@/components/ui/button";
import { AttackMatrix, cellsFromEvents, cellsFromRun, LiveFeed, ShrinkTrace } from "@/components/chaos/attack";
import { Empty, LAYER_HELP, LayerBadge, MutationChip, ORACLE_LABEL, RateBar, SectionTitle, Stat } from "@/components/chaos/bits";
import { CompareView } from "@/components/chaos/compare";
import { Replay } from "@/components/chaos/replay";
import { api, pct, type LabEvent, type Layer, type Run, type RunListItem, type Status } from "@/lib/api";
import { cn } from "@/lib/utils";

type Section = "overview" | "attack" | "failures" | "replay" | "fix";
const NAV: { id: Section; label: string; icon: typeof Activity }[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "attack", label: "Attack Lab", icon: Zap },
  { id: "failures", label: "Failures", icon: Bug },
  { id: "replay", label: "Replay", icon: PlayCircle },
  { id: "fix", label: "Fix & Retest", icon: GitCompareArrows },
];
const PIPELINE = ["Simulated caller · sarvam-105b", "Bulbul v3 voice", "Phone line + noise", "Saaras ASR", "Agent under test", "Tools", "Oracles"];

export default function Page() {
  const [section, setSection] = useState<Section>("overview");
  const [status, setStatus] = useState<Status | null>(null);
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [attackRun, setAttackRun] = useState<Run | null>(null);
  const [retestRun, setRetestRun] = useState<Run | null>(null);
  const [retests, setRetests] = useState<Run[]>([]);
  const [configs, setConfigs] = useState<Record<string, unknown>>({});
  const [version, setVersion] = useState("v1");
  const [scope, setScope] = useState<"ta" | "all">("ta");
  const [live, setLive] = useState<{ kind: "attack" | "retest"; events: LabEvent[] } | null>(null);
  const [replay, setReplay] = useState<{ run: Run; caseId: string; oracle?: string } | null>(null);
  const [proposal, setProposal] = useState<Awaited<ReturnType<typeof api.remediate>> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [s, r] = await Promise.all([api.status(), api.runs()]);
    setStatus(s);
    setRuns(r);
    return r;
  }, []);

  useEffect(() => {
    Promise.all([api.status(), api.runs()]).then(async ([s, r]) => {
      setStatus(s);
      setRuns(r);
      // real Sarvam runs first; the offline fixture only if nothing real exists
      const a = r.find((x) => x.kind === "attack") ?? r.find((x) => x.kind === "fixture" && !x.baseline_run);
      if (a) setAttackRun(await api.run(a.id));
      // every retest of this attack, newest agent version first
      const bs = r.filter((x) => x.baseline_run && x.baseline_run === a?.id).sort((x, y) => y.version.localeCompare(x.version));
      const loaded = await Promise.all(bs.map((b) => api.run(b.id)));
      setRetests(loaded);
      if (loaded[0]) setRetestRun(loaded[0]);
    }).catch((e) => setError(String(e)));
  }, []);

  const guard = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setError(null);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(null); }
  };

  const onEvent = (kind: "attack" | "retest") => (e: LabEvent) => {
    setLive((l) => (l ? { ...l, events: [...l.events, e] } : l));
    if (e.kind === "done") {
      if (kind === "attack") { setAttackRun(e.run); setRetestRun(null); setRetests([]); setProposal(null); }
      else { setRetestRun(e.run); setRetests((rs) => [e.run, ...rs.filter((x) => x.version !== e.run.version)]); }
      refresh().catch(() => {});
    }
    if (e.kind === "error") setError(e.message);
  };

  const startAttack = () => guard("attack", async () => {
    setLive({ kind: "attack", events: [] });
    setSection("attack");
    await api.attack(version, scope === "all" ? undefined : [scope], onEvent("attack"));
  });
  // the loop continues from the latest evidence: a retest's failures feed the next fix
  const fixSource = retestRun && retestRun.summary.pass.rate !== 1 ? retestRun : attackRun;
  const propose = () => guard("remediate", async () => {
    if (!fixSource) return;
    const p = await api.remediate(fixSource, configs[fixSource.version]);
    setConfigs((c) => ({ ...c, [p.version]: p.config }));
    setProposal(p);
    await refresh();
  });
  const retest = (v: string) => guard("retest", async () => {
    if (!attackRun) return;
    setLive({ kind: "retest", events: [] });
    await api.retest(attackRun, v, configs[v], onEvent("retest"));
  });

  const liveCases = useMemo(() => live?.events.find((e) => e.kind === "plan")?.cases ?? [], [live]);
  const liveDone = live?.events.some((e) => e.kind === "done" || e.kind === "error");
  const progress = live?.events.filter((e) => e.kind === "case_done").at(-1);
  const isFixture = attackRun?.kind === "fixture";
  const openCase = (run: Run, caseId: string, oracle?: string) => { setReplay({ run, caseId, oracle }); setSection("replay"); };
  const replayConv = replay ? replay.run.conversations.find((c) => c.case_id === replay.caseId && !c.repetition) ?? replay.run.conversations.find((c) => c.case_id === replay.caseId) : null;

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r bg-sidebar px-3 py-4 md:flex">
        <div className="flex items-center gap-2 px-2">
          <FlaskConical className="size-5 text-chaos" />
          <div>
            <div className="text-sm font-semibold tracking-tight">Indic Chaos Lab</div>
            <div className="text-[10px] text-muted-foreground">built on Sarvam</div>
          </div>
        </div>
        <nav className="mt-6 space-y-0.5">
          {NAV.map((n) => (
            <button key={n.id} onClick={() => setSection(n.id)}
              className={cn("flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground",
                section === n.id && "bg-sidebar-accent text-sidebar-foreground")}>
              <n.icon className="size-4" /> {n.label}
            </button>
          ))}
        </nav>
        <div className="mt-auto space-y-2 px-2 text-[11px] text-muted-foreground">
          <div className="flex items-center gap-1.5"><span className={cn("size-1.5 rounded-full", status?.sarvam_key ? "bg-pass" : "bg-fail")} /> Sarvam API {status?.sarvam_key ? "connected" : "key missing"}</div>
          <div>{runs.length} runs stored</div>
        </div>
      </aside>

      <main className="min-w-0 flex-1 px-4 py-6 md:px-10">
        <nav className="-mx-4 mb-4 flex gap-1 overflow-x-auto border-b px-4 pb-2 md:hidden">
          {NAV.map((n) => (
            <button key={n.id} onClick={() => setSection(n.id)}
              className={cn("flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-muted-foreground", section === n.id && "bg-accent text-foreground")}>
              <n.icon className="size-3.5" /> {n.label}
            </button>
          ))}
        </nav>
        <header className="mb-8 flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 rounded-md border px-2 py-1 text-sm">
            <span className="text-muted-foreground">Agent</span>
            <span className="font-medium">EMI collections · Kaveri Finance</span>
            <select value={version} onChange={(e) => setVersion(e.target.value)} className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs outline-none">
              {(status?.agents ?? ["v1"]).map((v) => <option key={v}>{v}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2 rounded-md border px-2 py-1 text-sm">
            <span className="text-muted-foreground">Callers</span>
            <select value={scope} onChange={(e) => setScope(e.target.value as "ta" | "all")} className="rounded bg-muted px-1.5 py-0.5 text-xs outline-none">
              <option value="ta">Tamil only (demo scope)</option>
              <option value="all">Tamil · Hindi · Telugu · Kannada</option>
            </select>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {attackRun && <a href={api.exportUrl(attackRun.id)} target="_blank" className="inline-flex h-8 items-center gap-1.5 rounded-md border px-3 text-xs text-muted-foreground hover:text-foreground"><Download className="size-3.5" /> Export to Sarvam Tests</a>}
            <Button onClick={startAttack} disabled={!!busy || (!!live && !liveDone) || !status?.sarvam_key || !status?.live} className="bg-chaos text-black hover:bg-chaos/90">
              {busy === "attack" || (live?.kind === "attack" && !liveDone) ? <Loader2 className="size-4 animate-spin" /> : <Zap className="size-4" />} Attack agent {version}
            </Button>
          </div>
        </header>

        {error && <div className="mb-6 flex items-start gap-2 rounded-lg border border-fail/40 bg-fail/10 px-4 py-3 text-sm text-fail"><AlertTriangle className="mt-0.5 size-4 shrink-0" /> {error}</div>}
        {!status?.sarvam_key && status && <div className="mb-6 rounded-lg border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn">SARVAM_API_KEY is not set on the backend. Add it to <span className="font-mono">.env</span> and restart the API to run live attacks.</div>}
        {isFixture && <div className="mb-6 rounded-lg border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn">Showing an offline fixture produced by the scripted test double. These are not Sarvam results; run a live attack to replace it.</div>}
        {attackRun?.source === "recorded" && !(live && !liveDone) && (
          <div className="mb-6 flex flex-wrap items-center gap-2 rounded-lg border px-4 py-2.5 text-xs text-muted-foreground">
            <span className="size-1.5 rounded-full bg-pass" />
            Replaying a recorded <span className="text-foreground">live Sarvam run</span> from {new Date(attackRun.created * 1000).toLocaleString()}. Every number comes from real Sarvam calls. Press Attack agent to run a new one.
          </div>
        )}
        {attackRun?.source === "live" && attackRun.kind !== "fixture" && !(live && !liveDone) && (
          <div className="mb-6 flex items-center gap-2 rounded-lg border border-pass/30 px-4 py-2.5 text-xs text-pass"><span className="size-1.5 animate-pulse rounded-full bg-pass" /> Live Sarvam run · {new Date(attackRun.created * 1000).toLocaleString()}</div>
        )}

        {section === "overview" && (
          <div className="space-y-8">
            <SectionTitle title="Overview" desc="Every call goes through the real voice loop. Failures are pinned to the layer that caused them, minimised to their smallest trigger, and kept as regression tests." />
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div className="rounded-lg border p-4">
                <div className="text-xs font-medium text-muted-foreground">Standard voice-agent testing (e.g. Sarvam Tests)</div>
                <div className="mt-2 font-mono text-xs">defined scenario → run → grade</div>
                <p className="mt-2 text-xs text-muted-foreground">You find the failures you thought to write down. Sarvam ships this natively.</p>
              </div>
              <div className="rounded-lg border border-chaos/40 bg-chaos/5 p-4">
                <div className="text-xs font-medium text-chaos">Indic Chaos Lab</div>
                <div className="mt-2 font-mono text-xs">seed → mutate → acoustic stress → execute → discover → minimise → attribute → regression test</div>
                <p className="mt-2 text-xs text-muted-foreground">Finds the edge cases you didn&apos;t think to test, proves the smallest trigger and the broken layer, and exports them back to Sarvam Tests.</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5 text-xs">
              {PIPELINE.map((p, i) => (
                <span key={p} className="flex items-center gap-1.5">
                  <span className="rounded-md border bg-card px-2.5 py-1.5">{p}</span>
                  {i < PIPELINE.length - 1 && <span className="text-muted-foreground">→</span>}
                </span>
              ))}
            </div>
            {attackRun ? (
              <>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <Stat label={`Pass rate · ${attackRun.version}`} value={pct(attackRun.summary.pass.rate)} sub={`${attackRun.summary.pass.k}/${attackRun.summary.pass.n} valid calls · CI ${pct(attackRun.summary.pass.ci95[0])}–${pct(attackRun.summary.pass.ci95[1])}`} />
                  <Stat label="Failure clusters" value={attackRun.clusters?.length ?? 0} sub={`${attackRun.shrinks?.length ?? 0} failures minimised`} />
                  <Stat label="Discarded (invalid sim.)" value={attackRun.summary.invalid} sub="caller never clearly said the truth" />
                  <Stat label="Agent latency p50 / p95" value={<span className="text-lg">{Math.round(attackRun.summary.latency_ms.p50 ?? 0)} / {Math.round(attackRun.summary.latency_ms.p95 ?? 0)} ms</span>} sub={`${attackRun.summary.latency_ms.n} agent turns`} />
                </div>
                <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                  <div className="rounded-lg border p-4">
                    <div className="mb-3 text-sm font-medium">Oracle pass rates</div>
                    <div className="space-y-2.5">
                      {Object.entries(attackRun.summary.oracles).map(([o, r]) => (
                        <div key={o} className="grid grid-cols-[170px_1fr] items-center gap-3 text-sm"><span>{ORACLE_LABEL[o] ?? o}</span><RateBar r={r} /></div>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-lg border p-4">
                    <div className="mb-3 text-sm font-medium">Where failures originate</div>
                    <div className="space-y-2">
                      {(Object.keys(LAYER_HELP) as Layer[]).map((l) => (
                        <div key={l} className="flex items-center gap-3 text-sm">
                          <LayerBadge layer={l} className="w-24 justify-center" />
                          <span className="font-mono tabular-nums">{attackRun.summary.failures_by_layer[l] ?? 0}</span>
                          <span className="text-xs text-muted-foreground">{LAYER_HELP[l]}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </>
            ) : <Empty>No runs yet. Attack the agent to generate its first reliability profile.</Empty>}
          </div>
        )}

        {section === "attack" && (
          <div className="space-y-6">
            <SectionTitle title="Attack Lab" desc="Each seed caller is replayed clean, under a storm of every stressor, as a third party, and under random combinations. Click a cell to replay the call."
              right={live && !liveDone && progress && progress.kind === "case_done" ? <span className="font-mono text-xs text-muted-foreground">{progress.done}/{progress.total} calls</span> : null} />
            {live && live.kind === "attack" && !liveDone ? (
              <>
                <AttackMatrix cases={liveCases} cells={cellsFromEvents(live.events)} status={status} />
                <LiveFeed events={live.events} />
              </>
            ) : attackRun ? (
              <>
                <AttackMatrix cases={Array.from(new Set(attackRun.conversations.filter((c) => !c.repetition).map((c) => c.case_id)))}
                  cells={cellsFromRun(attackRun.conversations)} status={status} onOpen={(id) => openCase(attackRun, id)} />
                <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                  {(attackRun.shrinks ?? []).map((s) => <ShrinkTrace key={s.case_id + s.oracle} s={s} status={status} />)}
                </div>
              </>
            ) : <Empty>Start an attack to see the suite fill in live.</Empty>}
          </div>
        )}

        {section === "failures" && (
          <div className="space-y-6">
            <SectionTitle title="Failure clusters" desc="Failures grouped by oracle, blamed layer and minimal trigger. Transfer shows whether the same minimal trigger breaks the other languages." />
            {attackRun?.clusters?.length ? attackRun.clusters.map((c) => {
              const t = attackRun.transfer?.find((x) => x.cluster === c.id);
              const rep = attackRun.conversations.find((x) => x.case_id === c.members[0] && !x.repetition);
              const f = rep?.findings.find((x) => x.oracle === c.oracle && !x.passed);
              return (
                <div key={c.id} className="rounded-lg border bg-card p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-muted-foreground">{c.id}</span>
                    <span className="font-medium">{ORACLE_LABEL[c.oracle] ?? c.oracle}</span>
                    <LayerBadge layer={c.layer} />
                    <Button size="sm" variant="outline" className="ml-auto" onClick={() => openCase(attackRun, c.members[0], c.oracle)}><PlayCircle className="size-3.5" /> Replay</Button>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
                    <span className="text-muted-foreground">Minimal trigger</span>
                    {c.minimal.length ? c.minimal.map((m) => <MutationChip key={m} id={m} info={status?.mutations[m]} />) : <span>none (baseline bug)</span>}
                    <span className="ml-3 text-muted-foreground">found on</span> {c.seeds.map((s) => <span key={s} className="rounded bg-muted px-1.5 font-mono">{s}</span>)}
                  </div>
                  {f && (
                    <div className="mt-3 grid gap-2 text-xs md:grid-cols-2">
                      <div className="rounded-md bg-muted/50 px-3 py-2"><span className="text-muted-foreground">Root cause · </span>{c.layer ? LAYER_HELP[c.layer] : ""} <span className="text-fail">{f.actual}</span></div>
                      {f.recommended_fix && <div className="rounded-md bg-pass/10 px-3 py-2 text-pass"><span className="opacity-70">Recommended fix · </span>{f.recommended_fix}</div>}
                    </div>
                  )}
                  {t && (
                    <div className="mt-3 grid grid-cols-[170px_1fr] items-center gap-3 text-xs">
                      <span className="text-muted-foreground">Transfers to other languages</span>
                      <div className="flex items-center gap-3">
                        <RateBar r={t} tone="fail" className="flex-1" />
                        <span className="flex gap-1">{Object.entries(t.by_seed).map(([s, f]) => <span key={s} className={cn("rounded px-1.5 font-mono", f ? "bg-fail/15 text-fail" : "bg-pass/10 text-pass")}>{s}</span>)}</span>
                      </div>
                    </div>
                  )}
                </div>
              );
            }) : <Empty>No failure clusters yet.</Empty>}
          </div>
        )}

        {section === "replay" && (
          <div>
            <SectionTitle title="Replay" desc="What the caller said, what the agent actually heard, what it did, and which oracle caught it." />
            {replay && replayConv ? <Replay conv={replayConv} runId={replay.run.id} status={status} focus={replay.oracle} />
              : <Empty>Open a cell in the Attack Lab or a cluster to replay a call.</Empty>}
          </div>
        )}

        {section === "fix" && (
          <div className="space-y-8">
            <SectionTitle title="Fix & Retest" desc="Chaos Lab writes the smallest prompt patch for the observed failures, enforces policy failures in the tool layer, and re-runs the identical suite plus every minimal repro." />
            {!attackRun ? <Empty>Run an attack first.</Empty> : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Button onClick={propose} disabled={!!busy || !fixSource || !status?.sarvam_key || !status?.live} variant="outline">
                    {busy === "remediate" ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />} Propose fix from {fixSource?.version} evidence
                  </Button>
                  {(proposal?.version || status?.agents.filter((v) => v !== attackRun.version).at(-1)) && (
                    <Button onClick={() => retest(proposal?.version ?? status!.agents.filter((v) => v !== attackRun.version).at(-1)!)} disabled={!!busy || (!!live && !liveDone) || !status?.sarvam_key || !status?.live} className="bg-chaos text-black hover:bg-chaos/90">
                      {live?.kind === "retest" && !liveDone ? <Loader2 className="size-4 animate-spin" /> : <GitCompareArrows className="size-4" />}
                      Re-run same suite on {proposal?.version ?? status!.agents.filter((v) => v !== attackRun.version).at(-1)}
                    </Button>
                  )}
                  {live?.kind === "retest" && !liveDone && progress?.kind === "case_done" && <span className="font-mono text-xs text-muted-foreground">{progress.done}/{progress.total} calls</span>}
                </div>
                {proposal && (
                  <div className="rounded-lg border bg-card p-4">
                    <div className="text-sm font-medium">Proposed {proposal.version}</div>
                    {proposal.structural.gate_tools_on_verification && <div className="mt-2 text-xs text-pass">+ tool backend: get_outstanding and record_promise_to_pay now refuse until verify_customer succeeds</div>}
                    {proposal.structural.pin_language && <div className="mt-1 text-xs text-pass">+ agent config: the call language is pinned instead of inferred</div>}
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-xs text-pass">{proposal.rules.map((r) => `+ ${r}`).join("\n")}</pre>
                  </div>
                )}
                {retests.length > 1 && (
                  <div className="flex items-center gap-1 text-xs">
                    <span className="mr-1 text-muted-foreground">Compare {attackRun.version} with</span>
                    {[...retests].sort((x, y) => x.version.localeCompare(y.version)).map((r) => (
                      <button key={r.id} onClick={() => setRetestRun(r)}
                        className={cn("rounded-md border px-2.5 py-1 font-mono", retestRun?.id === r.id ? "border-foreground/40 bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>
                        {r.version}{r.comparison?.regressed.length ? <span className="ml-1.5 text-fail">{r.comparison.regressed.length} regressed</span> : null}
                      </button>
                    ))}
                  </div>
                )}
                {retestRun?.comparison ? (
                  <>
                    <CompareView cmp={retestRun.comparison} a={attackRun.version} b={retestRun.version} />
                    {!!retestRun.regression_suite_results?.length && (
                      <div className="rounded-lg border p-4 text-sm">
                        <div className="font-medium">Minimal repros on {retestRun.version}</div>
                        <div className="mt-2 flex flex-wrap gap-2 font-mono text-xs">
                          {retestRun.regression_suite_results.map((r) => <span key={r.case_id} className={cn("rounded px-2 py-1", r.passed ? "bg-pass/10 text-pass" : "bg-fail/15 text-fail")}>{r.case_id}</span>)}
                        </div>
                      </div>
                    )}
                  </>
                ) : live?.kind === "retest" && !liveDone ? <LiveFeed events={live.events} /> : <Empty>Propose a fix, then re-run the same suite to measure it.</Empty>}
              </>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
