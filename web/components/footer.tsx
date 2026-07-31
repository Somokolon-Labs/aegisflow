import Link from "next/link";
import { Mark } from "./brand";

export function Footer() {
  return (
    <footer className="mt-24 border-t border-white/[0.06] bg-ink-950">
      <div className="mx-auto grid max-w-[1240px] gap-10 px-5 py-12 sm:grid-cols-2 lg:grid-cols-4">
        <div className="space-y-3">
          <div className="flex items-center gap-2.5">
            <Mark className="h-6 w-6" />
            <span className="text-sm font-semibold text-white">AegisFlow</span>
          </div>
          <p className="max-w-xs text-sm leading-relaxed text-slate-500">
            Event-driven ML inference platform with a durable ingest path, an elastic worker fleet and
            fault tolerance proven by scheduled chaos drills.
          </p>
        </div>

        <div className="space-y-2">
          <p className="label">Console</p>
          {[
            { href: "/console", label: "Operations" },
            { href: "/resilience", label: "Resilience lab" },
            { href: "/architecture", label: "Architecture" },
          ].map((item) => (
            <Link key={item.href} href={item.href} className="block text-sm link-quiet">
              {item.label}
            </Link>
          ))}
        </div>

        <div className="space-y-2">
          <p className="label">Platform</p>
          <p className="text-sm text-slate-400">FastAPI · Kafka / Redpanda</p>
          <p className="text-sm text-slate-400">Postgres · Redis · Prometheus</p>
          <p className="text-sm text-slate-400">Docker · Kubernetes · GitHub Actions</p>
        </div>

        <div className="space-y-2">
          <p className="label">Credits</p>
          <p className="text-sm text-slate-500">
            Built by <span className="text-slate-300">Shahriar Ahmed Seam</span> — Somokolon Labs.
          </p>
          <p className="text-xs leading-relaxed text-slate-600">
            Photography by Panumas Nikhomkhai, Brett Sayles and Tom de Monteiller on{" "}
            <a href="https://www.pexels.com" target="_blank" rel="noreferrer" className="underline decoration-white/20 hover:text-slate-400">
              Pexels
            </a>
            .
          </p>
        </div>
      </div>

      <div className="border-t border-white/[0.05]">
        <div className="mx-auto flex max-w-[1240px] flex-col gap-2 px-5 py-5 font-mono text-[11px] uppercase tracking-label text-slate-600 sm:flex-row sm:items-center sm:justify-between">
          <span>AegisFlow v1.0.0 — MIT licensed</span>
          <span>Zero data loss · chaos verified · p99 tracked</span>
        </div>
      </div>
    </footer>
  );
}
