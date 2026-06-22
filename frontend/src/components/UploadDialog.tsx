import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import { useToasts } from "../lib/toast";

// The four processing OPTIONs (input feature §3) — each fully determines the pipeline + which
// inputs it needs (a checklist and/or the knowledge base).
const OPTIONS = [
  {
    value: "FULL",
    label: "All reports",
    desc: "Feedback + checklist + ideal + raw transcript",
    needs: "Needs a checklist + the knowledge base",
  },
  {
    value: "FEEDBACK_IDEAL",
    label: "Feedback + ideal",
    desc: "Feedback + ideal conversation + raw (no checklist)",
    needs: "Needs the knowledge base",
  },
  {
    value: "CHECKLIST_ONLY",
    label: "Checklist only",
    desc: "Checklist + raw transcript (no feedback / ideal)",
    needs: "Needs a checklist",
  },
  {
    value: "RAW_ONLY",
    label: "Raw transcript only",
    desc: "Just the transcript — no AI, no LLM",
    needs: "No checklist or KB used",
  },
] as const;

const needsChecklist = (o: string) => o === "FULL" || o === "CHECKLIST_ONLY";
const needsKb = (o: string) => o === "FULL" || o === "FEEDBACK_IDEAL";
const reportsFor = (o: string) =>
  ({
    FULL: "feedback · checklist · ideal · raw",
    FEEDBACK_IDEAL: "feedback · ideal · raw",
    CHECKLIST_ONLY: "checklist · raw",
    RAW_ONLY: "raw transcript",
  })[o] ?? "";

