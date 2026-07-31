export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "dlq";

export interface Job {
  id: string;
  status: JobStatus;
  model: string;
  model_version?: string | null;
  input: Record<string, unknown>;
  result?: {
    label?: string;
    score?: number;
    probabilities?: Record<string, number>;
    dim?: number;
    vector?: number[];
    engine?: string;
    chars?: number;
  } | null;
  error?: string | null;
  attempts: number;
  degraded: boolean;
  priority: number;
  queue_ms?: number | null;
  compute_ms?: number | null;
  total_ms?: number | null;
  worker_id?: string | null;
  trace_id?: string | null;
  tenant: string;
  created_at?: string | null;
  finished_at?: string | null;
}

export interface ModelCard {
  name: string;
  task: string;
  description: string;
  input: Record<string, string>;
  output: Record<string, string>;
  loaded: boolean;
  version: string;
  metrics: Partial<{
    accuracy: number;
    f1_macro: number;
    samples: number;
    trained_at: string;
    training_seconds: number;
  }>;
}

export interface WorkerRow {
  worker_id: string;
  hostname: string;
  state: string;
  inflight: number;
  processed: number;
  failed: number;
  models: Record<string, boolean>;
  last_seen: string;
}

export interface Stats {
  generated_at: string;
  platform: {
    env: string;
    version: string;
    broker: string;
    database: string;
    cache: { backend: string; local_keys: number };
    topics: Record<string, string>;
  };
  jobs: {
    total: number;
    by_status: Partial<Record<JobStatus, number>>;
    window_minutes: number;
    window_by_status: Partial<Record<JobStatus, number>>;
    in_flight: number;
  };
  throughput: { completed_last_60s: number; per_second: number; window_completed: number };
  latency_ms: {
    p50: number | null;
    p95: number | null;
    p99: number | null;
    avg: number | null;
    compute_p95: number | null;
    queue_p95: number | null;
    samples: number;
  };
  queue: {
    depth: number;
    inflight_messages: number;
    outbox_pending: number;
    oldest_pending_age_s: number | null;
  };
  reliability: {
    success_rate_window: number | null;
    retries_window: number;
    dlq_open: number;
    unaccounted_jobs: number;
    breaker: { name: string; state: string; failures: number };
  };
  workers: WorkerRow[];
  chaos: { active: number };
  models: ModelCard[];
}

export interface JobEvent {
  id: number;
  job_id: string;
  type: string;
  data: Record<string, unknown>;
  at: string;
}

export interface DeadLetter {
  id: number;
  job_id: string;
  error: string;
  attempts: number;
  payload: Record<string, unknown>;
  created_at: string;
  replayed_at: string | null;
}

export interface ChaosFault {
  id: string;
  target: string;
  mode: string;
  probability: number;
  latency_ms: number;
  note?: string | null;
  created_by: string;
  created_at: string;
  expires_at: string;
  expires_in_s: number;
  active: boolean;
}

export interface Scenario {
  key: string;
  title: string;
  detail: string;
  fault: { target: string; mode: string; probability: number; latency_ms: number } | null;
}

export interface RunMetrics {
  requests_sent: number;
  accepted: number;
  rejected: number;
  status_codes: Record<string, number>;
  achieved_rps: number;
  completed_jobs: number;
  succeeded: number;
  failed: number;
  dlq: number;
  retries: number;
  still_pending: number;
  submit_latency_ms: { p50: number | null; p95: number | null; p99: number | null; max: number | null };
  end_to_end_ms: { p50: number | null; p95: number | null; p99: number | null };
  queue_drain_s: number | null;
  edge_error_rate: number;
  edge_error_rate_during_fault: number | null;
}

export interface RunVerdict {
  zero_data_loss: boolean;
  lost_jobs: number;
  accepted_jobs: number;
  accounted_jobs: number;
  recovery_seconds: number | null;
  availability_during_fault: number | null;
  sustained_rps: number;
  p99_submit_ms: number | null;
  notes: string;
}

export interface LoadRun {
  id: string;
  scenario: string;
  title: string;
  params: Record<string, number | string>;
  status: "running" | "completed" | "failed" | "cancelled";
  metrics: Partial<RunMetrics>;
  timeline: {
    client?: { t: number; sent: number; accepted: number; errors: number; p95_ms: number | null }[];
    completions?: { t: number; completed: number; p95_ms: number | null }[];
    events?: { t: number; event: string; detail: string }[];
  };
  verdict: Partial<RunVerdict>;
  error?: string | null;
  started_at: string;
  finished_at: string | null;
}
