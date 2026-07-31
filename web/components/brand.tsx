export function Mark({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="aegis-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#7FE3DA" />
          <stop offset="100%" stopColor="#159C90" />
        </linearGradient>
      </defs>
      <path
        d="M16 2.5 27 6.6v9.1c0 6.6-4.3 11.6-11 13.8-6.7-2.2-11-7.2-11-13.8V6.6L16 2.5Z"
        fill="none"
        stroke="url(#aegis-mark)"
        strokeWidth="1.6"
      />
      <path d="M9.6 16.2h4.1l2.1-4.4 2.3 8 2-3.6h2.6" fill="none" stroke="#7FE3DA" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="flex items-baseline gap-2">
      <span className="text-[15px] font-semibold tracking-tight text-white">AegisFlow</span>
      <span className="hidden font-mono text-[10px] uppercase tracking-label text-slate-500 sm:inline">
        inference platform
      </span>
    </span>
  );
}
