/**
 * Standalone demo mode.
 *
 * When the console is deployed without a backend (Vercel preview, portfolio
 * link) it runs against this simulator instead. It models the same objects the
 * real API returns - queue depth, completion rate, retries, dead letters, chaos
 * faults, drill timelines - so the interface behaves the way it does in
 * production rather than showing frozen placeholder data.
 */

import type {
  ChaosFault,
  DeadLetter,
  Job,
  JobEvent,
  LoadRun,
  ModelCard,
  Scenario,
  Stats,
} from "./types";

const POSITIVE = ["good", "great", "excellent", "love", "fast", "smooth", "perfect", "recommend", "premium", "early", "reliable"];
const NEGATIVE = ["bad", "terrible", "awful", "hate", "broken", "slow", "late", "worst", "refund", "damaged", "crash", "never"];

const SAMPLE_TEXTS = [
  "the courier arrived early and the packaging was perfect",
  "absolutely terrible support, three weeks and no refund",
  "fabric quality is decent for the price, nothing special",
  "app crashes every time i open the checkout page",
  "fast delivery, great communication, would order again",
  "the colour faded after one wash, very disappointed",
  "works exactly as described, no complaints at all",
];

const MODELS: ModelCard[] = [
  {
    name: "sentiment-v1",
    task: "text-classification",
    description: "TF-IDF word n-grams + logistic regression. Primary production model.",
    input: { text: "string" },
    output: { label: "positive|negative|neutral", score: "float" },
    loaded: true,
    version: "sentiment-v1+tfidf-logreg",
    metrics: { accuracy: 0.9953, f1_macro: 0.9952, samples: 2110, training_seconds: 0.08 },
  },
  {
    name: "sentiment-v2",
    task: "text-classification",
    description: "Character n-gram SVM, more robust to typos. Used for canary traffic.",
    input: { text: "string" },
    output: { label: "positive|negative|neutral", score: "float" },
    loaded: true,
    version: "sentiment-v2+char-svm",
    metrics: { accuracy: 0.9976, f1_macro: 0.9976, samples: 2110, training_seconds: 0.21 },
  },
  {
    name: "embed-v1",
    task: "embedding",
    description: "TF-IDF + truncated SVD, 64-dim sentence vectors for retrieval.",
    input: { text: "string" },
    output: { vector: "float[64]", dim: "int" },
    loaded: true,
    version: "embed-v1+tfidf-svd",
    metrics: { samples: 2110, training_seconds: 0.46 },
  },
];

export const MOCK_SCENARIOS: Scenario[] = [
  {
    key: "baseline",
    title: "Baseline throughput",
    detail: "No faults. Establishes the reference numbers for latency and throughput.",
    fault: null,
  },
  {
    key: "worker-loss",
    title: "Worker fleet loss",
    detail: "All consumers stop mid-load. Leases expire, the relay requeues, the fleet catches up.",
    fault: { target: "worker", mode: "pause", probability: 1, latency_ms: 0 },
  },
  {
    key: "broker-outage",
    title: "Broker outage",
    detail: "Publishing fails. The gateway keeps accepting because the outbox is durable.",
    fault: { target: "broker", mode: "error", probability: 1, latency_ms: 0 },
  },
  {
    key: "db-slowdown",
    title: "Database slowdown",
    detail: "800ms of injected storage latency in the worker path; timeouts and retries absorb it.",
    fault: { target: "db", mode: "latency", probability: 0.8, latency_ms: 800 },
  },
  {
    key: "poison-payloads",
    title: "Poison payloads",
    detail: "30% of model calls fail hard. Retries, then dead-letter, without stalling the queue.",
    fault: { target: "model", mode: "error", probability: 0.3, latency_ms: 0 },
  },
  {
    key: "burst",
    title: "Traffic burst",
    detail: "3x request rate for the fault window with no injected failure - pure elasticity test.",
    fault: null,
  },
];

function id(prefix: string) {
  return `${prefix}_${Math.random().toString(16).slice(2, 12)}${Math.random().toString(16).slice(2, 12)}`;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function percentile(values: number[], q: number) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return Math.round(sorted[clamp(Math.round((sorted.length - 1) * q), 0, sorted.length - 1)] * 100) / 100;
}

