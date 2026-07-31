"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";
import {
  AlertOctagon,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Flame,
  Loader2,
  ShieldCheck,
  Timer,
} from "lucide-react";
import { USE_MOCKS, getRun, getScenarios, listRuns, startRun } from "@/lib/api";
import type { LoadRun, Scenario } from "@/lib/types";
import { clock, ms, num, pct } from "@/lib/format";
import { Callout, KeyValue, MetricTile, Panel, SectionHeading, StatusPill } from "@/components/ui";
import { TimelineChart } from "@/components/timeline-chart";

const DEFAULTS = {
  rps: USE_MOCKS ? 60 : 25,
  duration_s: 30,
  fault_at_s: 8,
  fault_duration_s: 10,
  concurrency: 32,
  model: "sentiment-v1",
};

export default function ResiliencePage() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selected, setSelected] = useState("worker-loss");
  const [params, setParams] = useState(DEFAULTS);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [run, setRun] = useState<LoadRun | null>(null);
  const [history, setHistory] = useState<LoadRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    getScenarios().then(setScenarios).catch(() => undefined);
    listRuns().then(setHistory).catch(() => undefined);
  }, []);

  const poll = useCallback(async (runId: string) => {
    const detail = await getRun(runId);
    if (detail) setRun({ ...detail });
  }, []);

  useEffect(() => {
    if (!activeId) return;
    poll(activeId);
    const timer = setInterval(() => {
      poll(activeId).catch(() => undefined);
    }, 1200);
    return () => clearInterval(timer);
  }, [activeId, poll]);

  useEffect(() => {
    if (run?.status && run.status !== "running") {
      listRuns().then(setHistory).catch(() => undefined);
    }
  }, [run?.status]);

  const launch = async () => {
    setStarting(true);
    setError(null);
    try {
      const started = await startRun({ ...params, scenario: selected });
      setActiveId(started.id);
      setRun(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not start the drill");
    } finally {
      setStarting(false);
    }
  };

  const scenario = scenarios.find((s) => s.key === selected);
  const running = run?.status === "running" || (Boolean(activeId) && !run);
  const client = run?.timeline.client ?? [];
  const completions = run?.timeline.completions ?? [];
  const metrics = run?.metrics ?? {};
  const verdict = run?.verdict ?? {};
  const elapsed = client.length ? client[client.length - 1].t : 0;

  return (
    <div className="mx-auto max-w-[1240px] px-5 py-10">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <SectionHeading
          eyebrow="Resilience lab"
          title="Break it on purpose, then read the report"
          description="The lab drives paced traffic against the gateway, injects one real fault mid-run and reconciles every accepted job against every terminal state before it prints a verdict."
        />
        <div className="flex items-center gap-2">
          <span className="chip border-chaos/40 bg-chaos/10 text-chaos">
            <Flame className="h-3 w-3" />
            {USE_MOCKS ? "simulated drill" : "live drill"}
          </span>
        </div>
      </header>

      <div className="mt-8 grid gap-3 lg:grid-cols-[340px_minmax(0,1fr)]">
        {/* controls ---------------------------------------------------- */}
        <div className="space-y-3">
          <Panel title="Scenario" hint={`${scenarios.length} available`}>
            <div className="row-divide">
              {scenarios.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setSelected(item.key)}
                  className={clsx(
                    "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors",
                    selected === item.key ? "bg-chaos/[0.07]" : "hover:bg-white/[0.02]",
                  )}
                >
                  <CircleDot
                    className={clsx("mt-0.5 h-3.5 w-3.5 shrink-0", selected === item.key ? "text-chaos" : "text-slate-600")}
                    strokeWidth={1.8}
                  />
                  <span className="min-w-0">
                    <span className="block text-[13.5px] font-medium text-white">{item.title}</span>
                    <span className="mt-0.5 block text-[12.5px] leading-relaxed text-slate-500">{item.detail}</span>
                    {item.fault ? (
                      <span className="mt-1.5 inline-block font-mono text-[11px] text-chaos/80">
                        {item.fault.target}/{item.fault.mode} · p={item.fault.probability}
                        {item.fault.latency_ms ? ` · +${item.fault.latency_ms}ms` : ""}
                      </span>
                    ) : (
                      <span className="mt-1.5 inline-block font-mono text-[11px] text-slate-600">no fault injected</span>
                    )}
                  </span>
                </button>
              ))}
              {!scenarios.length ? <p className="px-4 py-6 text-[13px] text-slate-500">loading scenarios…</p> : null}
            </div>
          </Panel>

          <Panel title="Parameters">
            <div className="grid grid-cols-2 gap-3 p-4">
              {[
                { key: "rps", label: "target rps", min: 1, max: 500 },
                { key: "duration_s", label: "duration (s)", min: 10, max: 120 },
                { key: "fault_at_s", label: "fault at (s)", min: 0, max: 60 },
                { key: "fault_duration_s", label: "fault for (s)", min: 1, max: 60 },
                { key: "concurrency", label: "concurrency", min: 1, max: 256 },
              ].map((field) => (
                <label key={field.key} className="space-y-1.5">
                  <span className="label">{field.label}</span>
                  <input
                    type="number"
                    min={field.min}
                    max={field.max}
                    value={params[field.key as keyof typeof params] as number}
                    onChange={(event) =>
                      setParams((prev) => ({ ...prev, [field.key]: Number(event.target.value) || prev[field.key as keyof typeof prev] }))
                    }
                    className="field !py-1.5"
                  />
                </label>
              ))}
              <div className="col-span-2">
                <button type="button" className="btn-chaos w-full" onClick={launch} disabled={starting || running}>
                  {running ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      drill in progress
                    </>
                  ) : (
                    <>
                      Run drill
                      <ChevronRight className="h-4 w-4" />
                    </>
                  )}
                </button>
              </div>
              {error ? <p className="col-span-2 font-mono text-[12px] text-alarm">{error}</p> : null}
              <p className="col-span-2 font-mono text-[11px] leading-relaxed text-slate-600">
                the fault window must finish before the run ends · queue drain is measured after the load stops
              </p>
            </div>
          </Panel>

          {scenario ? (
            <Callout tone="chaos">
              <span className="font-medium">{scenario.title}.</span> {scenario.detail}
            </Callout>
          ) : null}
        </div>

        {/* results ----------------------------------------------------- */}
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricTile
              label="zero data loss"
              value={verdict.zero_data_loss === undefined ? "—" : verdict.zero_data_loss ? "PASS" : "FAIL"}
              tone={verdict.zero_data_loss === false ? "bad" : "good"}
              hint={`${num(verdict.lost_jobs ?? null)} lost of ${num(verdict.accepted_jobs ?? null)}`}
            />
            <MetricTile
              label="recovery"
              value={verdict.recovery_seconds === null || verdict.recovery_seconds === undefined ? "—" : `${verdict.recovery_seconds}s`}
              tone="good"
              hint="after the fault cleared"
            />
            <MetricTile
              label="sustained rps"
              value={num(metrics.achieved_rps ?? null, 1)}
              hint={`p99 submit ${ms(metrics.submit_latency_ms?.p99 ?? null)}`}
            />
            <MetricTile
              label="availability in fault"
              value={pct(verdict.availability_during_fault ?? null, 2)}
              tone="good"
              hint="edge acceptance"
            />
          </div>

          <Panel
            title="Drill timeline"
            hint={run ? `${run.scenario} · ${run.id}` : "no run selected"}
            action={run ? <StatusPill status={run.status} /> : null}
          >
            <div className="p-4">
              {client.length || completions.length ? (
                <TimelineChart
                  duration={Number(params.duration_s)}
                  faultFrom={scenario?.fault ? Number(params.fault_at_s) : undefined}
                  faultTo={scenario?.fault ? Number(params.fault_at_s) + Number(params.fault_duration_s) : undefined}
                  series={[
                    {
                      label: "accepted at the edge",
                      tone: "#4FD1C5",
                      points: client.map((point) => ({ t: point.t, value: point.accepted })),
                    },
                    {
                      label: "jobs completed",
                      tone: "#F0B429",
                      points: completions.map((point) => ({ t: point.t, value: point.completed })),
                    },
                  ]}
                />
              ) : (
                <div className="flex h-48 flex-col items-center justify-center gap-2 text-[13px] text-slate-500">
                  {running ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin text-chaos" />
                      collecting the first samples…
                    </>
                  ) : (
                    "run a drill to populate the timeline"
                  )}
                </div>
              )}

              {run?.timeline.events?.length ? (
                <ol className="mt-4 space-y-1.5 border-t border-white/[0.06] pt-3">
                  {run.timeline.events.map((event, index) => (
                    <li key={`${event.t}-${index}`} className="flex items-baseline gap-3 font-mono text-[11.5px]">
                      <span className="w-10 shrink-0 text-right text-slate-600">{event.t}s</span>
                      <span className={clsx(event.event.includes("fault") ? "text-chaos" : "text-signal")}>{event.event}</span>
                      <span className="text-slate-500">{event.detail}</span>
                    </li>
                  ))}
                </ol>
              ) : null}
            </div>
          </Panel>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel title="Measurements" hint={running ? `t+${elapsed}s` : "final"}>
              <dl className="row-divide">
                <KeyValue label="requests sent" value={num(metrics.requests_sent ?? null)} />
                <KeyValue label="accepted (202)" value={num(metrics.accepted ?? null)} />
                <KeyValue label="rejected at edge" value={num(metrics.rejected ?? null)} />
                <KeyValue label="jobs completed" value={num(metrics.completed_jobs ?? null)} />
                <KeyValue label="retries" value={num(metrics.retries ?? null)} />
                <KeyValue label="dead lettered" value={num(metrics.dlq ?? null)} />
                <KeyValue label="submit p50 / p95" value={`${ms(metrics.submit_latency_ms?.p50 ?? null)} / ${ms(metrics.submit_latency_ms?.p95 ?? null)}`} />
                <KeyValue label="end-to-end p95" value={ms(metrics.end_to_end_ms?.p95 ?? null)} />
                <KeyValue label="queue drain" value={metrics.queue_drain_s ? `${metrics.queue_drain_s}s` : "—"} />
              </dl>
            </Panel>

            <Panel title="Verdict" hint="reconciled after drain">
              <div className="space-y-3 p-4">
                <div
                  className={clsx(
                    "flex items-start gap-3 rounded-lg border px-4 py-3",
                    verdict.zero_data_loss === false
                      ? "border-alarm/35 bg-alarm/[0.07]"
                      : "border-signal/25 bg-signal/[0.06]",
                  )}
                >
                  {verdict.zero_data_loss === false ? (
                    <AlertOctagon className="mt-0.5 h-4 w-4 shrink-0 text-alarm" strokeWidth={1.8} />
                  ) : (
                    <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-signal" strokeWidth={1.8} />
                  )}
                  <div>
                    <p className="text-[13.5px] font-medium text-white">
                      {verdict.zero_data_loss === undefined
                        ? "Waiting for a completed drill"
                        : verdict.zero_data_loss
                          ? "Every accepted job reached a terminal state"
                          : `${verdict.lost_jobs} accepted jobs were not accounted for`}
                    </p>
                    <p className="mt-1 text-[12.5px] leading-relaxed text-slate-400">
                      {verdict.notes ?? "accepted vs (succeeded + failed + dead-lettered + pending) is compared per run using the run id as tenant."}
                    </p>
                  </div>
                </div>

                <dl className="row-divide overflow-hidden rounded-md border border-white/[0.06]">
                  <KeyValue label="accepted" value={num(verdict.accepted_jobs ?? null)} />
                  <KeyValue label="accounted" value={num(verdict.accounted_jobs ?? null)} />
                  <KeyValue label="lost" value={num(verdict.lost_jobs ?? null)} />
                  <KeyValue label="edge error rate" value={pct(metrics.edge_error_rate ?? null, 2)} />
                  <KeyValue label="during fault" value={pct(metrics.edge_error_rate_during_fault ?? null, 2)} />
                </dl>

                <p className="font-mono text-[11px] leading-relaxed text-slate-600">
                  accounted may exceed accepted when a client times out after the gateway already committed — that is a
                  client-side observation, not a lost job.
                </p>
              </div>
            </Panel>
          </div>

          <Panel title="Run history" hint={`${history.length} runs`}>
            <div className="scroll-slim max-h-[260px] overflow-y-auto">
              <table className="w-full text-left text-[13px]">
                <thead className="sticky top-0 bg-ink-900/95 font-mono text-[10px] uppercase tracking-label text-slate-500 backdrop-blur">
                  <tr>
                    <th className="px-4 py-2 font-normal">started</th>
                    <th className="px-4 py-2 font-normal">scenario</th>
                    <th className="px-4 py-2 font-normal">rps</th>
                    <th className="px-4 py-2 font-normal">recovery</th>
                    <th className="px-4 py-2 font-normal">loss</th>
                    <th className="px-4 py-2 font-normal">status</th>
                  </tr>
                </thead>
                <tbody className="row-divide">
                  {history.map((item) => (
                    <tr
                      key={item.id}
                      className="cursor-pointer hover:bg-white/[0.02]"
                      onClick={() => {
                        setActiveId(item.id);
                        setRun(item);
                        setSelected(item.scenario);
                      }}
                    >
                      <td className="px-4 py-2.5 font-mono text-[11.5px] text-slate-400">{clock(item.started_at)}</td>
                      <td className="px-4 py-2.5 text-slate-300">{item.scenario}</td>
                      <td className="px-4 py-2.5 font-mono text-[11.5px] tabular-nums text-slate-400">
                        {num(item.metrics.achieved_rps ?? null, 1)}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-[11.5px] text-slate-400">
                        {item.verdict.recovery_seconds === null || item.verdict.recovery_seconds === undefined
                          ? "—"
                          : `${item.verdict.recovery_seconds}s`}
                      </td>
                      <td className="px-4 py-2.5">
                        {item.verdict.zero_data_loss === undefined ? (
                          <span className="font-mono text-[11.5px] text-slate-500">—</span>
                        ) : item.verdict.zero_data_loss ? (
                          <span className="flex items-center gap-1 font-mono text-[11.5px] text-signal">
                            <CheckCircle2 className="h-3.5 w-3.5" /> none
                          </span>
                        ) : (
                          <span className="font-mono text-[11.5px] text-alarm">{item.verdict.lost_jobs}</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5">
                        <StatusPill status={item.status} />
                      </td>
                    </tr>
                  ))}
                  {!history.length ? (
                    <tr>
                      <td colSpan={6} className="px-4 py-8 text-center text-[13px] text-slate-500">
                        no drills recorded yet
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </Panel>

          <div className="panel flex flex-wrap items-center gap-4 px-5 py-4">
            <Timer className="h-4 w-4 text-slate-500" strokeWidth={1.7} />
            <p className="text-[13px] leading-relaxed text-slate-400">
              Recovery time is measured from the moment the fault clears to the first second where completion throughput
              is back above half of the pre-fault median. Numbers below one second read as <span className="font-mono">0s</span>.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
