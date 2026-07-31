import clsx from "clsx";
import type { ReactNode } from "react";

export function SectionHeading({
  eyebrow,
  title,
  description,
  align = "left",
}: {
  eyebrow: string;
  title: string;
  description?: string;
  align?: "left" | "center";
}) {
  return (
    <div className={clsx("max-w-2xl", align === "center" && "mx-auto text-center")}>
      <p className="label">{eyebrow}</p>
      <h2 className="mt-3 text-balance text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h2>
      {description ? <p className="mt-3 text-[15px] leading-relaxed text-slate-400">{description}</p> : null}
    </div>
  );
}

export function Panel({
  children,
  className,
  title,
  hint,
  action,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <section className={clsx("panel", className)}>
      {title ? (
        <header className="flex items-center gap-3 border-b border-white/[0.06] px-4 py-3">
          <h3 className="text-sm font-medium text-white">{title}</h3>
          {hint ? <span className="font-mono text-[11px] text-slate-500">{hint}</span> : null}
          {action ? <div className="ml-auto">{action}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

export function MetricTile({
  label,
  value,
  unit,
  hint,
  tone = "default",
  children,
}: {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
  tone?: "default" | "good" | "warn" | "bad";
  children?: ReactNode;
}) {
  const toneClass = {
    default: "text-white",
    good: "text-signal",
    warn: "text-amberline",
    bad: "text-alarm",
  }[tone];

  return (
    <div className="panel px-4 py-3.5">
      <p className="label">{label}</p>
      <p className="mt-2 flex items-baseline gap-1.5">
        <span className={clsx("font-mono text-[26px] font-medium leading-none tabular-nums", toneClass)}>{value}</span>
        {unit ? <span className="font-mono text-[11px] text-slate-500">{unit}</span> : null}
      </p>
      {hint ? <p className="mt-1.5 text-xs text-slate-500">{hint}</p> : null}
      {children}
    </div>
  );
}

export function StatusPill({ status, children }: { status: string; children?: ReactNode }) {
  const tone: Record<string, string> = {
    succeeded: "text-signal border-signal/30 bg-signal/10",
    healthy: "text-signal border-signal/30 bg-signal/10",
    completed: "text-signal border-signal/30 bg-signal/10",
    closed: "text-signal border-signal/30 bg-signal/10",
    running: "text-sky-300 border-sky-400/30 bg-sky-400/10",
    queued: "text-slate-300 border-white/10 bg-white/[0.04]",
    submitted: "text-slate-300 border-white/10 bg-white/[0.04]",
    retry: "text-amberline border-amberline/30 bg-amberline/10",
    open: "text-amberline border-amberline/30 bg-amberline/10",
    failed: "text-alarm border-alarm/30 bg-alarm/10",
    dlq: "text-alarm border-alarm/40 bg-alarm/10",
    replayed: "text-chaos border-chaos/40 bg-chaos/10",
  };
  return (
    <span className={clsx("chip", tone[status] ?? "text-slate-300 border-white/10 bg-white/[0.04]")}>
      {children ?? status}
    </span>
  );
}

export function KeyValue({ label, value, mono = true }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 py-2.5">
      <span className="text-[13px] text-slate-500">{label}</span>
      <span className={clsx("text-[13px] text-slate-200", mono && "font-mono tabular-nums")}>{value}</span>
    </div>
  );
}

export function Sparkline({
  values,
  height = 44,
  tone = "#4FD1C5",
  fill = true,
}: {
  values: number[];
  height?: number;
  tone?: string;
  fill?: boolean;
}) {
  if (values.length < 2) {
    return <div style={{ height }} className="flex items-end text-[11px] text-slate-600">collecting…</div>;
  }
  const width = 240;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - ((value - min) / span) * (height - 4) - 2;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const gradientId = `spark-${tone.replace("#", "")}`;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ height }} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={tone} stopOpacity="0.35" />
          <stop offset="100%" stopColor={tone} stopOpacity="0" />
        </linearGradient>
      </defs>
      {fill ? (
        <polygon points={`0,${height} ${points.join(" ")} ${width},${height}`} fill={`url(#${gradientId})`} />
      ) : null}
      <polyline points={points.join(" ")} fill="none" stroke={tone} strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

export function BarMeter({
  value,
  max,
  tone = "bg-signal",
  label,
}: {
  value: number;
  max: number;
  tone?: string;
  label?: string;
}) {
  const width = Math.max(2, Math.min(100, (value / Math.max(1, max)) * 100));
  return (
    <div className="space-y-1">
      {label ? <p className="font-mono text-[11px] text-slate-500">{label}</p> : null}
      <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
        <div className={clsx("h-full rounded-full transition-all duration-500", tone)} style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

export function CodeBlock({ children, caption }: { children: string; caption?: string }) {
  return (
    <figure className="overflow-hidden rounded-lg border border-white/[0.07] bg-ink-900/70">
      {caption ? (
        <figcaption className="border-b border-white/[0.06] px-3.5 py-2 font-mono text-[11px] uppercase tracking-label text-slate-500">
          {caption}
        </figcaption>
      ) : null}
      <pre className="scroll-slim overflow-x-auto px-3.5 py-3 font-mono text-[12.5px] leading-relaxed text-slate-300">
        {children}
      </pre>
    </figure>
  );
}

export function Callout({ tone = "signal", children }: { tone?: "signal" | "chaos" | "warn"; children: ReactNode }) {
  const map = {
    signal: "border-signal/25 bg-signal/[0.06] text-signal-soft",
    chaos: "border-chaos/25 bg-chaos/[0.06] text-chaos",
    warn: "border-amberline/25 bg-amberline/[0.06] text-amberline",
  }[tone];
  return <div className={clsx("rounded-lg border px-4 py-3 text-[13px] leading-relaxed", map)}>{children}</div>;
}
