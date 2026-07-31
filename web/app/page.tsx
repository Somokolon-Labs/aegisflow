import Image from "next/image";
import Link from "next/link";
import {
  ArrowRight,
  Boxes,
  CircleSlash2,
  Database,
  GitBranch,
  Layers,
  Radar,
  RefreshCcw,
  ShieldCheck,
  Timer,
  Zap,
} from "lucide-react";
import { FlowDiagram } from "@/components/flow-diagram";
import { LiveStrip } from "@/components/live-strip";
import { Callout, CodeBlock, Panel, SectionHeading } from "@/components/ui";

const HERO_IMAGE =
  "https://images.pexels.com/photos/17489151/pexels-photo-17489151.jpeg?auto=compress&cs=tinysrgb&w=2200&h=1200&fit=crop";
const CHAOS_IMAGE =
  "https://images.pexels.com/photos/13978776/pexels-photo-13978776.jpeg?auto=compress&cs=tinysrgb&w=1400&h=1000&fit=crop";
const CABLE_IMAGE =
  "https://images.pexels.com/photos/4682189/pexels-photo-4682189.jpeg?auto=compress&cs=tinysrgb&w=1400&h=900&fit=crop";

const PILLARS = [
  {
    icon: Database,
    title: "Nothing is lost at the door",
    body:
      "A request is only acknowledged after the job row and its publish intent are committed in the same transaction. If the broker is unavailable the gateway keeps accepting work and the relay drains the outbox when it returns.",
  },
  {
    icon: Boxes,
    title: "Workers are disposable",
    body:
      "Consumers hold leases, not ownership. Kill one mid-flight and the message returns to the queue when the visibility timeout expires. Results, ack and dedupe marker commit together, so a replay can never double-write.",
  },
  {
    icon: Radar,
    title: "Failure is a scheduled test",
    body:
      "The resilience lab drives load while injecting worker loss, broker outages, storage latency and poison payloads, then reports recovery time, availability during the fault and whether a single job went missing.",
  },
];

const GUARANTEES = [
  { invariant: "No accepted job is ever lost", mechanism: "Transactional outbox + durable queue + lease reaper", evidence: "unaccounted_jobs = 0" },
  { invariant: "Duplicate delivery is harmless", mechanism: "Inbox dedupe table; ack inside the result transaction", evidence: "attempts tracked per job" },
  { invariant: "Transient faults self-heal", mechanism: "Exponential backoff with jitter, capped attempts", evidence: "retry events in the audit log" },
  { invariant: "Bad input never blocks the queue", mechanism: "Permanent vs transient error split, dead-letter + replay", evidence: "DLQ depth and replay API" },
  { invariant: "A slow dependency stays contained", mechanism: "Circuit breaker, bulkhead, per-job timeout", evidence: "breaker state in /v1/stats" },
  { invariant: "Degraded beats down", mechanism: "Model fallback path, cache and limiter fail open", evidence: "degraded flag on results" },
];

const SCENARIOS = [
  { key: "worker-loss", title: "Worker fleet loss", detail: "Every consumer stops mid-load. Leases expire, work is requeued, the fleet catches up." },
  { key: "broker-outage", title: "Broker outage", detail: "Publishing fails for ten seconds. Ingest keeps 100% availability from the outbox." },
  { key: "db-slowdown", title: "Storage slowdown", detail: "800ms injected latency. Timeouts and retries absorb it instead of piling up." },
  { key: "poison-payloads", title: "Poison payloads", detail: "A third of model calls fail hard. Retries, then dead-letter, with the queue still moving." },
  { key: "burst", title: "Traffic burst", detail: "3x arrival rate with no fault: pure elasticity and backpressure behaviour." },
];

const STACK = [
  { label: "API + services", value: "FastAPI · asyncio · Pydantic v2" },
  { label: "Messaging", value: "Kafka / Redpanda · Redis Streams · DB queue" },
  { label: "State", value: "Postgres (SQLAlchemy 2 async) · Redis" },
  { label: "Models", value: "scikit-learn pipelines, pluggable runtime" },
  { label: "Observability", value: "Prometheus · Grafana · structured JSON logs" },
  { label: "Delivery", value: "Docker · Kubernetes (HPA/PDB) · GitHub Actions" },
];

