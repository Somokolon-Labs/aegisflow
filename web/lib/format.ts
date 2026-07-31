export function num(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1000) return `${(value / 1000).toFixed(2)}s`;
  if (value >= 100) return `${Math.round(value)}ms`;
  return `${value.toFixed(1)}ms`;
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const delta = (Date.now() - new Date(iso).getTime()) / 1000;
  if (delta < 2) return "just now";
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  return `${Math.floor(delta / 3600)}h ago`;
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "--:--:--";
  return new Date(iso).toLocaleTimeString("en-GB", { hour12: false });
}

export function shortId(value: string | null | undefined, keep = 6): string {
  if (!value) return "—";
  const parts = value.split("_");
  const tail = parts.length > 1 ? parts[parts.length - 1] : value;
  return `${parts.length > 1 ? `${parts[0]}·` : ""}${tail.slice(0, keep)}`;
}

export const STATUS_TONE: Record<string, string> = {
  succeeded: "text-signal border-signal/30 bg-signal/10",
  running: "text-sky-300 border-sky-400/30 bg-sky-400/10",
  queued: "text-slate-300 border-white/15 bg-white/5",
  submitted: "text-slate-300 border-white/15 bg-white/5",
  retry: "text-amberline border-amberline/30 bg-amberline/10",
  failed: "text-alarm border-alarm/30 bg-alarm/10",
  dlq: "text-alarm border-alarm/40 bg-alarm/10",
  replayed: "text-chaos border-chaos/40 bg-chaos/10",
};
