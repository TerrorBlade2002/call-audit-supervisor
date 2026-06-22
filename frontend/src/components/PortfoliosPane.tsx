import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useNav } from "../lib/nav";
import { useRoles } from "../lib/roles";
import { IconPencil, IconPin, IconPlus, IconTrash, PANE, Pager } from "./ui";

const LIMIT = 25;
const initialOf = (s: string) => (s.trim()[0] || "?").toUpperCase();

export function PortfoliosPane({
  selectedId,
  collapsed,
  pinned,
  setPinned,
  onSelect,
  onUsers,
}: {
  selectedId: string | null;
  collapsed: boolean;
  pinned: boolean;
  setPinned: (v: boolean) => void;
  onSelect: (id: string) => void;
  onUsers: (id: string) => void;
}) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["portfolios", offset],
    queryFn: () => api.listPortfolios({ limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  const portfolios = data?.items ?? [];

  const setRoles = useRoles((s) => s.setRoles);
  const setOrgAdmin = useRoles((s) => s.setOrgAdmin);
  const setPortfolioName = useNav((s) => s.setPortfolioName);
  useEffect(() => {
    if (portfolios.length) {
      setRoles(Object.fromEntries(portfolios.map((p) => [p.id, p.my_role ?? ""])));
    }
  }, [portfolios, setRoles]);
  useEffect(() => {
    if (data) setOrgAdmin(data.isOrgAdmin);
  }, [data, setOrgAdmin]);
  useEffect(() => {
    if (!selectedId) return;
    const match = portfolios.find((p) => p.id === selectedId);
    if (match) setPortfolioName(match.name);
  }, [portfolios, selectedId, setPortfolioName]);
  // When the user can only see one portfolio (supervisors/agents), open it straight away. Once.
  const didAuto = useRef(false);
  useEffect(() => {
    if (didAuto.current || selectedId) return;
    if (data && data.total === 1 && portfolios.length === 1) {
      didAuto.current = true;
      onSelect(portfolios[0].id);
    }
  }, [data, portfolios, selectedId, onSelect]);
  const isAdmin = data?.isOrgAdmin ?? false; // only the super admin manages portfolios

  const invalidate = () => qc.invalidateQueries({ queryKey: ["portfolios"] });
  const create = useMutation({
    mutationFn: (n: string) => api.createPortfolio(n),
    onSuccess: () => {
      invalidate();
      setAdding(false);
      setName("");
    },
  });
  const rename = useMutation({
    mutationFn: ({ id, n }: { id: string; n: string }) => api.renamePortfolio(id, n),
    onSuccess: () => {
      invalidate();
      setEditing(null);
    },
  });
  const del = useMutation({
    mutationFn: (id: string) => api.deletePortfolio(id),
    onSuccess: () => {
      invalidate();
      setConfirmDelete(null);
    },
  });

  // ── Collapsed icon rail ──────────────────────────────────────────────────────────────────
  if (collapsed) {
    return (
      <aside className={`${PANE} items-center gap-1 px-2 py-3`}>
        <span
          title="Portfolios — hover to expand"
          className="mb-1 flex h-9 w-9 items-center justify-center rounded-xl bg-[#dd9aa6]/20 text-[#8c3a55]"
        >
          <IconPin className="h-4 w-4" />
        </span>
        <div className="flex min-h-0 flex-1 flex-col items-center gap-1.5 overflow-auto">
          {portfolios.map((p) => (
            <button
              key={p.id}
              title={p.name}
              onClick={() => onSelect(p.id)}
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-sm font-semibold transition ${
                selectedId === p.id
                  ? "bg-[#dd9aa6] text-white shadow"
                  : "bg-white/70 text-[#8c3a55] hover:bg-[#dd9aa6]/25"
              }`}
            >
              {initialOf(p.name)}
            </button>
          ))}
          {isAdmin && (
            <button
              title="New portfolio"
              onClick={() => setPinned(true)}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-dashed border-[#c98aa0] text-[#8c3a55] hover:bg-[#dd9aa6]/10"
            >
              <IconPlus />
            </button>
          )}
        </div>
      </aside>
    );
  }

  // ── Expanded panel ───────────────────────────────────────────────────────────────────────
  return (
    <aside className={PANE}>
      <div className="flex items-center justify-between border-b border-white/50 px-3 py-2.5">
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setPinned(!pinned)}
            title={pinned ? "Unpin (collapse to icons)" : "Pin sidebar open"}
            className={`rounded-lg p-1.5 transition hover:bg-black/5 ${pinned ? "text-[#8c3a55]" : "text-gray-400 hover:text-gray-700"}`}
          >
            <IconPin filled={pinned} />
          </button>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Portfolios</h2>
        </div>
        {isAdmin && (
          <button
            className="text-sm font-medium text-[#c0567f] hover:underline"
            onClick={() => setAdding(true)}
          >
            + New
          </button>
        )}
      </div>
      {adding && (
        <form
          className="flex gap-1 border-b border-white/50 p-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate(name.trim());
          }}
        >
          <input
            autoFocus
            className="min-w-0 flex-1 rounded-lg border border-white/70 bg-white/70 px-2 py-1 text-sm outline-none focus:border-[#d28aa6]"
            placeholder="Portfolio name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button className="rounded-lg bg-[#dd9aa6] px-2 text-sm font-medium text-white">Add</button>
        </form>
      )}
      <div className="min-h-0 flex-1 overflow-auto p-1.5">
        {isLoading && <p className="p-3 text-sm text-gray-400">Loading…</p>}
        {portfolios.map((p) => (
          <div
            key={p.id}
            className={`group mb-0.5 rounded-lg px-1 ${
              selectedId === p.id ? "bg-[#dd9aa6]/25" : "hover:bg-black/5"
            }`}
          >
            {editing?.id === p.id ? (
              <form
                className="flex gap-1 p-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (editing.name.trim()) rename.mutate({ id: p.id, n: editing.name.trim() });
                }}
              >
                <input
                  autoFocus
                  className="min-w-0 flex-1 rounded border border-white/70 bg-white/80 px-2 py-0.5 text-sm outline-none"
                  value={editing.name}
                  onChange={(e) => setEditing({ id: p.id, name: e.target.value })}
                  onBlur={() => editing.name.trim() && rename.mutate({ id: p.id, n: editing.name.trim() })}
                />
                <button className="rounded bg-[#dd9aa6] px-2 text-xs font-medium text-white">Save</button>
              </form>
            ) : confirmDelete === p.id ? (
              <div className="flex items-center justify-between gap-1 px-2 py-1.5 text-sm">
                <span className="truncate text-rose-700">Delete “{p.name}”?</span>
                <span className="flex shrink-0 gap-1">
                  <button
                    onClick={() => del.mutate(p.id)}
                    className="rounded bg-rose-500 px-2 py-0.5 text-xs font-semibold text-white"
                  >
                    Yes
                  </button>
                  <button
                    onClick={() => setConfirmDelete(null)}
                    className="rounded border border-gray-300 px-2 py-0.5 text-xs"
                  >
                    No
                  </button>
                </span>
              </div>
            ) : (
              <div className="flex items-center justify-between gap-1">
                <button
                  onClick={() => onSelect(p.id)}
                  className="flex min-w-0 flex-1 items-center gap-2 px-1.5 py-2 text-left"
                >
                  <span
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs font-semibold ${
                      selectedId === p.id ? "bg-[#dd9aa6] text-white" : "bg-white/70 text-[#8c3a55]"
                    }`}
                  >
                    {initialOf(p.name)}
                  </span>
                  <span
                    className={`min-w-0 truncate text-sm ${
                      selectedId === p.id ? "font-semibold text-[#8c3a55]" : "text-ink"
                    }`}
                  >
                    {p.name}
                  </span>
                </button>
                {isAdmin && (
                  <div className="flex shrink-0 items-center gap-0.5 pr-1 opacity-0 transition group-hover:opacity-100">
                    <button
                      title="Manage supervisors & agents"
                      onClick={() => onUsers(p.id)}
                      className="rounded px-1 py-0.5 text-[0.7rem] font-medium text-gray-500 hover:bg-black/5 hover:text-gray-800"
                    >
                      Users
                    </button>
                    <button
                      title="Rename portfolio"
                      onClick={() => setEditing({ id: p.id, name: p.name })}
                      className="rounded p-1 text-gray-400 hover:bg-black/5 hover:text-gray-700"
                    >
                      <IconPencil />
                    </button>
                    <button
                      title="Delete portfolio"
                      onClick={() => setConfirmDelete(p.id)}
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
        {isError ? (
          <p className="p-3 text-sm text-amber-600">Couldn't load portfolios — reconnecting…</p>
        ) : (
          !isLoading &&
          portfolios.length === 0 && (
            <p className="p-3 text-sm text-gray-400">No portfolios yet.</p>
          )
        )}
      </div>
      <Pager offset={offset} limit={LIMIT} total={data?.total ?? 0} onPage={setOffset} />
    </aside>
  );
}
