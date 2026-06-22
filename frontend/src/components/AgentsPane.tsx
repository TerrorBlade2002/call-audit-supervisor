import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useNav } from "../lib/nav";
import { useCaps } from "../lib/roles";
import { IconChevronLeft, IconPencil, IconPin, IconTrash, PANE, Pager } from "./ui";

const LIMIT = 25;
const initialOf = (s: string) => (s.trim()[0] || "?").toUpperCase();

// Level-2 left sidebar: folders inside the active portfolio. Collapses to an icon rail that
// expands on hover (App controls the width); "‹ Portfolios" steps back to the folder grid.
export function AgentsPane({
  portfolioId,
  selectedId,
  collapsed,
  pinned,
  setPinned,
  onSelect,
  onBack,
}: {
  portfolioId: string;
  selectedId: string | null;
  collapsed: boolean;
  pinned: boolean;
  setPinned: (v: boolean) => void;
  onSelect: (id: string, name: string) => void;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const caps = useCaps(portfolioId);
  const setFolderName = useNav((s) => s.setFolderName);
  const [name, setName] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);

  const { data } = useQuery({
    queryKey: ["agents", portfolioId, offset],
    queryFn: () => api.listAgents(portfolioId, { limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  const agents = data?.items ?? [];

  useEffect(() => {
    if (!selectedId) return;
    const match = agents.find((a) => a.id === selectedId);
    if (match) setFolderName(match.name);
  }, [agents, selectedId, setFolderName]);

  const create = useMutation({
    mutationFn: (n: string) => api.createAgent(portfolioId, n),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents", portfolioId] });
      setName("");
    },
  });
  const rename = useMutation({
    mutationFn: ({ aid, n }: { aid: string; n: string }) => api.renameAgent(portfolioId, aid, n),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents", portfolioId] });
      setEditing(null);
    },
  });
  const removeAgent = useMutation({
    mutationFn: (aid: string) => api.deleteAgent(portfolioId, aid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents", portfolioId] }),
  });

  // ── Collapsed icon rail ──────────────────────────────────────────────────────────────────
  if (collapsed) {
    return (
      <aside className={`${PANE} items-center gap-1 px-2 py-3`}>
        <button
          title="Back to portfolios"
          onClick={onBack}
          className="mb-1 flex h-9 w-9 items-center justify-center rounded-xl bg-white/70 text-[#8c3a55] hover:bg-[#dd9aa6]/25"
        >
          <IconChevronLeft />
        </button>
        <div className="flex min-h-0 flex-1 flex-col items-center gap-1.5 overflow-auto">
          {agents.map((a) => (
            <button
              key={a.id}
              title={a.name}
              onClick={() => onSelect(a.id, a.name)}
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-sm font-semibold transition ${
                selectedId === a.id
                  ? "bg-[#dd9aa6] text-white shadow"
                  : "bg-white/70 text-[#8c3a55] hover:bg-[#dd9aa6]/25"
              }`}
            >
              {initialOf(a.name)}
            </button>
          ))}
        </div>
      </aside>
    );
  }

  // ── Expanded panel ───────────────────────────────────────────────────────────────────────
  return (
    <aside className={PANE}>
      <div className="border-b border-white/50 px-3 py-2.5">
        <div className="mb-2 flex items-center gap-1.5">
          <button
            onClick={() => setPinned(!pinned)}
            title={pinned ? "Unpin (collapse to icons)" : "Pin sidebar open"}
            className={`rounded-lg p-1.5 transition hover:bg-black/5 ${pinned ? "text-[#8c3a55]" : "text-gray-400 hover:text-gray-700"}`}
          >
            <IconPin filled={pinned} />
          </button>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Folders</h2>
        </div>
        <button
          onClick={onBack}
          className="flex items-center gap-1 rounded-lg px-1.5 py-1 text-xs font-medium text-[#c0567f] hover:bg-black/5"
        >
          <IconChevronLeft className="h-3.5 w-3.5" /> Portfolios
        </button>
        {caps.canManage && (
          <form
            className="mt-2 flex gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate(name.trim());
            }}
          >
            <input
              className="min-w-0 flex-1 rounded-lg border border-white/70 bg-white/70 px-2 py-1 text-sm outline-none focus:border-[#d28aa6]"
              placeholder="New folder name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button className="rounded-lg bg-[#dd9aa6] px-2 text-sm font-medium text-white">Add</button>
          </form>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-1.5">
        {agents.map((a) => (
          <div
            key={a.id}
            className={`group mb-0.5 rounded-lg px-1 ${
              selectedId === a.id ? "bg-[#dd9aa6]/25" : "hover:bg-black/5"
            }`}
          >
            {editing?.id === a.id ? (
              <form
                className="flex gap-1 p-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (editing.name.trim()) rename.mutate({ aid: a.id, n: editing.name.trim() });
                }}
              >
                <input
                  autoFocus
                  className="min-w-0 flex-1 rounded border border-white/70 bg-white/80 px-2 py-0.5 text-sm outline-none"
                  value={editing.name}
                  onChange={(e) => setEditing({ id: a.id, name: e.target.value })}
                  onBlur={() => setEditing(null)}
                />
                <button className="rounded bg-[#dd9aa6] px-2 text-xs font-medium text-white">
                  Save
                </button>
              </form>
            ) : (
              <div className="flex items-center justify-between gap-1">
                <button
                  onClick={() => onSelect(a.id, a.name)}
                  className="flex min-w-0 flex-1 items-center gap-2 px-1.5 py-2 text-left"
                >
                  <span
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs font-semibold ${
                      selectedId === a.id ? "bg-[#dd9aa6] text-white" : "bg-white/70 text-[#8c3a55]"
                    }`}
                  >
                    {initialOf(a.name)}
                  </span>
                  <span
                    className={`min-w-0 truncate text-sm ${
                      selectedId === a.id ? "font-semibold text-[#8c3a55]" : "text-ink"
                    }`}
                  >
                    {a.name}
                  </span>
                </button>
                {caps.canManage && (
                  <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition group-hover:opacity-100">
                    <button
                      title="Rename folder"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => setEditing({ id: a.id, name: a.name })}
                      className="rounded p-1 text-gray-400 hover:bg-black/5 hover:text-gray-700"
                    >
                      <IconPencil />
                    </button>
                    <button
                      title="Remove folder and all its recordings"
                      onClick={() => {
                        if (window.confirm(`Remove folder “${a.name}” and all its recordings/reports?`))
                          removeAgent.mutate(a.id);
                      }}
                      className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
                    >
                      <IconTrash className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        {agents.length === 0 && <p className="p-3 text-sm text-gray-400">No folders yet.</p>}
      </div>
      <Pager offset={offset} limit={LIMIT} total={data?.total ?? 0} onPage={setOffset} />
    </aside>
  );
}
