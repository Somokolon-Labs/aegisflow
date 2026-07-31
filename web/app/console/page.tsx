"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Gauge,
  Layers3,
  Play,
  RotateCcw,
  Send,
  Server,
  Trash2,
  Zap,
} from "lucide-react";
import {
  USE_MOCKS,
  clearChaos,
  getChaos,
  getDeadLetters,
  getJobs,
  getStats,
  injectChaos,
  replayDeadLetter,
  subscribeToEvents,
  submitPrediction,
} from "@/lib/api";
import type { ChaosFault, DeadLetter, Job, JobEvent, Stats } from "@/lib/types";
import { STATUS_TONE, clock, ms, num, pct, relativeTime, shortId } from "@/lib/format";
import { BarMeter, KeyValue, MetricTile, Panel, Sparkline, StatusPill } from "@/components/ui";

const SAMPLES = [
  "the courier arrived early and the fabric feels premium",
  "charged me twice and the refund never came, terrible support",
  "quality is acceptable, delivery took the usual four days",
];

const CHAOS_PRESETS = [
  { target: "worker", mode: "pause", label: "Pause the fleet", latency_ms: 0, probability: 1, ttl_s: 20 },
  { target: "model", mode: "error", label: "Model errors 30%", latency_ms: 0, probability: 0.3, ttl_s: 25 },
  { target: "db", mode: "latency", label: "Storage +800ms", latency_ms: 800, probability: 0.8, ttl_s: 20 },
  { target: "broker", mode: "error", label: "Broker outage", latency_ms: 0, probability: 1, ttl_s: 20 },
  { target: "gateway", mode: "latency", label: "Edge +300ms", latency_ms: 300, probability: 0.5, ttl_s: 20 },
];

