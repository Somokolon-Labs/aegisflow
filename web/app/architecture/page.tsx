import Image from "next/image";
import Link from "next/link";
import { ArrowRight, Boxes, Database, GitCompare, Network, ShieldAlert, Workflow } from "lucide-react";
import { FlowDiagram } from "@/components/flow-diagram";
import { Callout, CodeBlock, KeyValue, Panel, SectionHeading } from "@/components/ui";

export const metadata = {
  title: "Architecture",
  description:
    "Request lifecycle, delivery guarantees, failure matrix, scaling model and deployment topology for the AegisFlow inference platform.",
};

const RACK_IMAGE =
  "https://images.pexels.com/photos/37730212/pexels-photo-37730212.jpeg?auto=compress&cs=tinysrgb&w=1600&h=900&fit=crop";

const LIFECYCLE = [
  {
    step: "01",
    title: "Admission",
    body:
      "API key check, token-bucket rate limit (Redis when present, in-process when not), payload validation and an optional idempotency key that collapses retries onto one job.",
  },
  {
    step: "02",
    title: "Durable enqueue",
    body:
      "One transaction writes the job row, the audit event and the outbox intent. With the DB broker the queue insert joins that same transaction; with Kafka the publish happens straight after the commit and the relay covers failures.",
  },
  {
    step: "03",
    title: "Claim",
    body:
      "Workers claim a batch with a single atomic UPDATE stamped with a lease. No advisory locks, so it behaves the same on Postgres and SQLite. Kafka mode uses consumer groups with manual commits.",
  },
  {
    step: "04",
    title: "Compute",
    body:
      "The model runs in a thread with a per-job timeout inside a bulkhead that caps concurrency. Missing artifacts fall back to a heuristic and mark the result degraded instead of failing.",
  },
  {
    step: "05",
    title: "Commit",
    body:
      "Result, audit event, dedupe marker and (for the DB broker) the queue ack land in one transaction. Kafka commits the offset after the write, with the dedupe table absorbing replays.",
  },
  {
    step: "06",
    title: "Fan-out",
    body:
      "The append-only event log is tailed over SSE, results are cached, and Prometheus scrapes every service. Failures land in the DLQ with a replay endpoint.",
  },
];

const FAILURES = [
  { fault: "Worker killed mid-job", detection: "lease expiry", response: "reaper requeues the message", loss: "none, job is recomputed" },
  { fault: "Broker unavailable", detection: "publish breaker opens", response: "outbox accumulates, relay drains on recovery", loss: "none, ingest stays up" },
  { fault: "Database slow", detection: "per-job timeout", response: "retry with backoff, breaker sheds load", loss: "none, latency rises" },
  { fault: "Poison payload", detection: "permanent error class", response: "fail fast, no retry, DLQ entry", loss: "isolated to that job" },
  { fault: "Duplicate delivery", detection: "inbox dedupe key", response: "ack without reprocessing", loss: "no double write" },
  { fault: "Redis down", detection: "connection error", response: "cache and limiter fail open to in-process", loss: "none, capacity guard weakens" },
  { fault: "Traffic spike", detection: "queue depth metric", response: "HPA scales workers, backpressure via bounded prefetch", loss: "none, queue absorbs" },
];

const SCALING = [
  { label: "Gateway", value: "stateless · CPU bound · scale on RPS" },
  { label: "Workers", value: "stateless · scale on queue depth (HPA / KEDA)" },
  { label: "Relay", value: "single logical owner · leader-safe idempotent writes" },
  { label: "Postgres", value: "vertical + read replicas for the console" },
  { label: "Broker", value: "partitions = max worker parallelism" },
];

