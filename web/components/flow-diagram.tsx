type Node = {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  lines: string[];
  accent?: "signal" | "chaos" | "amber" | "slate";
};

const NODES: Node[] = [
  { id: "client", x: 8, y: 150, w: 132, h: 78, title: "Clients", lines: ["REST · batch", "API key + idempotency"] },
  {
    id: "gateway",
    x: 186,
    y: 132,
    w: 168,
    h: 114,
    title: "Gateway",
    lines: ["auth · rate limit", "validate · enqueue", "SSE · admin API"],
    accent: "signal",
  },
  { id: "store", x: 404, y: 34, w: 168, h: 100, title: "Postgres", lines: ["jobs · outbox", "inbox · dead letters"] },
  {
    id: "broker",
    x: 404, y: 214, w: 168, h: 108,
    title: "Broker",
    lines: ["inference.requests", "retry · dlq topics", "kafka | redis | db"],
    accent: "amber",
  },
  { id: "relay", x: 620, y: 34, w: 156, h: 100, title: "Relay", lines: ["outbox drain", "lease reaper", "janitor"] },
  {
    id: "workers",
    x: 620, y: 214, w: 156, h: 108,
    title: "Worker fleet",
    lines: ["bulkhead · timeout", "retry + backoff", "model runtime"],
    accent: "signal",
  },
  { id: "results", x: 828, y: 126, w: 164, h: 126, title: "Results", lines: ["atomic commit", "cache-aside", "live event log"] },
  { id: "observe", x: 404, y: 356, w: 372, h: 52, title: "Prometheus + Grafana", lines: ["metrics from every service"], accent: "slate" },
  { id: "chaos", x: 828, y: 320, w: 164, h: 88, title: "Resilience lab", lines: ["load generator", "fault injection"], accent: "chaos" },
];

const ACCENT = {
  signal: { stroke: "rgba(79,209,197,0.55)", fill: "rgba(79,209,197,0.06)", text: "#7FE3DA" },
  chaos: { stroke: "rgba(179,137,247,0.55)", fill: "rgba(179,137,247,0.06)", text: "#C6A6FA" },
  amber: { stroke: "rgba(240,180,41,0.5)", fill: "rgba(240,180,41,0.05)", text: "#F5C765" },
  slate: { stroke: "rgba(255,255,255,0.14)", fill: "rgba(255,255,255,0.02)", text: "#94A3B8" },
} as const;

const PATHS: { id: string; d: string; label?: string; dashed?: boolean; tone?: keyof typeof ACCENT; flow?: boolean }[] = [
  { id: "p1", d: "M140 189 H186", flow: true },
  { id: "p2", d: "M354 168 C 380 168 380 84 404 84", label: "commit job + intent", flow: true },
  { id: "p3", d: "M572 84 H620", flow: true },
  { id: "p4", d: "M698 134 C 698 170 498 172 488 214", label: "publish", flow: true },
  { id: "p5", d: "M572 268 H620", flow: true },
  { id: "p6", d: "M776 268 C 806 268 806 200 828 190", label: "result + ack", flow: true },
  { id: "p7", d: "M698 322 C 698 350 520 344 512 322", label: "retry / dlq", dashed: true, tone: "amber" },
  { id: "p8", d: "M910 126 C 910 60 700 12 260 46 C 200 52 196 110 200 132", label: "live stream", dashed: true },
  { id: "p9", d: "M354 246 C 372 300 384 340 404 356", dashed: true, tone: "slate" },
  { id: "p10", d: "M698 322 V 356", dashed: true, tone: "slate" },
  { id: "p11", d: "M828 364 C 700 384 560 384 490 330", label: "inject", dashed: true, tone: "chaos" },
  { id: "p12", d: "M872 320 C 760 250 500 210 270 200", dashed: true, tone: "chaos" },
];

export function FlowDiagram({ compact = false }: { compact?: boolean }) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-white/[0.07] bg-ink-900/40">
      <div className="hairline-grid absolute inset-0 opacity-[0.5]" aria-hidden="true" />
      <svg
        viewBox="0 0 1000 420"
        className="relative w-full"
        style={{ maxHeight: compact ? 300 : 460 }}
        role="img"
        aria-label="AegisFlow request path: clients, gateway, Postgres outbox, relay, broker, worker fleet, results, observability and the resilience lab."
      >
        {PATHS.map((path) => {
          const tone = ACCENT[path.tone ?? "signal"];
          return (
            <g key={path.id}>
              <path
                id={path.id}
                d={path.d}
                fill="none"
                stroke={tone.stroke}
                strokeWidth="1.15"
                strokeDasharray={path.dashed ? "4 5" : undefined}
              />
              {path.flow ? (
                <circle r="2.6" fill={tone.text}>
                  <animateMotion dur="3.4s" repeatCount="indefinite" keyPoints="0;1" keyTimes="0;1" calcMode="linear">
                    <mpath href={`#${path.id}`} />
                  </animateMotion>
                </circle>
              ) : null}
            </g>
          );
        })}

        {NODES.map((node) => {
          const tone = ACCENT[node.accent ?? "slate"];
          return (
            <g key={node.id}>
              <rect
                x={node.x}
                y={node.y}
                width={node.w}
                height={node.h}
                rx="9"
                fill={tone.fill}
                stroke={tone.stroke}
                strokeWidth="1"
              />
              <text x={node.x + 13} y={node.y + 24} fill="#F1F5F9" fontSize="13.5" fontWeight="560">
                {node.title}
              </text>
              {node.lines.map((line, index) => (
                <text
                  key={line}
                  x={node.x + 13}
                  y={node.y + 44 + index * 16}
                  fill={tone.text}
                  fontSize="10.5"
                  fontFamily="ui-monospace, monospace"
                  opacity="0.92"
                >
                  {line}
                </text>
              ))}
            </g>
          );
        })}

        {PATHS.filter((path) => path.label).map((path) => (
          <text key={`${path.id}-label`} fill="#64748B" fontSize="9.5" fontFamily="ui-monospace, monospace">
            <textPath href={`#${path.id}`} startOffset="42%">
              {path.label}
            </textPath>
          </text>
        ))}
      </svg>
    </div>
  );
}