export function classify(text: string) {
  const tokens = text.toLowerCase().split(/[^a-z']+/).filter(Boolean);
  const pos = tokens.filter((t) => POSITIVE.includes(t)).length;
  const neg = tokens.filter((t) => NEGATIVE.includes(t)).length;
  let label = "neutral";
  let score = 0.52 + Math.random() * 0.06;
  if (pos > neg) {
    label = "positive";
    score = clamp(0.72 + 0.07 * (pos - neg) + Math.random() * 0.08, 0, 0.998);
  } else if (neg > pos) {
    label = "negative";
    score = clamp(0.72 + 0.07 * (neg - pos) + Math.random() * 0.08, 0, 0.998);
  }
  const rest = (1 - score) / 2;
  const probabilities: Record<string, number> = {
    positive: label === "positive" ? score : rest,
    negative: label === "negative" ? score : rest,
    neutral: label === "neutral" ? score : rest,
  };
  Object.keys(probabilities).forEach((k) => (probabilities[k] = Math.round(probabilities[k] * 10000) / 10000));
  return { label, score: Math.round(score * 10000) / 10000, probabilities, chars: text.length };
}

const CAPACITY = 52; // jobs per second the simulated fleet can absorb

class MockWorld {
  private startedAt = Date.now();
  private lastTick = Date.now();
  private queue = 0;
  private submitted = 0;
  private succeeded = 0;
  private failed = 0;
  private dlqCount = 0;
  private retries = 0;
  private latencies: number[] = [];
  private computes: number[] = [];
  private jobs: Job[] = [];
  private events: JobEvent[] = [];
  private eventSeq = 1;
  private deadLetters: DeadLetter[] = [];
  private faults: ChaosFault[] = [];
  private runs: LoadRun[] = [];
  private burstUntil = 0;
  private history: { t: number; completed: number; p95: number }[] = [];

  private activeFault(target: string) {
    const now = Date.now();
    return this.faults.find((f) => f.target === target && new Date(f.expires_at).getTime() > now);
  }

  private pushEvent(job: Job, type: string, extra: Record<string, unknown> = {}) {
    this.events.push({
      id: this.eventSeq++,
      job_id: job.id,
      type,
      data: {
        model: job.model,
        label: job.result?.label,
        total_ms: job.total_ms,
        compute_ms: job.compute_ms,
        attempt: job.attempts,
        ...extra,
      },
      at: new Date().toISOString(),
    });
    if (this.events.length > 400) this.events = this.events.slice(-260);
  }

  private makeJob(status: Job["status"], totalMs: number, model = "sentiment-v1"): Job {
    const text = SAMPLE_TEXTS[Math.floor(Math.random() * SAMPLE_TEXTS.length)];
    const result = classify(text);
    const compute = Math.round((2.4 + Math.random() * 3.6) * 100) / 100;
    return {
      id: id("job"),
      status,
      model,
      model_version: model === "sentiment-v2" ? "sentiment-v2+char-svm" : "sentiment-v1+tfidf-logreg",
      input: { text },
      result: status === "succeeded" ? result : null,
      error: status === "succeeded" ? null : status === "dlq" ? "exhausted 4 attempts: injected model error" : "permanent: input.text must be a non-empty string",
      attempts: status === "dlq" ? 4 : 1,
      degraded: false,
      priority: 5,
      queue_ms: Math.round((totalMs - compute) * 100) / 100,
      compute_ms: compute,
      total_ms: Math.round(totalMs * 100) / 100,
      worker_id: `worker-${["a", "b", "c"][Math.floor(Math.random() * 3)]}`,
      trace_id: id("trc"),
      tenant: "public",
      created_at: new Date(Date.now() - totalMs).toISOString(),
      finished_at: new Date().toISOString(),
    };
  }

  tick() {
    const now = Date.now();
    const dt = clamp((now - this.lastTick) / 1000, 0, 4);
    if (dt <= 0.05) return;
    this.lastTick = now;

    const seconds = (now - this.startedAt) / 1000;
    const burst = now < this.burstUntil ? 3 : 1;
    const arrivalRate = (31 + 7 * Math.sin(seconds / 26) + Math.random() * 4) * burst;
    const arrivals = arrivalRate * dt;
    this.queue += arrivals;
    this.submitted += arrivals;

    const paused = Boolean(this.activeFault("worker"));
    const slowed = this.activeFault("db") ? 0.45 : 1;
    const capacity = paused ? 0 : CAPACITY * slowed * dt;
    const done = Math.min(this.queue, capacity);
    this.queue = Math.max(0, this.queue - done);

    const modelFault = this.activeFault("model");
    const failureRate = modelFault ? modelFault.probability * 0.35 : 0.004;
    const failures = done * failureRate;
    const successes = done - failures;

    this.succeeded += successes;
    this.failed += failures * 0.7;
    this.dlqCount += failures * 0.3;
    this.retries += failures * 2.1;

    const backlogPenalty = (this.queue / CAPACITY) * 1000;
    for (let i = 0; i < Math.min(6, Math.round(done)); i += 1) {
      const totalMs = 140 + backlogPenalty + Math.random() * 180;
      this.latencies.push(totalMs);
      this.computes.push(2.4 + Math.random() * 3.6);
      const job = this.makeJob(Math.random() < failureRate ? (Math.random() < 0.5 ? "failed" : "dlq") : "succeeded", totalMs);
      this.jobs.unshift(job);
      this.pushEvent(job, job.status);
      if (job.status === "dlq") {
        this.deadLetters.unshift({
          id: this.deadLetters.length + 1,
          job_id: job.id,
          error: "exhausted 4 attempts: chaos: injected model error",
          attempts: 4,
          payload: { job_id: job.id, model: job.model, input: job.input },
          created_at: new Date().toISOString(),
          replayed_at: null,
        });
      }
    }
    if (this.jobs.length > 80) this.jobs = this.jobs.slice(0, 60);
    if (this.latencies.length > 300) this.latencies = this.latencies.slice(-220);
    if (this.computes.length > 300) this.computes = this.computes.slice(-220);
    if (this.deadLetters.length > 25) this.deadLetters = this.deadLetters.slice(0, 20);

    this.history.push({ t: Math.round(seconds), completed: Math.round(done / dt), p95: percentile(this.latencies, 0.95) ?? 0 });
    if (this.history.length > 120) this.history = this.history.slice(-90);
  }

  stats(): Stats {
    this.tick();
    const total = Math.round(this.submitted);
    const succeeded = Math.round(this.succeeded);
    const failed = Math.round(this.failed);
    const dlq = Math.round(this.dlqCount);
    const queued = Math.round(this.queue);
    const paused = Boolean(this.activeFault("worker"));
    const recent = this.history.slice(-6);
    const perSecond = recent.length ? recent.reduce((a, b) => a + b.completed, 0) / recent.length : 0;

    return {
      generated_at: new Date().toISOString(),
      platform: {
        env: "demo",
        version: "1.0.0",
        broker: "kafka",
        database: "postgres",
        cache: { backend: "redis", local_keys: 0 },
        topics: { requests: "inference.requests", dlq: "inference.dlq" },
      },
      jobs: {
        total,
        by_status: { succeeded, failed, dlq, queued, running: paused ? 0 : Math.min(12, queued) },
        window_minutes: 15,
        window_by_status: { succeeded: Math.round(succeeded * 0.6), failed: Math.round(failed * 0.6), dlq: Math.round(dlq * 0.6) },
        in_flight: queued,
      },
      throughput: {
        completed_last_60s: Math.round(perSecond * 60),
        per_second: Math.round(perSecond * 10) / 10,
        window_completed: Math.round(succeeded * 0.6),
      },
      latency_ms: {
        p50: percentile(this.latencies, 0.5),
        p95: percentile(this.latencies, 0.95),
        p99: percentile(this.latencies, 0.99),
        avg: this.latencies.length
          ? Math.round((this.latencies.reduce((a, b) => a + b, 0) / this.latencies.length) * 100) / 100
          : null,
        compute_p95: percentile(this.computes, 0.95),
        queue_p95: percentile(this.latencies.map((l) => l - 4), 0.95),
        samples: this.latencies.length,
      },
      queue: {
        depth: queued,
        inflight_messages: paused ? queued : Math.min(18, queued),
        outbox_pending: this.activeFault("broker") ? Math.round(this.queue * 0.8) : 0,
        oldest_pending_age_s: queued ? Math.round((queued / CAPACITY) * 10) / 10 : null,
      },
      reliability: {
        success_rate_window: succeeded + failed + dlq ? Math.round((succeeded / (succeeded + failed + dlq)) * 10000) / 10000 : null,
        retries_window: Math.round(this.retries),
        dlq_open: this.deadLetters.filter((d) => !d.replayed_at).length,
        unaccounted_jobs: 0,
        breaker: {
          name: "broker.publish",
          state: this.activeFault("broker") ? "open" : "closed",
          failures: this.activeFault("broker") ? 5 : 0,
        },
      },
      workers: ["worker-a", "worker-b", "worker-c"].map((worker, index) => ({
        worker_id: worker,
        hostname: `aegisflow-worker-${index + 1}`,
        state: paused ? "paused (chaos)" : "healthy",
        inflight: paused ? 0 : Math.round(Math.random() * 4),
        processed: Math.round(this.succeeded / 3),
        failed: Math.round(this.failed / 3),
        models: { "sentiment-v1": true, "sentiment-v2": true, "embed-v1": true },
        last_seen: new Date().toISOString(),
      })),
      chaos: { active: this.faults.filter((f) => new Date(f.expires_at).getTime() > Date.now()).length },
      models: MODELS,
    };
  }

  listJobs(limit = 25) {
    this.tick();
    return this.jobs.slice(0, limit);
  }

  tailEvents(after: number) {
    this.tick();
    return this.events.filter((e) => e.id > after);
  }

  lastEventId() {
    return this.eventSeq - 1;
  }

  submit(model: string, input: Record<string, unknown>): Job {
    const text = String(input.text ?? "");
    if (!text.trim()) {
      throw new Error("input.text must be a non-empty string");
    }
    const compute = Math.round((2.4 + Math.random() * 3.6) * 100) / 100;
    const totalMs = 120 + (this.queue / CAPACITY) * 1000 + Math.random() * 120;
    const result = model === "embed-v1"
      ? {
          dim: 64,
          vector: Array.from({ length: 8 }, () => Math.round((Math.random() - 0.5) * 1e4) / 1e4),
          engine: "tfidf-svd",
        }
      : classify(text);
    const job: Job = {
      id: id("job"),
      status: "succeeded",
      model,
      model_version: `${model}+demo`,
      input,
      result,
      error: null,
      attempts: 1,
      degraded: false,
      priority: 5,
      queue_ms: Math.round((totalMs - compute) * 100) / 100,
      compute_ms: compute,
      total_ms: Math.round(totalMs * 100) / 100,
      worker_id: "worker-a",
      trace_id: id("trc"),
      tenant: "public",
      created_at: new Date(Date.now() - totalMs).toISOString(),
      finished_at: new Date().toISOString(),
    };
    this.jobs.unshift(job);
    this.pushEvent(job, "succeeded", { source: "console" });
    return job;
  }

  deadLetterList() {
    this.tick();
    return this.deadLetters;
  }

  replay(dlqId: number) {
    const row = this.deadLetters.find((d) => d.id === dlqId);
    if (row) row.replayed_at = new Date().toISOString();
    return row;
  }

  chaosList() {
    const now = Date.now();
    return {
      active: this.faults.filter((f) => new Date(f.expires_at).getTime() > now),
      history: this.faults.slice(0, 20),
    };
  }

  inject(fault: { target: string; mode: string; probability: number; latency_ms: number; ttl_s: number; note?: string }) {
    const now = Date.now();
    const row: ChaosFault = {
      id: id("chaos"),
      target: fault.target,
      mode: fault.mode,
      probability: fault.probability,
      latency_ms: fault.latency_ms,
      note: fault.note ?? null,
      created_by: "console",
      created_at: new Date(now).toISOString(),
      expires_at: new Date(now + fault.ttl_s * 1000).toISOString(),
      expires_in_s: fault.ttl_s,
      active: true,
    };
    this.faults.unshift(row);
    return row;
  }

  clearChaos(target?: string) {
    const before = this.faults.length;
    this.faults = target ? this.faults.filter((f) => f.target !== target) : [];
    return before - this.faults.length;
  }

  /** Simulates a drill in real time so the resilience page animates. */
  startRun(params: {
    scenario: string;
    rps: number;
    duration_s: number;
    fault_at_s: number;
    fault_duration_s: number;
    concurrency: number;
    model: string;
  }): LoadRun {
    const scenario = MOCK_SCENARIOS.find((s) => s.key === params.scenario) ?? MOCK_SCENARIOS[0];
    const run: LoadRun = {
      id: id("run"),
      scenario: params.scenario,
      title: scenario.title,
      params,
      status: "running",
      metrics: {},
      timeline: { client: [], completions: [], events: [{ t: 0, event: "load started", detail: `${params.rps} rps target` }] },
      verdict: {},
      started_at: new Date().toISOString(),
      finished_at: null,
    };
    this.runs.unshift(run);

    if (params.scenario === "burst") {
      this.burstUntil = Date.now() + (params.fault_at_s + params.fault_duration_s) * 1000;
    }

    const faultFrom = params.fault_at_s;
    const faultTo = params.fault_at_s + params.fault_duration_s;
    const started = Date.now();
    let injected = false;

    const interval = setInterval(() => {
      const t = Math.floor((Date.now() - started) / 1000);
      if (scenario.fault && !injected && t >= faultFrom) {
        injected = true;
        this.inject({ ...scenario.fault, ttl_s: params.fault_duration_s, note: `drill ${run.id}` });
        run.timeline.events?.push({
          t,
          event: "fault injected",
          detail: `${scenario.fault.target}/${scenario.fault.mode} p=${scenario.fault.probability}`,
        });
      }
      if (scenario.fault && injected && t >= faultTo && !run.timeline.events?.some((e) => e.event === "fault cleared")) {
        this.clearChaos(scenario.fault.target);
        run.timeline.events?.push({ t, event: "fault cleared", detail: "recovery observed" });
      }

      const inFault = t >= faultFrom && t < faultTo;
      const rate = params.scenario === "burst" && inFault ? params.rps * 3 : params.rps;
      const degraded = inFault && scenario.fault;
      const accepted = scenario.fault?.target === "gateway" && inFault ? Math.round(rate * 0.72) : rate;
      run.timeline.client?.push({
        t,
        sent: rate,
        accepted,
        errors: rate - accepted,
        p95_ms: Math.round((degraded ? 240 + Math.random() * 260 : 60 + Math.random() * 40) * 100) / 100,
      });

      let completed = rate;
      if (degraded) {
        if (scenario.fault?.mode === "pause") completed = 0;
        else if (scenario.fault?.mode === "latency") completed = Math.round(rate * 0.45);
        else if (scenario.fault?.mode === "error") completed = Math.round(rate * 0.62);
      } else if (t >= faultTo && t <= faultTo + 3 && scenario.fault?.mode === "pause") {
        completed = Math.round(rate * 2.4); // catch-up burst after requeue
      }
      run.timeline.completions?.push({
        t,
        completed,
        p95_ms: Math.round((degraded ? 900 + Math.random() * 700 : 210 + Math.random() * 120) * 100) / 100,
      });

      if (t >= params.duration_s) {
        clearInterval(interval);
        const client = run.timeline.client ?? [];
        const completions = run.timeline.completions ?? [];
        const sent = client.reduce((a, b) => a + b.sent, 0);
        const acceptedTotal = client.reduce((a, b) => a + b.accepted, 0);
        const completedTotal = completions.reduce((a, b) => a + b.completed, 0);
        const recovery = scenario.fault?.mode === "pause" ? 2 : scenario.fault ? 1 : null;
        const poison = params.scenario === "poison-payloads";
        run.status = "completed";
        run.finished_at = new Date().toISOString();
        run.metrics = {
          requests_sent: sent,
          accepted: acceptedTotal,
          rejected: sent - acceptedTotal,
          status_codes: { "202": acceptedTotal, "422": poison ? Math.round(sent * 0.14) : 0 },
          achieved_rps: Math.round((sent / params.duration_s) * 100) / 100,
          completed_jobs: Math.min(acceptedTotal, completedTotal),
          succeeded: Math.round(Math.min(acceptedTotal, completedTotal) * (poison ? 0.79 : 0.999)),
          failed: poison ? Math.round(acceptedTotal * 0.14) : 0,
          dlq: poison ? Math.round(acceptedTotal * 0.07) : 0,
          retries: poison ? Math.round(acceptedTotal * 0.21) : scenario.fault ? Math.round(acceptedTotal * 0.03) : 0,
          still_pending: 0,
          submit_latency_ms: { p50: 44.1, p95: scenario.fault ? 312.6 : 96.4, p99: scenario.fault ? 688.2 : 141.7, max: 1204.3 },
          end_to_end_ms: {
            p50: scenario.fault ? 640.2 : 268.4,
            p95: scenario.fault ? 2480.7 : 512.9,
            p99: scenario.fault ? 4120.5 : 702.1,
          },
          queue_drain_s: scenario.fault?.mode === "pause" ? 3.6 : 1.2,
          edge_error_rate: Math.round(((sent - acceptedTotal) / Math.max(1, sent)) * 10000) / 10000,
          edge_error_rate_during_fault: scenario.fault ? 0 : 0,
        };
        run.verdict = {
          zero_data_loss: true,
          lost_jobs: 0,
          accepted_jobs: acceptedTotal,
          accounted_jobs: acceptedTotal,
          recovery_seconds: recovery,
          availability_during_fault: 1,
          sustained_rps: run.metrics.achieved_rps ?? params.rps,
          p99_submit_ms: run.metrics.submit_latency_ms?.p99 ?? null,
          notes: scenario.detail,
        };
      }
    }, 1000);

    return run;
  }

  getRun(runId: string) {
    return this.runs.find((r) => r.id === runId);
  }

  listRuns() {
    return this.runs;
  }
}

let world: MockWorld | null = null;

export function mockWorld(): MockWorld {
  if (!world) world = new MockWorld();
  return world;
}
