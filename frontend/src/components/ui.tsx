// Shared chrome for the frosted-glass SAAS shell that floats over the app gradient.

export const PANE =
  "flex min-h-0 flex-col overflow-hidden rounded-2xl border border-white/50 bg-white/75 shadow-[0_10px_40px_-12px_rgba(80,20,40,0.35)] backdrop-blur-xl";

export function IconPanel({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  );
}

export function IconChevronLeft({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}

export function IconTrash({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m2 0v12a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V7m4 4v6m4-6v6" />
    </svg>
  );
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg className={`${className} animate-spin text-amber-500`} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function IconStop({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
      <rect x="6" y="6" width="12" height="12" rx="2.5" />
    </svg>
  );
}

export function IconPencil({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

export function IconPin({ className = "h-4 w-4", filled = false }: { className?: string; filled?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M9 4h6l-1 5 3 3v2H7v-2l3-3-1-5Z" />
      <path d="M12 14v6" />
    </svg>
  );
}

export function IconFolder({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
    </svg>
  );
}

export function IconPlus({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

/** Collapse toggle shown at the top of an expanded pane. */
export function CollapseButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      title={`Minimize ${label}`}
      className="rounded-lg p-1.5 text-gray-500 transition hover:bg-black/5 hover:text-gray-800"
    >
      <IconChevronLeft />
    </button>
  );
}

/** Thin rail rendered in place of a collapsed pane; click anywhere to reopen. */
export function Rail({ label, onExpand }: { label: string; onExpand: () => void }) {
  return (
    <button
      onClick={onExpand}
      title={`Open ${label}`}
      className={`${PANE} group w-full cursor-pointer items-center gap-3 py-3 transition hover:bg-white/85`}
    >
      <span className="rounded-lg p-1.5 text-gray-600 group-hover:text-gray-900">
        <IconPanel />
      </span>
      <span
        className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 group-hover:text-gray-800"
        style={{ writingMode: "vertical-rl" }}
      >
        {label}
      </span>
    </button>
  );
}

/** Prev/next footer. Renders nothing when everything fits on one page. */
export function Pager({
  offset,
  limit,
  total,
  onPage,
}: {
  offset: number;
  limit: number;
  total: number;
  onPage: (offset: number) => void;
}) {
  if (total <= limit && offset === 0) return null;
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + limit, total);
  const btn =
    "rounded-md border border-white/60 bg-white/60 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-white disabled:opacity-40 disabled:hover:bg-white/60";
  return (
    <div className="flex items-center justify-between gap-2 border-t border-white/50 px-3 py-2 text-xs text-gray-500">
      <span>
        {start}–{end} of {total}
      </span>
      <div className="flex gap-1.5">
        <button disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - limit))} className={btn}>
          Prev
        </button>
        <button disabled={end >= total} onClick={() => onPage(offset + limit)} className={btn}>
          Next
        </button>
      </div>
    </div>
  );
}
