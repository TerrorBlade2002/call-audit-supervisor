import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type Call } from "../lib/api";
import { useCaps } from "../lib/roles";
import { useToasts } from "../lib/toast";
import { IconStop, IconTrash, Pager, Spinner } from "./ui";
import { UploadDialog } from "./UploadDialog";

const isTerminal = (s: string | null) => s === "DONE" || s === "FAILED";
const LIMIT = 20;

const STATUS_STYLE: Record<string, string> = {
  PENDING_TRANSCRIPTION: "bg-amber-100 text-amber-700",
  AWAITING_TRANSCRIPT: "bg-amber-100 text-amber-700",
  PENDING_JUDGE: "bg-blue-100 text-blue-700",
  DONE: "bg-green-100 text-green-700",
  FAILED: "bg-red-100 text-red-700",
};

const OPTION_LABEL: Record<string, string> = {
  FULL: "All reports",
  FEEDBACK_IDEAL: "Feedback + ideal",
  CHECKLIST_ONLY: "Checklist only",
  RAW_ONLY: "Raw only",
};
const producesChecklist = (o: string | null) => o === "FULL" || o === "CHECKLIST_ONLY";

interface Batch {
  id: string;
  calls: Call[];
}

export function CallsPane({
  portfolioId,
  agentId,
  agentName,
  onOpenReport,
  onOpenSummary,
}: {
  portfolioId: string | null;
  agentId: string | null;
  agentName?: string | null;
  onOpenReport: (reportId: string) => void;
  onOpenSummary: (batchId: string) => void;
}) {
  const qc = useQueryClient();
  const caps = useCaps(portfolioId);
  const push = useToasts((s) => s.push);
  const [offset, setOffset] = useState(0);
  const [uploadOpen, setUploadOpen] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["calls", portfolioId, agentId, offset],
    queryFn: () => api.listCalls(portfolioId!, agentId!, { limit: LIMIT, offset }),
    enabled: !!portfolioId && !!agentId,
    refetchInterval: 3000, // live-ish status (SSE backbone exists server-side)
    placeholderData: keepPreviousData,
  });
  const calls = data?.items ?? [];
  const total = data?.total ?? 0;

  // Group the page's calls by their upload batch, preserving order (newest batch first).
  const batches: Batch[] = (() => {
    const idx = new Map<string, number>();
    const out: Batch[] = [];
    for (const c of calls) {
      const b = c.batch_id ?? c.id;
      if (!idx.has(b)) {
        idx.set(b, out.length);
        out.push({ id: b, calls: [] });
      }
      out[idx.get(b)!].calls.push(c);
    }
    return out;
  })();

  const remove = useMutation({
    mutationFn: (callId: string) => api.deleteCall(portfolioId!, agentId!, callId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["calls", portfolioId, agentId] }),
  });

  // Toast once per batch when it finishes (only for batches we saw processing this session).
  const seenPending = useRef<Set<string>>(new Set());
  const toasted = useRef<Set<string>>(new Set());
  useEffect(() => {
    for (const g of batches) {
      if (g.calls.some((c) => !isTerminal(c.status))) {
        seenPending.current.add(g.id);
        continue;
      }
      if (!seenPending.current.has(g.id) || toasted.current.has(g.id)) continue;
      toasted.current.add(g.id);
      const n = g.calls.length;
      const failed = g.calls.filter((c) => c.status === "FAILED").length;
      if (n === 1) {
        push(failed ? "Recording failed to process." : "Recording processed.", failed ? "error" : "success");
      } else {
        push(
          `Batch processed — ${n - failed}/${n} succeeded${failed ? `, ${failed} failed` : ""}.`,
          failed ? "info" : "success",
        );
      }
    }
  }, [batches, push]);

  if (!agentId) {
    return <div className="p-6 text-sm text-gray-400">Select an agent to see its calls.</div>;
  }

  return (
    <div className="flex h-full flex-col p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">{agentName || "Calls"}</h2>
          <span className="text-sm text-gray-500">{total} recording(s)</span>
        </div>
        {caps.canManage && (
          <button
            onClick={() => setUploadOpen(true)}
            className="rounded-lg bg-[#dd9aa6] px-3 py-1.5 text-sm font-medium text-white hover:brightness-105"
          >
            ⬆ Upload recordings
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto pr-1">
        {/* While the first page is loading, don't flash the empty state — the fetch takes a
            moment for already-processed folders. Only show "no calls" once loading completes. */}
        {isLoading ? (
          <p className="flex items-center justify-center gap-2 rounded-xl border border-gray-200/70 bg-white/70 px-4 py-6 text-center text-sm text-gray-500">
            <Spinner />
            Please wait while we fetch your calls…
          </p>
        ) : (
          batches.length === 0 && (
            <p className="rounded-xl border border-gray-200/70 bg-white/70 px-4 py-6 text-center text-sm text-gray-400">
              No calls yet{caps.canManage ? " — upload recordings to get started." : "."}
            </p>
          )
        )}

        {batches.map((g) => {
          const opt = g.calls[0].option;
          const allDone = g.calls.every((c) => isTerminal(c.status));
          const doneCount = g.calls.filter((c) => c.status === "DONE").length;
          const failedCount = g.calls.filter((c) => c.status === "FAILED").length;
          // Batch CSV is ready only when EVERY call in the batch is finished, the option
          // produces a checklist, and at least one call actually succeeded.
          const csvReady = allDone && producesChecklist(opt) && doneCount > 0;
          return (
            <div
              key={g.id}
              className="overflow-hidden rounded-xl border border-gray-200/80 bg-white/70 shadow-sm"
            >
              {/* batch header */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-200/70 bg-gray-50/70 px-4 py-2">
                <div className="flex items-center gap-2 text-sm">
                  {!allDone && <Spinner />}
                  <span className="font-semibold text-ink">
                    Batch · {new Date(g.calls[0].created_at).toLocaleString()}
                  </span>
                  <span className="rounded-full bg-[#dd9aa6]/20 px-2 py-0.5 text-xs font-medium text-[#8c3a55]">
                    {OPTION_LABEL[opt ?? ""] ?? opt ?? "—"}
                  </span>
                  <span className="text-xs text-gray-500">
                    {g.calls.length} recording(s){" "}
                    {allDone
                      ? `· ${doneCount} done${failedCount ? `, ${failedCount} failed` : ""}`
                      : "· processing…"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {caps.canManage && allDone && (
                    <button
                      title="At-a-glance batch summary (checklist triage + feedback download)"
                      onClick={() => onOpenSummary(g.id)}
                      className="rounded-lg border border-gray-300 px-2.5 py-1 text-xs font-medium text-ink hover:bg-gray-50"
                    >
                      📋 Summary
                    </button>
                  )}
                  {caps.canManage &&
                    producesChecklist(opt) &&
                    (csvReady ? (
                      <button
                        title="Download this batch's checklist results (CSV)"
                        onClick={() =>
                          api
                            .downloadBatchCsv(portfolioId!, g.id)
                            .catch(() => push("No checklist results for that batch.", "error"))
                        }
                        className="rounded-lg border border-[#c98aa0] px-2.5 py-1 text-xs font-medium text-[#8c3a55] hover:bg-[#dd9aa6]/10"
                      >
                        ⤓ Batch checklist CSV
                      </button>
                    ) : (
                      <span
                        className="text-xs text-gray-400"
                        title="Available once all reports in this batch are generated"
                      >
                        CSV ready when all done
                      </span>
                    ))}
                </div>
              </div>

              {/* batch calls */}
              <table className="w-full text-sm">
                <tbody>
                  {g.calls.map((c) => (
                    <tr key={c.id} className="border-t border-gray-200/50 first:border-t-0">
                      <td className="px-4 py-2 font-mono text-xs text-gray-600">{c.id.slice(0, 8)}</td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-2">
                          {!isTerminal(c.status) && <Spinner />}
                          <span
                            className={`rounded px-2 py-0.5 text-xs font-medium ${
                              STATUS_STYLE[c.status ?? ""] ?? "bg-gray-100 text-gray-600"
                            }`}
                          >
                            {c.status ?? "—"}
                          </span>
                        </div>
                        {c.status === "FAILED" && c.last_error && (
                          <p className="mt-1 max-w-xs text-xs leading-snug text-red-600">
                            {c.last_error}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-2 text-gray-500">
                        {c.completed_at ? (
                          new Date(c.completed_at).toLocaleString()
                        ) : (
                          <span className="text-gray-300">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex items-center justify-end gap-3">
                          <button
                            disabled={!c.report_id}
                            onClick={() => c.report_id && onOpenReport(c.report_id)}
                            className="font-medium text-[#c0567f] hover:underline disabled:text-gray-300"
                          >
                            Open report
                          </button>
                          {caps.canManage &&
                            (isTerminal(c.status) ? (
                              <button
                                title="Remove recording, transcript & report"
                                disabled={remove.isPending}
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      "Remove this recording, its transcript and report? This deletes the audio from storage and cannot be undone.",
                                    )
                                  )
                                    remove.mutate(c.id);
                                }}
                                className="rounded-md p-1.5 text-gray-400 transition hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                              >
                                <IconTrash />
                              </button>
                            ) : (
                              <button
                                title="Cancel processing & remove"
                                disabled={remove.isPending}
                                onClick={() => {
                                  if (window.confirm("Cancel processing and remove this recording?"))
                                    remove.mutate(c.id);
                                }}
                                className="rounded-md p-1.5 text-red-500 transition hover:bg-red-50 disabled:opacity-40"
                              >
                                <IconStop />
                              </button>
                            ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>

      <Pager offset={offset} limit={LIMIT} total={total} onPage={setOffset} />

      {uploadOpen && portfolioId && agentId && (
        <UploadDialog
          portfolioId={portfolioId}
          agentId={agentId}
          onClose={() => setUploadOpen(false)}
          onUploaded={(n) => {
            qc.invalidateQueries({ queryKey: ["calls", portfolioId, agentId] });
            push(`Queued ${n} recording(s).`, "success");
          }}
        />
      )}
    </div>
  );
}
