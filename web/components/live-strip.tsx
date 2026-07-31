"use client";

import { useEffect, useState } from "react";
import clsx from "clsx";
import { getStats } from "@/lib/api";
import type { Stats } from "@/lib/types";
import { ms, num, pct } from "@/lib/format";

export function LiveStrip() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      getStats()
        .then((next) => alive && (setStats(next), setError(false)))
        .catch(() => alive && setError(true));
    load();
    const timer = setInterval(load, 2500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const cells = [
    {
      label: "throughput",
      value: stats ? num(stats.throughput.per_second, 1) : "—",
      unit: "jobs/s",
      tone: "text-signal",
    },
    { label: "p95 end-to-end", value: stats ? ms(stats.latency_ms.p95) : "—", unit: "", tone: "text-white" },
    {
      label: "queue depth",
      value: stats ? num(Math.max(0, stats.queue.depth)) : "—",
      unit: "msgs",
      tone: stats && stats.queue.depth > 400 ? "text-amberline" : "text-white",
    },
    {
      label: "success rate",
      value: stats ? pct(stats.reliability.success_rate_window, 2) : "—",
      unit: "15m",
      tone: "text-signal",
    },
    {
      label: "unaccounted jobs",
      value: stats ? num(stats.reliability.unaccounted_jobs) : "—",
      unit: "invariant",
      tone: stats && stats.reliability.unaccounted_jobs === 0 ? "text-signal" : "text-alarm",
    },
  ];

  return (
    <div className="grid grid-cols-2 divide-white/[0.06] overflow-hidden rounded-xl border border-white/[0.08] bg-ink-950/70 backdrop-blur md:grid-cols-5 md:divide-x">
      {cells.map((cell) => (
        <div key={cell.label} className="border-b border-white/[0.06] px-4 py-3 md:border-b-0">
          <p className="label">{cell.label}</p>
          <p className="mt-1.5 flex items-baseline gap-1.5">
            <span className={clsx("font-mono text-lg font-medium tabular-nums", cell.tone)}>{cell.value}</span>
            {cell.unit ? <span className="font-mono text-[10px] text-slate-500">{cell.unit}</span> : null}
          </p>
        </div>
      ))}
      {error ? (
        <p className="col-span-full border-t border-white/[0.06] px-4 py-2 font-mono text-[11px] text-amberline">
          gateway unreachable — showing last known values
        </p>
      ) : null}
    </div>
  );
}