export default function LandingPage() {
  return (
    <>
      {/* hero ------------------------------------------------------------ */}
      <section className="relative overflow-hidden border-b border-white/[0.06]">
        <Image
          src={HERO_IMAGE}
          alt="Racks of networking equipment in a dimly lit data centre aisle"
          fill
          priority
          sizes="100vw"
          className="object-cover object-center opacity-[0.28]"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-ink-950/70 via-ink-950/88 to-ink-950" />
        <div className="hairline-grid absolute inset-0 opacity-40" aria-hidden="true" />

        <div className="relative mx-auto max-w-[1240px] px-5 pb-14 pt-20 sm:pt-28">
          <div className="max-w-3xl animate-rise">
            <p className="label">Event-driven ML inference platform</p>
            <h1 className="mt-4 text-balance text-4xl font-semibold leading-[1.06] tracking-tight sm:text-[54px]">
              Inference that survives
              <span className="text-signal"> its own worst day.</span>
            </h1>
            <p className="mt-5 max-w-2xl text-[17px] leading-relaxed text-slate-400">
              AegisFlow accepts prediction requests durably, processes them on an elastic worker fleet and proves
              its fault tolerance on demand. Break the broker, kill the workers, slow the database — then read the
              recovery report.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link href="/console" className="btn-primary">
                Open the console
                <ArrowRight className="h-4 w-4" />
              </Link>
              <Link href="/resilience" className="btn-ghost">
                Run a chaos drill
                <Zap className="h-4 w-4" />
              </Link>
              <Link href="/architecture" className="btn-ghost">
                Architecture
                <Layers className="h-4 w-4" />
              </Link>
            </div>
          </div>

          <div className="mt-12">
            <LiveStrip />
          </div>
        </div>
      </section>

      {/* pillars --------------------------------------------------------- */}
      <section className="mx-auto max-w-[1240px] px-5 py-20">
        <SectionHeading
          eyebrow="Design intent"
          title="Three properties, engineered rather than assumed"
          description="Most inference services fall over in the same three places: the moment of acceptance, the moment a consumer dies, and the moment nobody is watching. Each one has a mechanism here, and each mechanism has a number attached."
        />

        <div className="mt-10 grid gap-4 lg:grid-cols-3">
          {PILLARS.map((pillar) => (
            <article key={pillar.title} className="panel flex flex-col gap-4 p-5">
              <span className="flex h-9 w-9 items-center justify-center rounded-md border border-signal/25 bg-signal/[0.08] text-signal">
                <pillar.icon className="h-4 w-4" strokeWidth={1.6} />
              </span>
              <h3 className="text-[17px] font-medium leading-snug">{pillar.title}</h3>
              <p className="text-[14px] leading-relaxed text-slate-400">{pillar.body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* diagram --------------------------------------------------------- */}
      <section className="border-y border-white/[0.06] bg-ink-950/60">
        <div className="mx-auto max-w-[1240px] px-5 py-20">
          <div className="flex flex-wrap items-end justify-between gap-6">
            <SectionHeading
              eyebrow="Request path"
              title="Seven hops, each one independently recoverable"
              description="Every arrow is a place where the platform can fail. Every box owns exactly one job and can be scaled or restarted without coordinating with the others."
            />
            <Link href="/architecture" className="btn-ghost">
              Full architecture
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>

          <div className="mt-10">
            <FlowDiagram />
          </div>

          <div className="mt-6 grid gap-4 md:grid-cols-3">
            {[
              { icon: ShieldCheck, title: "Accept", body: "Auth, token-bucket limit, schema validation, idempotency key, one commit." },
              { icon: RefreshCcw, title: "Process", body: "Batched claim, bounded concurrency, timeout, retry with jitter, dead-letter." },
              { icon: Timer, title: "Observe", body: "Prometheus metrics, append-only event log streamed to the console over SSE." },
            ].map((item) => (
              <div key={item.title} className="panel-tight flex gap-3 p-4">
                <item.icon className="mt-0.5 h-4 w-4 shrink-0 text-signal" strokeWidth={1.7} />
                <div>
                  <p className="text-sm font-medium text-white">{item.title}</p>
                  <p className="mt-1 text-[13px] leading-relaxed text-slate-400">{item.body}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* guarantees ------------------------------------------------------ */}
      <section className="mx-auto max-w-[1240px] px-5 py-20">
        <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div>
            <SectionHeading
              eyebrow="Invariants"
              title="What the platform promises, and how it keeps the promise"
            />
            <div className="mt-8 overflow-hidden rounded-xl border border-white/[0.07]">
              <table className="w-full text-left text-[13.5px]">
                <thead className="bg-white/[0.03] font-mono text-[10px] uppercase tracking-label text-slate-500">
                  <tr>
                    <th className="px-4 py-2.5 font-normal">Invariant</th>
                    <th className="px-4 py-2.5 font-normal">Mechanism</th>
                    <th className="hidden px-4 py-2.5 font-normal sm:table-cell">Evidence</th>
                  </tr>
                </thead>
                <tbody className="row-divide">
                  {GUARANTEES.map((row) => (
                    <tr key={row.invariant} className="align-top">
                      <td className="px-4 py-3 text-slate-200">{row.invariant}</td>
                      <td className="px-4 py-3 text-slate-400">{row.mechanism}</td>
                      <td className="hidden px-4 py-3 font-mono text-[12px] text-signal/80 sm:table-cell">{row.evidence}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="space-y-4">
            <div className="relative h-44 overflow-hidden rounded-xl border border-white/[0.07]">
              <Image
                src={CABLE_IMAGE}
                alt="Patch panel with neatly routed network cables"
                fill
                sizes="(max-width: 1024px) 100vw, 360px"
                className="object-cover opacity-40"
              />
              <div className="absolute inset-0 bg-gradient-to-t from-ink-950 via-ink-950/60 to-transparent" />
              <div className="absolute bottom-4 left-4 right-4">
                <p className="label">Ingest availability</p>
                <p className="mt-1 font-mono text-2xl text-white">100%</p>
                <p className="text-[12px] text-slate-400">during a full broker outage drill</p>
              </div>
            </div>

            <CodeBlock caption="submit a prediction">
{`curl -X POST $GATEWAY/v1/predict \\
  -H 'X-API-Key: demo-key-aegisflow' \\
  -H 'content-type: application/json' \\
  -d '{
    "model": "sentiment-v1",
    "input": { "text": "courier arrived early" },
    "wait_ms": 3000
  }'`}
            </CodeBlock>

            <Callout>
              202 is returned the instant the job is durable. Poll <span className="font-mono">/v1/jobs/&#123;id&#125;</span>,
              stream <span className="font-mono">/v1/events</span>, or pass <span className="font-mono">wait_ms</span> for a
              synchronous answer.
            </Callout>
          </div>
        </div>
      </section>

      {/* chaos ----------------------------------------------------------- */}
      <section className="border-y border-white/[0.06] bg-ink-950/60">
        <div className="mx-auto grid max-w-[1240px] gap-10 px-5 py-20 lg:grid-cols-[420px_minmax(0,1fr)]">
          <div className="relative overflow-hidden rounded-xl border border-white/[0.07]">
            <Image
              src={CHAOS_IMAGE}
              alt="Operations room with monitoring screens"
              fill
              sizes="(max-width: 1024px) 100vw, 420px"
              className="object-cover opacity-35"
            />
            <div className="absolute inset-0 bg-gradient-to-br from-ink-950/80 via-ink-950/70 to-chaos/10" />
            <div className="relative flex h-full flex-col justify-end gap-4 p-6">
              <span className="chip w-fit border-chaos/40 bg-chaos/10 text-chaos">
                <CircleSlash2 className="h-3 w-3" />
                fault injection
              </span>
              <p className="text-[15px] leading-relaxed text-slate-300">
                Faults live in the database, so every replica of every service picks them up within a second. Drills
                are reproducible, time-boxed and self-clearing.
              </p>
              <dl className="grid grid-cols-2 gap-3 border-t border-white/[0.08] pt-4">
                <div>
                  <dt className="label">recovery</dt>
                  <dd className="mt-1 font-mono text-lg text-signal">≤ 2s</dd>
                </div>
                <div>
                  <dt className="label">lost jobs</dt>
                  <dd className="mt-1 font-mono text-lg text-signal">0</dd>
                </div>
              </dl>
            </div>
          </div>

          <div>
            <SectionHeading
              eyebrow="Resilience lab"
              title="Drills that produce numbers, not adjectives"
              description="Pick a scenario, set the rate, and the lab drives traffic while injecting the fault at a fixed offset. It reconciles every accepted job against every terminal state before it reports."
            />

            <div className="mt-8 overflow-hidden rounded-xl border border-white/[0.07] row-divide">
              {SCENARIOS.map((scenario) => (
                <div key={scenario.key} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-4 py-3.5">
                  <span className="font-mono text-[11px] text-chaos">{scenario.key}</span>
                  <span className="text-sm font-medium text-white">{scenario.title}</span>
                  <span className="w-full text-[13px] leading-relaxed text-slate-400 sm:w-auto sm:flex-1">
                    {scenario.detail}
                  </span>
                </div>
              ))}
            </div>

            <Link href="/resilience" className="btn-chaos mt-6">
              Launch a drill
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </section>

      {/* stack ----------------------------------------------------------- */}
      <section className="mx-auto max-w-[1240px] px-5 py-20">
        <div className="grid gap-10 lg:grid-cols-2">
          <div>
            <SectionHeading
              eyebrow="Implementation"
              title="Boring technology, deliberately arranged"
              description="One image runs four services. The broker is pluggable, so the same code path runs on Kafka in production and on a database-backed queue when you only have one node."
            />
            <dl className="mt-8 overflow-hidden rounded-xl border border-white/[0.07] row-divide">
              {STACK.map((item) => (
                <div key={item.label} className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-3">
                  <dt className="label">{item.label}</dt>
                  <dd className="font-mono text-[12.5px] text-slate-300">{item.value}</dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="space-y-4">
            <Panel title="Deployment paths" hint="pick one">
              <div className="row-divide">
                {[
                  { name: "docker compose", detail: "Postgres + Redpanda + Redis + Prometheus + Grafana + console" },
                  { name: "kubernetes", detail: "kustomize base, HPA on queue depth, PDB, probes, ServiceMonitor" },
                  { name: "single node", detail: "BROKER=db, SQLite or managed Postgres, no message broker required" },
                ].map((item) => (
                  <div key={item.name} className="px-4 py-3">
                    <p className="font-mono text-[12px] text-signal">{item.name}</p>
                    <p className="mt-1 text-[13px] text-slate-400">{item.detail}</p>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title="Repository" hint="what a reviewer will find">
              <div className="row-divide">
                {[
                  { icon: GitBranch, text: "CI that builds the image, trains the models and validates manifests" },
                  { icon: Layers, text: "docs/architecture.md with the request lifecycle and failure matrix" },
                  { icon: Radar, text: "Grafana dashboard provisioned from JSON, alert rules included" },
                ].map((row) => (
                  <div key={row.text} className="flex items-start gap-3 px-4 py-3">
                    <row.icon className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" strokeWidth={1.7} />
                    <p className="text-[13px] leading-relaxed text-slate-400">{row.text}</p>
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        </div>

        <div className="panel mt-10 flex flex-wrap items-center justify-between gap-6 p-6">
          <div>
            <h3 className="text-lg font-medium text-white">Start with the console</h3>
            <p className="mt-1 text-[14px] text-slate-400">
              Submit a request, watch the event log, then break something on purpose.
            </p>
          </div>
          <div className="flex gap-3">
            <Link href="/console" className="btn-primary">
              Console
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link href="/resilience" className="btn-ghost">
              Resilience lab
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
