"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { ArrowUpRight } from "lucide-react";
import { Mark, Wordmark } from "./brand";
import { API_URL, USE_MOCKS, getStats } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/console", label: "Console" },
  { href: "/resilience", label: "Resilience" },
  { href: "/architecture", label: "Architecture" },
];

export function Nav() {
  const pathname = usePathname();
  const [live, setLive] = useState<"checking" | "live" | "demo" | "offline">("checking");

  useEffect(() => {
    if (USE_MOCKS) {
      setLive("demo");
      return;
    }
    let alive = true;
    getStats()
      .then(() => alive && setLive("live"))
      .catch(() => alive && setLive("offline"));
    return () => {
      alive = false;
    };
  }, []);

  const tone =
    live === "live"
      ? "text-signal"
      : live === "demo"
        ? "text-amberline"
        : live === "offline"
          ? "text-alarm"
          : "text-slate-500";

  return (
    <header className="sticky top-0 z-50 border-b border-white/[0.06] bg-ink-950/85 backdrop-blur-xl">
      <div className="mx-auto flex h-14 max-w-[1240px] items-center gap-6 px-5">
        <Link href="/" className="flex items-center gap-2.5">
          <Mark />
          <Wordmark />
        </Link>

        <nav className="ml-2 hidden items-center gap-1 md:flex">
          {LINKS.map((link) => {
            const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={clsx(
                  "rounded-md px-3 py-1.5 text-sm transition-colors",
                  active ? "bg-white/[0.06] text-white" : "text-slate-400 hover:text-white",
                )}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <span className={clsx("chip border-white/10 bg-white/[0.03]", tone)}>
            <span className={clsx("h-1.5 w-1.5 rounded-full bg-current", live !== "offline" && "animate-pulse-dot")} />
            {live === "live" ? "live api" : live === "demo" ? "demo mode" : live === "offline" ? "api offline" : "…"}
          </span>
          <a
            href={USE_MOCKS ? "https://github.com/shahriarahmedseam/aegisflow" : `${API_URL}/docs`}
            target="_blank"
            rel="noreferrer"
            className="hidden items-center gap-1 font-mono text-[11px] uppercase tracking-label text-slate-400 transition-colors hover:text-white sm:flex"
          >
            {USE_MOCKS ? "source" : "api docs"}
            <ArrowUpRight className="h-3 w-3" />
          </a>
        </div>
      </div>

      <nav className="flex items-center gap-1 overflow-x-auto border-t border-white/[0.05] px-4 py-2 md:hidden">
        {LINKS.map((link) => {
          const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              className={clsx(
                "whitespace-nowrap rounded-md px-3 py-1.5 text-sm",
                active ? "bg-white/[0.06] text-white" : "text-slate-400",
              )}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