export function UploadDialog({
  portfolioId,
  agentId,
  onClose,
  onUploaded,
}: {
  portfolioId: string;
  agentId: string;
  onClose: () => void;
  onUploaded: (count: number) => void;
}) {
  const pushToast = useToasts((s) => s.push);
  const [option, setOption] = useState<string>("FULL");
  const [checklistId, setChecklistId] = useState<string>("");
  const [kbMode, setKbMode] = useState<"all" | "specific">("all");
  const [kbDocIds, setKbDocIds] = useState<Set<string>>(new Set());
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const { data: checklists = [] } = useQuery({
    queryKey: ["checklists", portfolioId],
    queryFn: () => api.listChecklists(portfolioId),
    enabled: needsChecklist(option),
  });
  const { data: kbDocs = [] } = useQuery({
    queryKey: ["kb", portfolioId],
    queryFn: () => api.listKb(portfolioId),
    enabled: needsKb(option),
  });
  // Per-portfolio in-flight cap (max 10 processing at once). Refetches so headroom updates live
  // as the current batch finishes.
  const { data: quota } = useQuery({
    queryKey: ["upload-quota", portfolioId],
    queryFn: () => api.getUploadQuota(portfolioId),
    refetchInterval: 4000,
  });
  const remaining = quota?.remaining ?? 10;

  // Preselect the default checklist so the choice is always explicit (never a silent blank).
  useEffect(() => {
    if (needsChecklist(option) && !checklistId && checklists.length) {
      setChecklistId((checklists.find((c) => c.is_default) ?? checklists[0]).id);
    }
  }, [checklists, option, checklistId]);

  const toggleDoc = (id: string) =>
    setKbDocIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const submit = async () => {
    // Validate the chosen option's requirements before uploading — toast a clear "select…".
    if (needsChecklist(option)) {
      if (!checklists.length) {
        pushToast("No checklist available — create one in the Checklist builder first.", "error");
        return;
      }
      if (!checklistId) {
        pushToast("Select a checklist to audit against.", "error");
        return;
      }
    }
    if (needsKb(option)) {
      if (!kbDocs.length) {
        pushToast(
          "No knowledge-base documents — add some in the Knowledge base, or pick an option that doesn't use the KB.",
          "error",
        );
        return;
      }
      if (kbMode === "specific" && kbDocIds.size === 0) {
        pushToast('Select at least one KB document, or choose "All documents".', "error");
        return;
      }
    }
    if (!files.length) {
      pushToast("Choose at least one recording to upload.", "error");
      return;
    }
    // Per-portfolio in-flight cap (the server enforces this too — this is just a faster, clearer
    // client-side guard).
    if (files.length > remaining) {
      pushToast(
        remaining <= 0
          ? "This portfolio already has 10 recordings processing — wait for the current batch to finish."
          : `Only ${remaining} more recording(s) can be queued right now (max 10 processing per portfolio).`,
        "error",
      );
      return;
    }

    setBusy(true);
    try {
      const res = await api.uploadRecordings(portfolioId, agentId, files, {
        option,
        checklistId: needsChecklist(option) ? checklistId : null,
        kbDocIds: needsKb(option) && kbMode === "specific" ? [...kbDocIds] : null,
      });
      onUploaded(res.calls.length);
      onClose();
    } catch (e) {
      let msg = "Upload failed.";
      if (e instanceof ApiError) {
        try {
          msg = JSON.parse(e.message).detail ?? `Upload failed (${e.status}).`;
        } catch {
          msg = `Upload failed (${e.status}).`;
        }
      }
      pushToast(msg, "error");
    } finally {
      setBusy(false);
    }
  };

  const selChecklist = checklists.find((c) => c.id === checklistId);
  const summary = [
    needsChecklist(option) && `Checklist: ${selChecklist ? selChecklist.name : "—"}`,
    needsKb(option) &&
      `KB: ${kbMode === "all" ? `all ${kbDocs.length} doc(s)` : `${kbDocIds.size} selected`}`,
    `Reports: ${reportsFor(option)}`,
  ].filter(Boolean);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[88vh] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-white/60 bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <h2 className="text-lg font-semibold text-ink">Upload recordings</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-lg p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          >
            ✕
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-6 py-5">
        <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500">
          1 · What to produce
        </p>
        <div className="space-y-1.5">
          {OPTIONS.map((o) => (
            <label
              key={o.value}
              className={`flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 ${
                option === o.value
                  ? "border-[#d28aa6] bg-[#dd9aa6]/10"
                  : "border-gray-200 hover:bg-gray-50"
              }`}
            >
              <input
                type="radio"
                name="option"
                className="mt-0.5"
                checked={option === o.value}
                onChange={() => setOption(o.value)}
              />
              <span>
                <span className="block text-sm font-medium text-ink">{o.label}</span>
                <span className="block text-xs text-gray-500">{o.desc}</span>
                <span className="mt-0.5 block text-[0.7rem] font-medium text-[#a85478]">
                  {o.needs}
                </span>
              </span>
            </label>
          ))}
        </div>

        {needsChecklist(option) && (
          <div className="mt-4">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
              2 · Checklist to audit against
            </p>
            {checklists.length === 0 ? (
              <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                No checklist exists yet — create one in the Checklist builder first.
              </p>
            ) : (
              <select
                className="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm"
                value={checklistId}
                onChange={(e) => setChecklistId(e.target.value)}
              >
                {checklists.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} (v{c.version}){c.is_default ? " — default" : ""}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        {needsKb(option) && (
          <div className="mt-4">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
              {needsChecklist(option) ? "3" : "2"} · Knowledge base to ground on
            </p>
            {kbDocs.length === 0 ? (
              <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                No KB documents — add some in the Knowledge base, or pick an option that doesn't
                use the KB.
              </p>
            ) : (
              <>
                <div className="flex gap-4 text-sm">
                  <label className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      name="kbmode"
                      checked={kbMode === "all"}
                      onChange={() => setKbMode("all")}
                    />
                    All {kbDocs.length} document(s)
                  </label>
                  <label className="flex items-center gap-1.5">
                    <input
                      type="radio"
                      name="kbmode"
                      checked={kbMode === "specific"}
                      onChange={() => setKbMode("specific")}
                    />
                    Specific documents
                  </label>
                </div>
                {kbMode === "specific" && (
                  <div className="mt-1 max-h-32 space-y-1 overflow-auto rounded-lg border border-gray-200 p-2">
                    {kbDocs.map((d) => (
                      <label key={d.id} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={kbDocIds.has(d.id)}
                          onChange={() => toggleDoc(d.id)}
                        />
                        <span className="truncate">{d.filename ?? d.sha256.slice(0, 10)}</span>
                      </label>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        <div className="mt-4">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
            {needsChecklist(option) && needsKb(option)
              ? "4"
              : needsChecklist(option) || needsKb(option)
                ? "3"
                : "2"}{" "}
            · Recordings
          </p>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept="audio/*"
            className="hidden"
            onChange={(e) =>
              setFiles(Array.from(e.target.files ?? []).slice(0, Math.max(0, remaining)))
            }
          />
          <button
            onClick={() => fileInput.current?.click()}
            disabled={remaining <= 0}
            className="rounded-lg border border-dashed border-[#c98aa0] px-3 py-1.5 text-sm text-[#8c3a55] hover:bg-[#dd9aa6]/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {files.length
              ? `${files.length} file(s) selected`
              : `Choose recordings (≤${Math.max(0, remaining)})`}
          </button>
          <p className="mt-1.5 text-xs text-gray-500">
            {remaining <= 0 ? (
              <span className="text-amber-600">
                This portfolio already has {quota?.in_flight ?? 10} recording(s) processing (max{" "}
                {quota?.max ?? 10}). Wait for the current batch to finish before uploading more.
              </span>
            ) : (
              <>
                <span className="font-medium text-gray-700">{remaining}</span> of {quota?.max ?? 10}{" "}
                slots free in this portfolio
                {quota && quota.in_flight > 0 ? ` (${quota.in_flight} still processing).` : "."}
              </>
            )}
          </p>
        </div>

        {/* Plain-language summary of exactly what will run for this batch. */}
        <div className="mt-4 rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600">
          <span className="font-semibold text-gray-700">This batch → </span>
          {summary.join("  ·  ")}
        </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-gray-100 px-6 py-4">
          <button
            onClick={onClose}
            className="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy}
            className="rounded-lg bg-[#dd9aa6] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? "Uploading…" : "Upload"}
          </button>
        </div>
      </div>
    </div>
  );
}