export default function ConsolePage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [dlq, setDlq] = useState<DeadLetter[]>([]);
  const [faults, setFaults] = useState<ChaosFault[]>([]);
  const [throughput, setThroughput] = useState<number[]>([]);
  const [latencySeries, setLatencySeries] = useState<number[]>([]);

  const [model, setModel] = useState("sentiment-v1");
  const [text, setText] = useState(SAMPLES[0]);
  const [submitting, setSubmitting] = useState(false);
  const [lastJob, setLastJob] = useState<Job | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "err"; message: string } | null>(null);

  const feedRef = useRef<HTMLDivElement>(null);

  const refreshStats = useCallback(async () => {
    try {
      const next = await getStats();
      setStats(next);
      setThroughput((prev) => [...prev, next.throughput.per_second].slice(-60));
      setLatencySeries((prev) => [...prev, next.latency_ms.p95 ?? 0].slice(-60));
    } catch {
      /* the strip in the nav already reports connectivity */
    }
  }, []);

  useEffect(() => {
    refreshStats();
    const timer = setInterval(refreshStats, 2000);
    return () => clearInterval(timer);
  }, [refreshStats]);

  useEffect(() => {
    const load = () => {
      getJobs(18).then(setJobs).catch(() => undefined);
      getDeadLetters().then(setDlq).catch(() => undefined);
      getChaos().then((data) => setFaults(data.active)).catch(() => undefined);
    };
    load();
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const unsubscribe = subscribeToEvents((event) => {
      setEvents((prev) => [event, ...prev].slice(0, 60));
    });
    return unsubscribe;
  }, []);

  const submit = async () => {
    setSubmitting(true);
    setNotice(null);
    try {
      const job = await submitPrediction(model, text);
      setLastJob(job);
      setNotice({ tone: "ok", message: `${job.id} → ${job.status}` });
      refreshStats();
    } catch (error) {
      setNotice({ tone: "err", message: error instanceof Error ? error.message : "submit failed" });
    } finally {
      setSubmitting(false);
    }
  };

  const fire = async (preset: (typeof CHAOS_PRESETS)[number]) => {
    try {
      await injectChaos({
        target: preset.target,
        mode: preset.mode,
        probability: preset.probability,
        latency_ms: preset.latency_ms,
        ttl_s: preset.ttl_s,
        note: "console",
      });
      setNotice({ tone: "ok", message: `injected ${preset.target}/${preset.mode} for ${preset.ttl_s}s` });
      getChaos().then((data) => setFaults(data.active));
    } catch (error) {
      setNotice({ tone: "err", message: error instanceof Error ? error.message : "injection failed" });
    }
  };

  const byStatus = stats?.jobs.by_status ?? {};
  const workerTotal = stats?.workers.length ?? 0;
  const models = stats?.models ?? [];

  const queueTone = useMemo(() => {
    const depth = stats?.queue.depth ?? 0;
    if (depth > 600) return "bad" as const;
    if (depth > 200) return "warn" as const;
    return "default" as const;
  }, [stats?.queue.depth]);

  return (
    <div className="mx-auto max-w-[1240px] px-5 py-10">
      <header className="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p className="label">Operations console</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">Live platform state</h1>
          <p className="mt-2 max-w-2xl text-[14.5px] leading-relaxed text-slate-400">
            Submit work, follow the event log as jobs move through the pipeline, and inject faults to watch the
            recovery behaviour in real time.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {[
            { label: "broker", value: stats?.platform.broker ?? "…" },
            { label: "store", value: stats?.platform.database ?? "…" },
            { label: "cache", value: stats?.platform.cache.backend ?? "…" },
            { label: "env", value: USE_MOCKS ? "demo" : (stats?.platform.env ?? "…") },
          ].map((chip) => (
            <span key={chip.label} className="chip border-white/10 bg-white/[0.03] text-slate-300">
              <span className="text-slate-500">{chip.label}</span>
              {chip.value}
            </span>
          ))}
        </div>
      </header>

      {/* metrics --------------------------------------------------------- */}
      <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
        <MetricTile
          label="throughput"
          value={num(stats?.throughput.per_second ?? null, 1)}
          unit="jobs/s"
          tone="good"
          hint={`${num(stats?.throughput.completed_last_60s ?? null)} in last 60s`}
        />
        <MetricTile label="p95 end-to-end" value={ms(stats?.latency_ms.p95)} hint={`p99 ${ms(stats?.latency_ms.p99)}`} />
        <MetricTile label="queue depth" value={num(Math.max(0, stats?.queue.depth ?? 0))} unit="msgs" tone={queueTone} hint={`outbox ${num(stats?.queue.outbox_pending ?? null)}`} />
        <MetricTile
          label="success rate"
          value={pct(stats?.reliability.success_rate_window, 2)}
          unit="15m"
          tone="good"
          hint={`retries ${num(stats?.reliability.retries_window ?? null)}`}
        />
        <MetricTile
          label="dead letters"
          value={num(stats?.reliability.dlq_open ?? null)}
          tone={(stats?.reliability.dlq_open ?? 0) > 0 ? "warn" : "default"}
          hint="replayable"
        />
        <MetricTile
          label="workers online"
          value={num(workerTotal)}
          tone={workerTotal > 0 ? "good" : "bad"}
          hint={`breaker ${stats?.reliability.breaker.state ?? "—"}`}
        />
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="Completion rate" hint="jobs/s, 2s samples">
          <div className="px-4 pb-4 pt-3">
            <Sparkline values={throughput} height={64} />
            <div className="mt-2 flex justify-between font-mono text-[11px] text-slate-500">
              <span>window {throughput.length * 2}s</span>
              <span>peak {num(Math.max(0, ...throughput), 1)}/s</span>
            </div>
          </div>
        </Panel>
        <Panel title="Latency p95" hint="end-to-end, ms">
          <div className="px-4 pb-4 pt-3">
            <Sparkline values={latencySeries} height={64} tone="#F0B429" />
            <div className="mt-2 grid grid-cols-3 gap-3 font-mono text-[11px] text-slate-500">
              <span>p50 {ms(stats?.latency_ms.p50)}</span>
              <span>compute p95 {ms(stats?.latency_ms.compute_p95)}</span>
              <span>samples {num(stats?.latency_ms.samples ?? null)}</span>
            </div>
          </div>
        </Panel>
      </div>

      {/* main grid ------------------------------------------------------- */}
      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          <Panel
            title="Submit inference"
            hint={USE_MOCKS ? "demo mode · simulated worker" : "POST /v1/predict"}
            action={
              <button type="button" className="btn-ghost !px-2 !py-1 text-[12px]" onClick={() => setText(SAMPLES[Math.floor(Math.random() * SAMPLES.length)])}>
                sample text
              </button>
            }
          >
            <div className="space-y-3 p-4">
              <div className="flex flex-wrap gap-2">
                {(models.length ? models.map((m) => m.name) : ["sentiment-v1", "sentiment-v2", "embed-v1"]).map((name) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => setModel(name)}
                    className={clsx(
                      "chip transition-colors",
                      model === name
                        ? "border-signal/40 bg-signal/10 text-signal"
                        : "border-white/10 bg-white/[0.03] text-slate-400 hover:text-white",
                    )}
                  >
                    <Cpu className="h-3 w-3" />
                    {name}
                  </button>
                ))}
              </div>

              <textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                rows={3}
                className="field resize-none"
                aria-label="Text to classify"
                placeholder="text to classify"
              />

              <div className="flex flex-wrap items-center gap-3">
                <button type="button" className="btn-primary" onClick={submit} disabled={submitting || !text.trim()}>
                  {submitting ? "submitting…" : "Submit"}
                  <Send className="h-4 w-4" />
                </button>
                {notice ? (
                  <span className={clsx("font-mono text-[12px]", notice.tone === "ok" ? "text-signal" : "text-alarm")}>
                    {notice.message}
                  </span>
                ) : (
                  <span className="font-mono text-[12px] text-slate-500">
                    returns 202 once durable, then waits up to 6s for the result
                  </span>
                )}
              </div>

              {lastJob ? (
                <div className="panel-tight mt-1 grid gap-4 p-4 sm:grid-cols-[minmax(0,1fr)_200px]">
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusPill status={lastJob.status} />
                      <span className="font-mono text-[11px] text-slate-500">{lastJob.id}</span>
                      {lastJob.degraded ? <StatusPill status="retry">degraded model</StatusPill> : null}
                    </div>
                    <p className="font-mono text-2xl text-white">
                      {lastJob.result?.label ?? (lastJob.result?.dim ? `${lastJob.result.dim}-dim vector` : "—")}
                      {lastJob.result?.score ? (
                        <span className="ml-2 text-sm text-slate-500">{pct(lastJob.result.score, 2)}</span>
                      ) : null}
                    </p>
                    {lastJob.result?.probabilities ? (
                      <div className="space-y-1.5 pt-1">
                        {Object.entries(lastJob.result.probabilities)
                          .sort((a, b) => b[1] - a[1])
                          .map(([label, value]) => (
                            <BarMeter
                              key={label}
                              value={value * 100}
                              max={100}
                              label={`${label} ${pct(value, 1)}`}
                              tone={label === lastJob.result?.label ? "bg-signal" : "bg-white/25"}
                            />
                          ))}
                      </div>
                    ) : null}
                    {lastJob.error ? <p className="font-mono text-[12px] text-alarm">{lastJob.error}</p> : null}
                  </div>
                  <dl className="row-divide overflow-hidden rounded-md border border-white/[0.06]">
                    <KeyValue label="queue" value={ms(lastJob.queue_ms)} />
                    <KeyValue label="compute" value={ms(lastJob.compute_ms)} />
                    <KeyValue label="total" value={ms(lastJob.total_ms)} />
                    <KeyValue label="worker" value={lastJob.worker_id ?? "—"} />
                    <KeyValue label="version" value={lastJob.model_version ?? "—"} />
                  </dl>
                </div>
              ) : null}
            </div>
          </Panel>

          <Panel title="Recent jobs" hint={`${jobs.length} shown`}>
            <div className="scroll-slim max-h-[360px] overflow-y-auto">
              <table className="w-full text-left text-[13px]">
                <thead className="sticky top-0 bg-ink-900/95 font-mono text-[10px] uppercase tracking-label text-slate-500 backdrop-blur">
                  <tr>
                    <th className="px-4 py-2 font-normal">job</th>
                    <th className="px-4 py-2 font-normal">model</th>
                    <th className="px-4 py-2 font-normal">result</th>
                    <th className="px-4 py-2 font-normal">total</th>
                    <th className="px-4 py-2 font-normal">status</th>
                  </tr>
                </thead>
                <tbody className="row-divide">
                  {jobs.map((job) => (
                    <tr key={job.id} className="hover:bg-white/[0.02]">
                      <td className="px-4 py-2.5 font-mono text-[11.5px] text-slate-400">{shortId(job.id, 8)}</td>
                      <td className="px-4 py-2.5 text-slate-300">{job.model}</td>
                      <td className="px-4 py-2.5 text-slate-300">
                        {job.result?.label ?? (job.result?.dim ? `vec[${job.result.dim}]` : job.error ? "—" : "…")}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-[11.5px] tabular-nums text-slate-400">{ms(job.total_ms)}</td>
                      <td className="px-4 py-2.5">
                        <StatusPill status={job.status} />
                      </td>
                    </tr>
                  ))}
                  {!jobs.length ? (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-[13px] text-slate-500">
                        no jobs yet — submit one above
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </Panel>

          <Panel title="Dead letter queue" hint="permanent failures, replayable">
            <div className="row-divide">
              {dlq.length ? (
                dlq.slice(0, 6).map((row) => (
                  <div key={row.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-alarm" strokeWidth={1.7} />
                    <div className="min-w-0 flex-1">
                      <p className="font-mono text-[11.5px] text-slate-400">{shortId(row.job_id, 8)}</p>
                      <p className="truncate text-[13px] text-slate-300">{row.error}</p>
                    </div>
                    <span className="font-mono text-[11px] text-slate-500">{row.attempts} attempts</span>
                    {row.replayed_at ? (
                      <StatusPill status="replayed">replayed</StatusPill>
                    ) : (
                      <button
                        type="button"
                        className="btn-ghost !px-2.5 !py-1 text-[12px]"
                        onClick={async () => {
                          await replayDeadLetter(row.id);
                          getDeadLetters().then(setDlq);
                        }}
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        replay
                      </button>
                    )}
                  </div>
                ))
              ) : (
                <p className="flex items-center gap-2 px-4 py-6 text-[13px] text-slate-500">
                  <CheckCircle2 className="h-4 w-4 text-signal" strokeWidth={1.7} />
                  queue clean — no dead letters
                </p>
              )}
            </div>
          </Panel>
        </div>

        {/* right rail --------------------------------------------------- */}
        <div className="space-y-3">
          <Panel
            title="Live event log"
            hint={USE_MOCKS ? "simulated stream" : "SSE /v1/events"}
            action={
              <span className="chip border-signal/30 bg-signal/10 text-signal">
                <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-current" />
                streaming
              </span>
            }
          >
            <div ref={feedRef} className="scroll-slim mask-fade-b max-h-[420px] overflow-y-auto">
              <div className="row-divide">
                {events.map((event) => (
                  <div key={`${event.id}-${event.job_id}`} className="flex items-baseline gap-3 px-4 py-2">
                    <span className="font-mono text-[11px] text-slate-600">{clock(event.at)}</span>
                    <span className={clsx("chip shrink-0", STATUS_TONE[event.type] ?? "border-white/10 bg-white/[0.04] text-slate-300")}>
                      {event.type}
                    </span>
                    <span className="truncate font-mono text-[11.5px] text-slate-500">{shortId(event.job_id, 6)}</span>
                    {typeof event.data.label === "string" ? (
                      <span className="ml-auto font-mono text-[11.5px] text-slate-300">{event.data.label}</span>
                    ) : typeof event.data.total_ms === "number" ? (
                      <span className="ml-auto font-mono text-[11.5px] text-slate-400">{ms(event.data.total_ms as number)}</span>
                    ) : null}
                  </div>
                ))}
                {!events.length ? (
                  <p className="px-4 py-8 text-center text-[13px] text-slate-500">waiting for events…</p>
                ) : null}
              </div>
            </div>
          </Panel>

          <Panel title="Worker fleet" hint={`${workerTotal} online`}>
            <div className="row-divide">
              {stats?.workers.length ? (
                stats.workers.map((worker) => (
                  <div key={worker.worker_id} className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Server className="h-3.5 w-3.5 text-slate-500" strokeWidth={1.7} />
                      <span className="font-mono text-[12px] text-slate-300">{worker.worker_id}</span>
                      <StatusPill status={worker.state.startsWith("healthy") ? "healthy" : "retry"}>
                        {worker.state}
                      </StatusPill>
                      <span className="ml-auto font-mono text-[11px] text-slate-500">
                        {worker.inflight} in flight
                      </span>
                    </div>
                    <div className="mt-2">
                      <BarMeter value={worker.inflight} max={6} />
                    </div>
                    <p className="mt-1.5 font-mono text-[11px] text-slate-500">
                      {num(worker.processed)} processed · {num(worker.failed)} failed · seen {relativeTime(worker.last_seen)}
                    </p>
                  </div>
                ))
              ) : (
                <p className="px-4 py-6 text-[13px] text-slate-500">no worker heartbeats in the last 30s</p>
              )}
            </div>
          </Panel>

          <Panel
            title="Fault injection"
            hint="time-boxed, self-clearing"
            action={
              <button
                type="button"
                className="btn-ghost !px-2.5 !py-1 text-[12px]"
                onClick={async () => {
                  await clearChaos();
                  getChaos().then((data) => setFaults(data.active));
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
                clear
              </button>
            }
          >
            <div className="space-y-2 p-4">
              <div className="grid gap-2 sm:grid-cols-2">
                {CHAOS_PRESETS.map((preset) => (
                  <button
                    key={`${preset.target}-${preset.mode}`}
                    type="button"
                    onClick={() => fire(preset)}
                    className="btn-chaos !justify-start !px-3 !py-2 text-[12.5px]"
                  >
                    <Zap className="h-3.5 w-3.5" />
                    {preset.label}
                  </button>
                ))}
              </div>

              {faults.length ? (
                <div className="row-divide overflow-hidden rounded-md border border-chaos/25 bg-chaos/[0.04]">
                  {faults.map((fault) => (
                    <div key={fault.id} className="flex items-center gap-2 px-3 py-2 font-mono text-[11.5px]">
                      <Activity className="h-3.5 w-3.5 text-chaos" strokeWidth={1.7} />
                      <span className="text-chaos">
                        {fault.target}/{fault.mode}
                      </span>
                      <span className="text-slate-500">p={fault.probability}</span>
                      {fault.latency_ms ? <span className="text-slate-500">+{fault.latency_ms}ms</span> : null}
                      <span className="ml-auto text-slate-500">{fault.expires_in_s}s left</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="font-mono text-[11.5px] text-slate-500">no active faults</p>
              )}
            </div>
          </Panel>

          <Panel title="Queue internals" hint="storage view">
            <dl className="row-divide">
              <KeyValue label="in flight (leased)" value={num(stats?.queue.inflight_messages ?? null)} />
              <KeyValue label="outbox pending" value={num(stats?.queue.outbox_pending ?? null)} />
              <KeyValue label="oldest pending" value={stats?.queue.oldest_pending_age_s ? `${stats.queue.oldest_pending_age_s}s` : "—"} />
              <KeyValue label="queued" value={num(byStatus.queued ?? 0)} />
              <KeyValue label="succeeded" value={num(byStatus.succeeded ?? 0)} />
              <KeyValue label="failed / dlq" value={`${num(byStatus.failed ?? 0)} / ${num(byStatus.dlq ?? 0)}`} />
              <KeyValue
                label="unaccounted"
                value={
                  <span className={stats?.reliability.unaccounted_jobs ? "text-alarm" : "text-signal"}>
                    {num(stats?.reliability.unaccounted_jobs ?? 0)}
                  </span>
                }
              />
            </dl>
          </Panel>

          <Panel title="Model registry" hint="pluggable runtime">
            <div className="row-divide">
              {(models.length ? models : []).map((card) => (
                <div key={card.name} className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <Layers3 className="h-3.5 w-3.5 text-slate-500" strokeWidth={1.7} />
                    <span className="font-mono text-[12px] text-slate-300">{card.name}</span>
                    <StatusPill status={card.loaded ? "healthy" : "retry"}>
                      {card.loaded ? "loaded" : "fallback"}
                    </StatusPill>
                  </div>
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-500">{card.description}</p>
                  {card.metrics.accuracy ? (
                    <p className="mt-1 font-mono text-[11px] text-slate-500">
                      acc {pct(card.metrics.accuracy, 2)} · f1 {pct(card.metrics.f1_macro ?? null, 2)} · {num(card.metrics.samples ?? null)} samples
                    </p>
                  ) : null}
                </div>
              ))}
              {!models.length ? <p className="px-4 py-6 text-[13px] text-slate-500">registry unavailable</p> : null}
            </div>
          </Panel>

          <Panel title="How to read this" hint="operator notes">
            <ul className="space-y-2.5 px-4 py-3 text-[13px] leading-relaxed text-slate-400">
              <li className="flex gap-2">
                <Gauge className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={1.7} />
                Queue depth rising with a flat completion rate means the fleet is saturated — that is the HPA signal in Kubernetes.
              </li>
              <li className="flex gap-2">
                <Play className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={1.7} />
                Pause the fleet, then watch the catch-up spike after the fault expires. No job is lost, only delayed.
              </li>
              <li className="flex gap-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" strokeWidth={1.7} />
                Outbox pending &gt; 0 while the breaker is open is the durable-ingest path doing its job.
              </li>
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  );
}