export default function ArchitecturePage() {
  return (
    <div className="mx-auto max-w-[1240px] px-5 py-10">
      <header className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_360px]">
        <SectionHeading
          eyebrow="Architecture"
          title="Four services, one image, three interchangeable brokers"
          description="AegisFlow is deliberately small: a gateway that never loses an accepted request, workers that are safe to kill, a relay that repairs the gaps, and a lab that proves all of it. Everything else is configuration."
        />
        <div className="relative h-44 overflow-hidden rounded-xl border border-white/[0.07] lg:h-full">
          <Image src={RACK_IMAGE} alt="Server racks with status lights" fill sizes="(max-width: 1024px) 100vw, 360px" className="object-cover opacity-30" />
          <div className="absolute inset-0 bg-gradient-to-t from-ink-950 via-ink-950/50 to-transparent" />
          <dl className="absolute bottom-4 left-4 right-4 grid grid-cols-2 gap-3">
            <div>
              <dt className="label">services</dt>
              <dd className="mt-1 font-mono text-lg text-white">4</dd>
            </div>
            <div>
              <dt className="label">broker backends</dt>
              <dd className="mt-1 font-mono text-lg text-white">3</dd>
            </div>
          </dl>
        </div>
      </header>

      <div className="mt-10">
        <FlowDiagram />
      </div>

      {/* lifecycle -------------------------------------------------------- */}
      <section className="mt-16">
        <SectionHeading eyebrow="Request lifecycle" title="What happens between 202 and the result" />
        <div className="mt-8 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {LIFECYCLE.map((item) => (
            <article key={item.step} className="panel p-5">
              <p className="font-mono text-[11px] text-signal">{item.step}</p>
              <h3 className="mt-2 text-[15.5px] font-medium">{item.title}</h3>
              <p className="mt-2 text-[13.5px] leading-relaxed text-slate-400">{item.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* guarantees ------------------------------------------------------- */}
      <section className="mt-16 grid gap-3 lg:grid-cols-2">
        <Panel title="Delivery semantics" hint="per broker backend">
          <dl className="row-divide">
            <KeyValue label="db queue" value="exactly-once (ack in result txn)" />
            <KeyValue label="kafka / redpanda" value="effectively-once (offset + inbox)" />
            <KeyValue label="redis streams" value="effectively-once (XACK + inbox)" />
            <KeyValue label="ordering" value="per job key, partition-scoped" />
            <KeyValue label="max attempts" value="4, exponential backoff + jitter" />
            <KeyValue label="visibility timeout" value="45s (configurable)" />
          </dl>
        </Panel>

        <Panel title="Scaling model">
          <dl className="row-divide">
            {SCALING.map((row) => (
              <KeyValue key={row.label} label={row.label} value={row.value} mono={false} />
            ))}
          </dl>
        </Panel>
      </section>

      {/* failure matrix --------------------------------------------------- */}
      <section className="mt-16">
        <SectionHeading
          eyebrow="Failure matrix"
          title="Every fault has a detector and a response"
          description="These are the exact behaviours the resilience lab exercises. If one of them regresses, a drill fails and the verdict says so."
        />
        <div className="mt-8 overflow-hidden rounded-xl border border-white/[0.07]">
          <table className="w-full text-left text-[13px]">
            <thead className="bg-white/[0.03] font-mono text-[10px] uppercase tracking-label text-slate-500">
              <tr>
                <th className="px-4 py-2.5 font-normal">Fault</th>
                <th className="px-4 py-2.5 font-normal">Detection</th>
                <th className="px-4 py-2.5 font-normal">Response</th>
                <th className="hidden px-4 py-2.5 font-normal md:table-cell">Data loss</th>
              </tr>
            </thead>
            <tbody className="row-divide">
              {FAILURES.map((row) => (
                <tr key={row.fault} className="align-top">
                  <td className="px-4 py-3 text-slate-200">{row.fault}</td>
                  <td className="px-4 py-3 font-mono text-[12px] text-amberline/85">{row.detection}</td>
                  <td className="px-4 py-3 text-slate-400">{row.response}</td>
                  <td className="hidden px-4 py-3 font-mono text-[12px] text-signal/85 md:table-cell">{row.loss}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* decisions -------------------------------------------------------- */}
      <section className="mt-16 grid gap-3 lg:grid-cols-3">
        {[
          {
            icon: Database,
            title: "Why an outbox instead of publishing first",
            body:
              "Publishing before committing can acknowledge work that never persists; committing before publishing can persist work nobody consumes. The outbox makes the two atomic and turns broker downtime into latency rather than loss.",
          },
          {
            icon: GitCompare,
            title: "Why the ack sits in the result transaction",
            body:
              "With the DB broker the queue lives in the same database as the result, so the ack can be part of the same commit. That upgrades at-least-once to exactly-once without a distributed transaction.",
          },
          {
            icon: Boxes,
            title: "Why one image for four services",
            body:
              "Shared configuration, one build to verify, one artifact to promote. Each service is a different command over the same code, which keeps drift between them impossible.",
          },
          {
            icon: Network,
            title: "Why a pluggable broker",
            body:
              "Kafka is right for production throughput and wrong for a 512MB demo box. The same code path runs on Kafka, Redis Streams or a database-backed queue, chosen by one environment variable.",
          },
          {
            icon: ShieldAlert,
            title: "Why chaos lives in the database",
            body:
              "Faults need to reach every replica of every service within a second, survive restarts and expire on their own. A table with a TTL does all three with no extra moving parts.",
          },
          {
            icon: Workflow,
            title: "Why the input rides in the envelope",
            body:
              "Carrying the payload in the message removes a read before every computation, cutting the hot path to a single write transaction per job and roughly doubling throughput.",
          },
        ].map((item) => (
          <article key={item.title} className="panel p-5">
            <item.icon className="h-4 w-4 text-signal" strokeWidth={1.6} />
            <h3 className="mt-3 text-[15px] font-medium leading-snug">{item.title}</h3>
            <p className="mt-2 text-[13.5px] leading-relaxed text-slate-400">{item.body}</p>
          </article>
        ))}
      </section>

      {/* api -------------------------------------------------------------- */}
      <section className="mt-16 grid gap-3 lg:grid-cols-2">
        <div className="space-y-3">
          <SectionHeading eyebrow="API surface" title="Everything the console does, you can do with curl" />
          <CodeBlock caption="core endpoints">
{`POST   /v1/predict            submit one job (wait_ms optional)
POST   /v1/predict/batch      up to 500 inputs
GET    /v1/jobs/{id}          job state + result
GET    /v1/jobs?status=dlq    filtered listing
GET    /v1/events             SSE tail of the audit log
GET    /v1/stats              throughput, latency, queue, workers
GET    /v1/models             registry + training metrics
GET    /v1/dlq                dead letters
POST   /v1/dlq/{id}/replay    requeue one (admin key)
POST   /v1/chaos              inject a fault (admin key)
DELETE /v1/chaos              clear faults (admin key)
POST   /v1/lab/loadtest       start a drill
GET    /v1/lab/report         best numbers per scenario
GET    /metrics               Prometheus exposition`}
          </CodeBlock>
        </div>

        <div className="space-y-3">
          <Panel title="Local run" hint="no Docker required">
            <div className="p-4">
              <CodeBlock caption="windows powershell">
{`.\\scripts\\dev.ps1 setup
.\\scripts\\dev.ps1 up
.\\scripts\\dev.ps1 smoke
.\\scripts\\dev.ps1 drill -Scenario worker-loss`}
              </CodeBlock>
            </div>
          </Panel>

          <Panel title="Full stack" hint="compose">
            <div className="p-4">
              <CodeBlock caption="postgres + redpanda + redis + prometheus + grafana">
{`docker compose up -d --build
# console   http://localhost:3000
# gateway   http://localhost:8000/docs
# grafana   http://localhost:3001`}
              </CodeBlock>
            </div>
          </Panel>

          <Callout>
            Kubernetes manifests live in <span className="font-mono">deploy/k8s</span> with probes, HPA, PodDisruptionBudget
            and a ServiceMonitor. <span className="font-mono">kubectl apply -k deploy/k8s</span>.
          </Callout>
        </div>
      </section>

      <div className="panel mt-16 flex flex-wrap items-center justify-between gap-6 p-6">
        <div>
          <h3 className="text-lg font-medium text-white">See the invariants hold under fire</h3>
          <p className="mt-1 text-[14px] text-slate-400">Run a drill and compare the verdict against this page.</p>
        </div>
        <Link href="/resilience" className="btn-primary">
          Resilience lab
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
    </div>
  );
}
