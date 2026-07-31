/**
 * Single client for the AegisFlow API.
 *
 * `NEXT_PUBLIC_USE_MOCKS=false` + `NEXT_PUBLIC_API_URL` points the console at a
 * live gateway. Anything else runs the built-in simulator, which is what the
 * public demo uses.
 */

import { MOCK_SCENARIOS, mockWorld } from "./mock";
import type { ChaosFault, DeadLetter, Job, JobEvent, LoadRun, Scenario, Stats } from "./types";

export const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS !== "false";
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const API_KEY = process.env.NEXT_PUBLIC_DEMO_API_KEY ?? "demo-key-aegisflow";
const ADMIN_KEY = process.env.NEXT_PUBLIC_DEMO_ADMIN_KEY ?? "admin-key-aegisflow";

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}, admin = false): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "content-type": "application/json",
      "X-API-Key": admin ? ADMIN_KEY : API_KEY,
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep status text */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

/* ---------------------------------------------------------------- reads */
export async function getStats(): Promise<Stats> {
  if (USE_MOCKS) return mockWorld().stats();
  return request<Stats>("/v1/stats");
}

export async function getJobs(limit = 25): Promise<Job[]> {
  if (USE_MOCKS) return mockWorld().listJobs(limit);
  const data = await request<{ jobs: Job[] }>(`/v1/jobs?limit=${limit}`);
  return data.jobs;
}

export async function getDeadLetters(): Promise<DeadLetter[]> {
  if (USE_MOCKS) return mockWorld().deadLetterList();
  const data = await request<{ dead_letters: DeadLetter[] }>("/v1/dlq?limit=25");
  return data.dead_letters;
}

export async function getChaos(): Promise<{ active: ChaosFault[]; history: ChaosFault[] }> {
  if (USE_MOCKS) return mockWorld().chaosList();
  return request<{ active: ChaosFault[]; history: ChaosFault[] }>("/v1/chaos");
}

export async function getScenarios(): Promise<Scenario[]> {
  if (USE_MOCKS) return MOCK_SCENARIOS;
  const data = await request<{ scenarios: Scenario[] }>("/v1/lab/scenarios");
  return data.scenarios;
}

export async function listRuns(): Promise<LoadRun[]> {
  if (USE_MOCKS) return mockWorld().listRuns();
  const data = await request<{ runs: LoadRun[] }>("/v1/lab/loadtest");
  return data.runs;
}

export async function getRun(runId: string): Promise<LoadRun | undefined> {
  if (USE_MOCKS) return mockWorld().getRun(runId);
  return request<LoadRun>(`/v1/lab/loadtest/${runId}`);
}

/* --------------------------------------------------------------- writes */
export async function submitPrediction(model: string, text: string): Promise<Job> {
  if (USE_MOCKS) return mockWorld().submit(model, { text });
  const data = await request<{ job: Job }>("/v1/predict", {
    method: "POST",
    body: JSON.stringify({ model, input: { text }, wait_ms: 6000 }),
  });
  return data.job;
}

export async function injectChaos(fault: {
  target: string;
  mode: string;
  probability: number;
  latency_ms: number;
  ttl_s: number;
  note?: string;
}): Promise<ChaosFault> {
  if (USE_MOCKS) return mockWorld().inject(fault);
  return request<ChaosFault>("/v1/chaos", { method: "POST", body: JSON.stringify(fault) }, true);
}

export async function clearChaos(target?: string): Promise<number> {
  if (USE_MOCKS) return mockWorld().clearChaos(target);
  const data = await request<{ cleared: number }>(
    `/v1/chaos${target ? `?target=${target}` : ""}`,
    { method: "DELETE" },
    true,
  );
  return data.cleared;
}

export async function replayDeadLetter(dlqId: number): Promise<void> {
  if (USE_MOCKS) {
    mockWorld().replay(dlqId);
    return;
  }
  await request(`/v1/dlq/${dlqId}/replay`, { method: "POST" }, true);
}

export async function startRun(params: {
  scenario: string;
  rps: number;
  duration_s: number;
  fault_at_s: number;
  fault_duration_s: number;
  concurrency: number;
  model: string;
}): Promise<{ id: string }> {
  if (USE_MOCKS) return mockWorld().startRun(params);
  return request<{ id: string }>("/v1/lab/loadtest", { method: "POST", body: JSON.stringify(params) });
}

/* ------------------------------------------------------- live event feed */
export function subscribeToEvents(onEvent: (event: JobEvent) => void): () => void {
  if (USE_MOCKS) {
    let cursor = mockWorld().lastEventId();
    const timer = setInterval(() => {
      const batch = mockWorld().tailEvents(cursor);
      if (batch.length) {
        cursor = batch[batch.length - 1].id;
        batch.slice(-6).forEach(onEvent);
      }
    }, 900);
    return () => clearInterval(timer);
  }

  const source = new EventSource(`${API_URL}/v1/events`);
  source.addEventListener("job", (message) => {
    try {
      onEvent(JSON.parse((message as MessageEvent).data) as JobEvent);
    } catch {
      /* ignore malformed frame */
    }
  });
  return () => source.close();
}

export { ApiError };
