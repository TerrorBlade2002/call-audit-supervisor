import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type ChecklistItemModel } from "../lib/api";
import { useToasts } from "../lib/toast";

const ANSWER_TYPES = ["CHOICE", "PASS_FAIL", "PASS_FAIL_NA", "TEXT"] as const;
const RISKS = ["NORMAL", "ELEVATED", "CRITICAL"] as const;

function blankItem(): ChecklistItemModel {
  return {
    section: "A · Compliance & Mandatory Disclosures",
    text: "",
    answer_type: "CHOICE",
    options: ["Yes", "No", "NA"],
    is_subjective: false,
    risk: "NORMAL",
    guidance: "",
  };
}

export function ChecklistBuilder({
  portfolioId,
  onClose,
}: {
  portfolioId: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const pushToast = useToasts((s) => s.push);
  const fileInput = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [items, setItems] = useState<ChecklistItemModel[]>([]);
  const [cid, setCid] = useState<string | null>(null);
  const [requiresKb, setRequiresKb] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);

  const { data: summaries } = useQuery({
    queryKey: ["checklists", portfolioId],
    queryFn: () => api.listChecklists(portfolioId),
  });
  const defaultId = summaries?.find((c) => c.is_default)?.id ?? summaries?.[0]?.id;

  const { data: detail } = useQuery({
    queryKey: ["checklist", portfolioId, defaultId],
    queryFn: () => api.getChecklist(portfolioId, defaultId!),
    enabled: !!defaultId,
  });

  useEffect(() => {
    if (detail) {
      setCid(detail.id);
      setName(detail.name);
      setItems(detail.items.map((i) => ({ ...i })));
      setRequiresKb(detail.requires_kb);
    }
  }, [detail]);

  const save = useMutation({
    mutationFn: () => api.updateChecklist(portfolioId, cid!, name, items, requiresKb),
    onSuccess: (d) => {
      setMsg(`Saved — v${d.version}`);
      qc.invalidateQueries({ queryKey: ["checklists", portfolioId] });
      qc.invalidateQueries({ queryKey: ["checklist", portfolioId] });
    },
    onError: () => setMsg("Save failed (need MANAGER/ADMIN)."),
  });

  // Upload a .txt in the Everest checklist format → parse server-side → load into the editor.
  // The user then reviews and clicks Save. A file that doesn't match the format just toasts.
  const onUpload = async (file: File) => {
    try {
      const parsed = await api.parseChecklistTxt(portfolioId, file);
      if (parsed.name) setName(parsed.name);
      setItems(parsed.items.map((i) => ({ ...i })));
      pushToast(
        `Loaded ${parsed.items.length} items from ${file.name} — review and Save.`,
        "success",
      );
    } catch (e) {
      pushToast(
        e instanceof ApiError && e.status === 422
          ? "Couldn't parse — invalid format. Create it in the editor instead."
          : "Upload failed.",
        "error",
      );
    }
  };

  const update = (idx: number, patch: Partial<ChecklistItemModel>) =>
    setItems((xs) => xs.map((it, i) => (i === idx ? { ...it, ...patch } : it)));
  const move = (idx: number, dir: -1 | 1) =>
    setItems((xs) => {
      const j = idx + dir;
      if (j < 0 || j >= xs.length) return xs;
      const copy = [...xs];
      [copy[idx], copy[j]] = [copy[j], copy[idx]];
      return copy;
    });

  return (
    <div className="mx-auto max-w-4xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <button onClick={onClose} className="text-sm text-accent hover:underline">
          ← Back
        </button>
        <div className="flex items-center gap-2">
          {msg && <span className="text-sm text-gray-500">{msg}</span>}
          <input
            ref={fileInput}
            type="file"
            accept=".txt"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onUpload(f);
              e.target.value = ""; // allow re-uploading the same file
            }}
          />
          <button
            onClick={() => fileInput.current?.click()}
            className="rounded border px-3 py-1.5 text-sm hover:bg-gray-50"
            title="Upload a checklist .txt in the Everest format"
          >
            Upload .txt
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={!cid || save.isPending}
            className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white"
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
      {detail?.updated_at && (
        <p className="mb-3 text-xs text-gray-400">
          Last modified {new Date(detail.updated_at).toLocaleString()} · v{detail.version}
        </p>
      )}

      <label className="block text-sm">
        <span className="text-gray-500">Checklist name</span>
        <input
          className="mt-1 w-full rounded border px-2 py-1.5"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>

      <label className="mt-3 flex items-start gap-2 rounded-lg border border-gray-200 bg-gray-50/70 p-3 text-sm">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={requiresKb}
          onChange={(e) => setRequiresKb(e.target.checked)}
        />
        <span>
          <span className="font-medium text-ink">Use the Knowledge Base to fill this checklist</span>
          <span className="block text-xs text-gray-500">
            On = the knowledge base is given to the checklist judge to ground its verdicts. Off =
            answer from the model's own knowledge (no KB — leaner & cheaper). Turn off for generic
            checklists that don't need Everest's specific SOP.
          </span>
        </span>
      </label>

      <div className="mt-4 space-y-3">
        {items.map((it, idx) => (
          <div key={idx} className="rounded-lg border bg-white p-3">
            <div className="flex items-start gap-2">
              <div className="flex flex-col gap-1 pt-1">
                <button onClick={() => move(idx, -1)} className="text-xs text-gray-400 hover:text-ink">▲</button>
                <button onClick={() => move(idx, 1)} className="text-xs text-gray-400 hover:text-ink">▼</button>
              </div>
              <div className="flex-1 space-y-2">
                <input
                  className="w-full rounded border px-2 py-1 text-xs text-gray-500"
                  value={it.section}
                  onChange={(e) => update(idx, { section: e.target.value })}
                  placeholder="Section"
                />
                <textarea
                  className="w-full rounded border px-2 py-1 text-sm"
                  rows={2}
                  value={it.text}
                  onChange={(e) => update(idx, { text: e.target.value })}
                  placeholder="Item question…"
                />
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <select
                    className="rounded border px-1.5 py-1"
                    value={it.answer_type}
                    onChange={(e) => update(idx, { answer_type: e.target.value as ChecklistItemModel["answer_type"] })}
                  >
                    {ANSWER_TYPES.map((t) => (
                      <option key={t}>{t}</option>
                    ))}
                  </select>
                  <select
                    className="rounded border px-1.5 py-1"
                    value={it.risk}
                    onChange={(e) => update(idx, { risk: e.target.value as ChecklistItemModel["risk"] })}
                  >
                    {RISKS.map((r) => (
                      <option key={r}>{r}</option>
                    ))}
                  </select>
                  <label className="flex items-center gap-1">
                    <input
                      type="checkbox"
                      checked={it.is_subjective}
                      onChange={(e) => update(idx, { is_subjective: e.target.checked })}
                    />
                    subjective
                  </label>
                  <input
                    className="min-w-[180px] flex-1 rounded border px-1.5 py-1"
                    value={(it.options ?? []).join(", ")}
                    onChange={(e) =>
                      update(idx, {
                        options: e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      })
                    }
                    placeholder="Options (comma-separated) — empty for free text"
                  />
                  <button
                    onClick={() => setItems((xs) => xs.filter((_, i) => i !== idx))}
                    className="text-red-500 hover:underline"
                  >
                    delete
                  </button>
                </div>
                <input
                  className="w-full rounded border px-2 py-1 text-xs text-gray-600"
                  value={it.guidance ?? ""}
                  onChange={(e) => update(idx, { guidance: e.target.value })}
                  placeholder="Guidance / rubric hint…"
                />
              </div>
            </div>
          </div>
        ))}
      </div>

      <button
        onClick={() => setItems((xs) => [...xs, blankItem()])}
        className="mt-3 rounded border border-dashed px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
      >
        + Add item
      </button>
    </div>
  );
}
