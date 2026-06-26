import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type ChecklistItemModel } from "../lib/api";
import { useCaps } from "../lib/roles";
import { useToasts } from "../lib/toast";
import { PortfolioPicker } from "./PortfolioPicker";

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
  const caps = useCaps(portfolioId);
  const pushToast = useToasts((s) => s.push);
  const fileInput = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [items, setItems] = useState<ChecklistItemModel[]>([]);
  const [requiresKb, setRequiresKb] = useState(true);
  // The existing checklist being edited (versioned on save). null while authoring a brand-new one.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newMode, setNewMode] = useState(false);
  // Portfolios a NEW checklist is saved to (defaults to the one being viewed).
  const [targets, setTargets] = useState<Set<string>>(() => new Set([portfolioId]));
  const [msg, setMsg] = useState<string | null>(null);

  const { data: summaries } = useQuery({
    queryKey: ["checklists", portfolioId],
    queryFn: () => api.listChecklists(portfolioId),
  });
  const defaultId = summaries?.find((c) => c.is_default)?.id ?? summaries?.[0]?.id;

  // The checklist currently loaded in the editor (the selected one, or the default initially).
  const { data: detail } = useQuery({
    queryKey: ["checklist", portfolioId, selectedId],
    queryFn: () => api.getChecklist(portfolioId, selectedId!),
    enabled: !!selectedId && !newMode,
  });
  // The portfolio's default checklist — the seed for "New checklist".
  const { data: defaultDetail } = useQuery({
    queryKey: ["checklist", portfolioId, defaultId],
    queryFn: () => api.getChecklist(portfolioId, defaultId!),
    enabled: !!defaultId,
  });

  // Land on the default checklist when the panel opens.
  useEffect(() => {
    if (!selectedId && !newMode && defaultId) setSelectedId(defaultId);
  }, [defaultId, selectedId, newMode]);

  // Load the selected existing checklist into the editor (not while authoring a new one).
  useEffect(() => {
    if (detail && !newMode) {
      setName(detail.name);
      setItems(detail.items.map((i) => ({ ...i })));
      setRequiresKb(detail.requires_kb);
    }
  }, [detail, newMode]);

  const startNew = () => {
    const base = defaultDetail;
    setNewMode(true);
    setSelectedId(null);
    setName(base ? `${base.name} (copy)` : "New checklist");
    setItems((base?.items ?? []).map((i) => ({ ...i, id: undefined })));
    setRequiresKb(base?.requires_kb ?? true);
    setTargets(new Set([portfolioId]));
    setMsg(null);
  };

  const editExisting = (id: string) => {
    setNewMode(false);
    setSelectedId(id);
    setMsg(null);
  };

  const save = useMutation({
    mutationFn: async () => {
      if (newMode) {
        const ids = [...targets];
        const results = await Promise.allSettled(
          ids.map((pid) => api.createChecklist(pid, name, items, requiresKb)),
        );
        const ok = ids.filter((_, i) => results[i].status === "fulfilled");
        const failed = ids.filter((_, i) => results[i].status === "rejected");
        const mine = results[ids.indexOf(portfolioId)];
        const createdHere =
          mine && mine.status === "fulfilled"
            ? (mine as PromiseFulfilledResult<{ id: string }>).value.id
            : null;
        return { mode: "create" as const, ok, failed, createdHere };
      }
      const d = await api.updateChecklist(portfolioId, selectedId!, name, items, requiresKb);
      return { mode: "update" as const, version: d.version, id: d.id };
    },
    onSuccess: (res) => {
      if (res.mode === "create") {
        res.ok.forEach((pid) => qc.invalidateQueries({ queryKey: ["checklists", pid] }));
        if (res.failed.length === 0) {
          setMsg(`Created in ${res.ok.length} portfolio(s).`);
        } else {
          setMsg(
            `Created in ${res.ok.length} portfolio(s); ${res.failed.length} failed ` +
              "(you may lack manage rights there).",
          );
        }
        setNewMode(false);
        setSelectedId(res.createdHere ?? defaultId ?? null);
      } else {
        setMsg(`Saved — v${res.version}`);
        setSelectedId(res.id);
        qc.invalidateQueries({ queryKey: ["checklists", portfolioId] });
        qc.invalidateQueries({ queryKey: ["checklist", portfolioId] });
      }
    },
    onError: (e) =>
      setMsg(
        e instanceof ApiError && e.status === 403
          ? "Save failed — you need manage rights (SUPERVISOR/ADMIN)."
          : "Save failed.",
      ),
  });

  // Rename the selected checklist (local to this portfolio; no version bump for a label change).
  const rename = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      api.renameChecklist(portfolioId, id, name),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["checklists", portfolioId] });
      qc.invalidateQueries({ queryKey: ["checklist", portfolioId] });
      pushToast(`Renamed to “${d.name}”.`, "success");
    },
    onError: () => pushToast("Rename failed.", "error"),
  });
  // Delete the selected checklist (admin only; soft-delete — existing reports stay intact).
  const del = useMutation({
    mutationFn: (id: string) => api.deleteChecklist(portfolioId, id),
    onSuccess: () => {
      pushToast("Checklist deleted. Reports judged against it are unaffected.", "success");
      setNewMode(false);
      setSelectedId(defaultId ?? null);
      qc.invalidateQueries({ queryKey: ["checklists", portfolioId] });
    },
    onError: (e) =>
      pushToast(
        e instanceof ApiError && e.status === 409
          ? "The default checklist can't be deleted."
          : e instanceof ApiError && e.status === 403
            ? "Only admins can delete checklists."
            : "Delete failed.",
        "error",
      ),
  });

  // Upload a .txt in the Everest checklist format → parse server-side → load into the editor.
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

  const saveDisabled =
    save.isPending || items.length === 0 || (newMode ? targets.size === 0 : !selectedId);

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
              e.target.value = "";
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
            disabled={saveDisabled}
            className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : newMode ? "Create checklist" : "Save"}
          </button>
        </div>
      </div>

      {/* Which checklist — pick an existing one to edit, or author a new one. */}
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-gray-200 bg-white/60 p-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          Checklist
        </span>
        <select
          className="rounded-lg border border-gray-300 px-2 py-1.5 text-sm disabled:opacity-50"
          value={newMode ? "" : selectedId ?? ""}
          onChange={(e) => e.target.value && editExisting(e.target.value)}
          disabled={newMode}
        >
          {newMode && <option value="">(new checklist — unsaved)</option>}
          {(summaries ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
              {c.is_default ? " (default)" : ""} · v{c.version}
            </option>
          ))}
        </select>
        <button
          onClick={startNew}
          className="rounded-lg border border-[#dd9aa6] px-2.5 py-1.5 text-sm font-medium text-[#8c3a55] hover:bg-[#dd9aa6]/10"
        >
          + New checklist
        </button>
        {!newMode && selectedId && detail && caps.canManage && (
          <button
            onClick={() => {
              const name = window.prompt("Rename this checklist", detail.name)?.trim();
              if (name && name !== detail.name) rename.mutate({ id: selectedId, name });
            }}
            className="rounded-lg border border-gray-300 px-2.5 py-1.5 text-sm hover:bg-gray-50"
          >
            Rename
          </button>
        )}
        {/* Delete is ADMIN-only; the default checklist can't be deleted. */}
        {!newMode && selectedId && detail && caps.isSuperAdmin && !detail.is_default && (
          <button
            onClick={() => {
              if (
                window.confirm(
                  `Delete “${detail.name}”? It disappears from pickers, but reports already judged ` +
                    "against it stay intact.",
                )
              )
                del.mutate(selectedId);
            }}
            disabled={del.isPending}
            className="rounded-lg border border-red-300 px-2.5 py-1.5 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"
          >
            Delete
          </button>
        )}
        {!newMode && detail?.updated_at && (
          <span className="text-xs text-gray-400">
            Last modified {new Date(detail.updated_at).toLocaleString()} · v{detail.version}
          </span>
        )}
      </div>

      {/* New-checklist target portfolios. */}
      {newMode && (
        <div className="mb-3 rounded-xl border border-[#dd9aa6]/50 bg-[#dd9aa6]/5 p-3">
          <p className="mb-2 text-sm font-medium text-ink">
            Save this new checklist to which portfolios?
          </p>
          <PortfolioPicker selected={targets} onChange={setTargets} />
          <p className="mt-2 text-xs text-gray-500">
            Seeded from the default checklist — edit below, then “Create checklist”. It is saved as a
            new checklist in each selected portfolio (the default is never overwritten).
          </p>
        </div>
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
                  {/* Subjective = free text: the model writes a short answer instead of PASS/FAIL,
                      so the answer-type + options controls don't apply and are hidden. */}
                  <label className="flex items-center gap-1 rounded border border-[#dd9aa6]/60 bg-[#dd9aa6]/10 px-2 py-1 font-medium text-[#8c3a55]">
                    <input
                      type="checkbox"
                      checked={it.is_subjective}
                      onChange={(e) =>
                        update(
                          idx,
                          e.target.checked
                            ? { is_subjective: true, answer_type: "TEXT", options: null }
                            : { is_subjective: false, answer_type: "CHOICE", options: ["Yes", "No", "NA"] },
                        )
                      }
                    />
                    subjective (free text)
                  </label>
                  <select
                    className="rounded border px-1.5 py-1"
                    value={it.risk}
                    onChange={(e) => update(idx, { risk: e.target.value as ChecklistItemModel["risk"] })}
                  >
                    {RISKS.map((r) => (
                      <option key={r}>{r}</option>
                    ))}
                  </select>
                  {it.is_subjective ? (
                    <span className="flex-1 italic text-gray-400">
                      Free-text answer — the model writes a short, precise response (no Yes/No).
                    </span>
                  ) : (
                    <>
                      <select
                        className="rounded border px-1.5 py-1"
                        value={it.answer_type}
                        onChange={(e) =>
                          update(idx, { answer_type: e.target.value as ChecklistItemModel["answer_type"] })
                        }
                      >
                        {ANSWER_TYPES.map((t) => (
                          <option key={t}>{t}</option>
                        ))}
                      </select>
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
                    </>
                  )}
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
