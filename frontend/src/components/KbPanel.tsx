import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import { IconTrash } from "./ui";

export function KbPanel({ portfolioId, onClose }: { portfolioId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const { data: docs = [], isLoading } = useQuery({
    queryKey: ["kb", portfolioId],
    queryFn: () => api.listKb(portfolioId),
  });

  const upload = useMutation({
    mutationFn: (files: File[]) => api.uploadKb(portfolioId, files),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["kb", portfolioId] });
      setMsg(`Added ${d.length} document(s). The rubric re-grounds on the next call judged.`);
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
        <div>
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
          <button
            onClick={() => fileInput.current?.click()}
            disabled={upload.isPending}
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {upload.isPending ? "Uploading…" : "+ Add document"}
          </button>
        </div>
      </div>

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
        PDFs are stored in this portfolio's R2 bucket; their text grounds the rubric (§7.2).
        Only supervisors and super admins can manage documents.
      </p>
    </div>
  );
}
