import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import { PortfolioPicker } from "./PortfolioPicker";
import { IconTrash } from "./ui";

export function KbPanel({ portfolioId, onClose }: { portfolioId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  // Which portfolios the document(s) will be uploaded to — defaults to the one being viewed.
  const [targets, setTargets] = useState<Set<string>>(() => new Set([portfolioId]));

  const { data: docs = [], isLoading } = useQuery({
    queryKey: ["kb", portfolioId],
    queryFn: () => api.listKb(portfolioId),
  });

  // Upload the same file(s) to EACH selected portfolio (fan-out), so a doc is available only on
  // the portfolios chosen here. Each call is authorized server-side per portfolio.
  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const ids = [...targets];
      const results = await Promise.allSettled(ids.map((pid) => api.uploadKb(pid, files)));
      const ok = ids.filter((_, i) => results[i].status === "fulfilled");
      const failed = ids.filter((_, i) => results[i].status === "rejected");
      const firstErr = results.find((r) => r.status === "rejected") as
        | PromiseRejectedResult
        | undefined;
      return { ok, failed, files: files.length, firstErr: firstErr?.reason };
    },
    onSuccess: ({ ok, failed, files, firstErr }) => {
      ok.forEach((pid) => qc.invalidateQueries({ queryKey: ["kb", pid] }));
      if (failed.length === 0) {
        setMsg(`Added ${files} document(s) to ${ok.length} portfolio(s).`);
        setAddOpen(false);
      } else {
        const why = firstErr instanceof ApiError ? ` (${firstErr.status})` : "";
        setMsg(
          `Added to ${ok.length} portfolio(s); ${failed.length} failed${why}. ` +
            "You may lack manage rights on the failed portfolio(s).",
        );
      }
    },
    onError: (e) =>
      setMsg(e instanceof ApiError ? `Upload failed (${e.status}).` : "Upload failed."),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteKb(portfolioId, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["kb", portfolioId] }),
  });

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <button onClick={onClose} className="text-sm font-medium text-[#c0567f] hover:underline">
          ← Back to calls
        </button>
        <h2 className="text-lg font-semibold text-ink">Knowledge Base</h2>
        <button
          onClick={() => {
            setTargets(new Set([portfolioId]));
            setMsg(null);
            setAddOpen((v) => !v);
          }}
          disabled={upload.isPending}
          className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {addOpen ? "Cancel" : "+ Add document"}
        </button>
      </div>

      {addOpen && (
        <div className="mb-4 rounded-xl border border-gray-200 bg-white/70 p-4">
          <input
            ref={fileInput}
            type="file"
            multiple
            accept=".pdf,application/pdf"
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length) upload.mutate(files);
              e.target.value = "";
            }}
          />
          <p className="mb-2 text-sm font-medium text-ink">Upload to which portfolios?</p>
          <PortfolioPicker selected={targets} onChange={setTargets} className="mb-3" />
          <button
            onClick={() => fileInput.current?.click()}
            disabled={targets.size === 0 || upload.isPending}
            className="rounded-lg bg-[#dd9aa6] px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {upload.isPending
              ? "Uploading…"
              : `Choose PDF(s) → upload to ${targets.size} portfolio(s)`}
          </button>
          <p className="mt-2 text-xs text-gray-500">
            The document is added to each selected portfolio's knowledge base, and available only
            there. The rubric re-grounds on the next call judged.
          </p>
        </div>
      )}

      {msg && <p className="mb-3 rounded-lg bg-sky-50 px-3 py-2 text-sm text-sky-800">{msg}</p>}

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        {isLoading && <p className="p-4 text-sm text-gray-400">Loading…</p>}
        {docs.map((d) => (
          <div
            key={d.id}
            className="flex items-center justify-between gap-3 border-b border-gray-100 px-4 py-3 last:border-0"
          >
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-ink">
                {d.filename || `Document ${d.id.slice(0, 8)}`}
              </div>
              <div className="text-xs text-gray-400">
                {d.page_count != null ? `${d.page_count} pages · ` : ""}added{" "}
                {new Date(d.created_at).toLocaleDateString()}
              </div>
            </div>
            <button
              title="Delete document"
              disabled={remove.isPending}
              onClick={() => {
                if (window.confirm(`Delete “${d.filename || "this document"}” from the KB?`))
                  remove.mutate(d.id);
              }}
              className="rounded-md p-1.5 text-gray-400 transition hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
            >
              <IconTrash />
            </button>
          </div>
        ))}
        {!isLoading && docs.length === 0 && (
          <p className="p-6 text-center text-sm text-gray-400">
            No documents yet. Add the portfolio's SOP / cheat-sheets to ground verdicts.
          </p>
        )}
      </div>
      <p className="mt-3 text-xs text-gray-500">
        PDFs are stored per portfolio; their text grounds the rubric (§7.2). This list shows the
        current portfolio's documents. Only supervisors and super admins can manage documents.
      </p>
    </div>
  );
}
