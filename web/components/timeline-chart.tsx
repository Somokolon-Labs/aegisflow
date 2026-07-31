"use client";

type Series = { label: string; tone: string; points: { t: number; value: number }[] };

export function TimelineChart({
  series,
  faultFrom,
  faultTo,
  duration,
  unit = "per second",
}: {
  series: Series[];
  faultFrom?: number;
  faultTo?: number;
  duration: number;
  unit?: string;
}) {
  const width = 780;
  const height = 220;
  const padding = { top: 16, right: 14, bottom: 26, left: 38 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;
  const maxT = Math.max(duration, ...series.flatMap((s) => s.points.map((p) => p.t)), 1);
  const maxValue = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.value)));

  const x = (t: number) => padding.left + (t / maxT) * innerW;
  const y = (value: number) => padding.top + innerH - (value / maxValue) * innerH;

  const gridValues = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(maxValue * f));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-4">
        {series.map((s) => (
          <span key={s.label} className="flex items-center gap-2 font-mono text-[11px] text-slate-400">
            <span className="h-0.5 w-4 rounded" style={{ background: s.tone }} />
            {s.label}
          </span>
        ))}
        {faultFrom !== undefined && faultTo !== undefined && faultTo > faultFrom ? (
          <span className="flex items-center gap-2 font-mono text-[11px] text-chaos">
            <span className="h-2.5 w-4 rounded-sm border border-chaos/50 bg-chaos/15" />
            fault window
          </span>
        ) : null}
        <span className="ml-auto font-mono text-[11px] text-slate-600">{unit}</span>
      </div>

      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Drill timeline">
        {gridValues.map((value) => (
          <g key={value}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={y(value)}
              y2={y(value)}
              stroke="rgba(255,255,255,0.06)"
              strokeWidth="1"
            />
            <text x={padding.left - 8} y={y(value) + 3.5} textAnchor="end" fill="#475569" fontSize="9.5" fontFamily="ui-monospace, monospace">
              {value}
            </text>
          </g>
        ))}

        {faultFrom !== undefined && faultTo !== undefined && faultTo > faultFrom ? (
          <g>
            <rect
              x={x(faultFrom)}
              y={padding.top}
              width={Math.max(2, x(faultTo) - x(faultFrom))}
              height={innerH}
              fill="rgba(179,137,247,0.12)"
              stroke="rgba(179,137,247,0.35)"
              strokeDasharray="3 4"
            />
            <text x={x(faultFrom) + 5} y={padding.top + 12} fill="#C6A6FA" fontSize="9.5" fontFamily="ui-monospace, monospace">
              fault
            </text>
          </g>
        ) : null}

        {series.map((s) => {
          if (s.points.length < 2) return null;
          const path = s.points
            .map((point, index) => `${index === 0 ? "M" : "L"}${x(point.t).toFixed(1)} ${y(point.value).toFixed(1)}`)
            .join(" ");
          const area = `${path} L${x(s.points[s.points.length - 1].t).toFixed(1)} ${y(0)} L${x(s.points[0].t).toFixed(1)} ${y(0)} Z`;
          return (
            <g key={s.label}>
              <path d={area} fill={s.tone} opacity="0.09" />
              <path d={path} fill="none" stroke={s.tone} strokeWidth="1.8" strokeLinejoin="round" />
            </g>
          );
        })}

        <line
          x1={padding.left}
          x2={width - padding.right}
          y1={y(0)}
          y2={y(0)}
          stroke="rgba(255,255,255,0.14)"
          strokeWidth="1"
        />
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const t = Math.round(maxT * f);
          return (
            <text
              key={t}
              x={x(t)}
              y={height - 8}
              textAnchor="middle"
              fill="#475569"
              fontSize="9.5"
              fontFamily="ui-monospace, monospace"
            >
              {t}s
            </text>
          );
        })}
      </svg>
    </div>
  );
}
