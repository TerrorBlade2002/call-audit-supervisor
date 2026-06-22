import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";

// "Export" button → dropdown of the portfolio's checklists. Each row downloads a merged CSV
// of every call judged under that checklist (call id · agent · completed · per-item verdicts).
export function ExportMenu({ portfolioId }: { portfolioId: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const { data: checklists = [] } = useQuery({
    queryKey: ["checklists", portfolioId],
    queryFn: () => api.listChecklists(portfolioId),
    enabled: open,
  });

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const download = async (cid: string, name: string) => {
    setBusy(cid);
    setErr(null);
    try {
      await api.downloadChecklistCsv(portfolioId, cid, `${name}.csv`);
    } catch (e) {
      setErr(e instanceof ApiError ? `Export failed (${e.status})` : "Export failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title="Download the full checklist results — every call, all batches"
        className="rounded-lg border border-white/50 bg-white/40 px-2.5 py-1 hover:bg-white/70"
      >
        All-calls CSV ▾
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-1 w-72 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl">
          <div className="border-b border-gray-100 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
            Full checklist results · every call
          </div>
          {checklists.map((c) => (
            <button
              key={c.id}
              disabled={busy === c.id}
              onClick={() => download(c.id, c.name)}
              className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left text-sm hover:bg-gray-50 disabled:opacity-50"
            >
              <span className="min-w-0">
                <span className="block truncate font-medium text-ink">{c.name}</span>
                {c.updated_at && (
                  <span className="block text-xs text-gray-400">
                    v{c.version} · {new Date(c.updated_at).toLocaleDateString()}
                  </span>
                )}
              </span>
              <span className="shrink-0 text-xs font-medium text-[#c0567f]">
                {busy === c.id ? "…" : "Download"}
              </span>
            </button>
          ))}
          {checklists.length === 0 && (
            <p className="px-3 py-3 text-sm text-gray-400">No checklists yet.</p>
          )}
          {err && <p className="border-t border-gray-100 px-3 py-2 text-xs text-red-600">{err}</p>}
        </div>
      )}
    </div>
  );
}
