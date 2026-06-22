import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { useCaps } from "../lib/roles";
import { useToasts } from "../lib/toast";
import { IconPencil, IconTrash, Pager, Spinner } from "./ui";
import { UploadDialog } from "./UploadDialog";

const LIMIT = 24;

// Level-1 surface (main area): the folders inside a portfolio, shown as roomy cards. Clicking a
// card drills into that folder's calls. Managers can create / rename / delete folders and upload
// recordings straight from a card.
export function FoldersGrid({
  portfolioId,
  onOpen,
}: {
  portfolioId: string;
  onOpen: (agentId: string, name: string) => void;
}) {
  const qc = useQueryClient();
  const caps = useCaps(portfolioId);
  const push = useToasts((s) => s.push);
  const [offset, setOffset] = useState(0);
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const [uploadFor, setUploadFor] = useState<{ id: string; name: string } | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["agents", portfolioId, offset],
    queryFn: () => api.listAgents(portfolioId, { limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });
  const folders = data?.items ?? [];

  const invalidate = () => qc.invalidateQueries({ queryKey: ["agents", portfolioId] });
  const create = useMutation({
    mutationFn: (n: string) => api.createAgent(portfolioId, n),
    onSuccess: () => {
      invalidate();
      setName("");
    },
  });
  const rename = useMutation({
    mutationFn: ({ id, n }: { id: string; n: string }) => api.renameAgent(portfolioId, id, n),
    onSuccess: () => {
      invalidate();
      setEditing(null);
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteAgent(portfolioId, id),
    onSuccess: invalidate,
  });

  return (
    <div className="flex h-full flex-col p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">Folders</h2>
          <p className="text-sm text-gray-500">
            {data?.total ?? 0} folder(s) · pick one to see its calls
          </p>
        </div>
        {caps.canManage && (
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate(name.trim());
            }}
          >
            <input
              className="w-52 rounded-lg border border-white/70 bg-white/80 px-3 py-1.5 text-sm outline-none focus:border-[#d28aa6]"
              placeholder="New folder name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button
              disabled={!name.trim() || create.isPending}
              className="rounded-lg bg-[#dd9aa6] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              ＋ Add folder
            </button>
          </form>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto pr-1">
        {isLoading && (
          <p className="flex items-center gap-2 p-3 text-sm text-gray-400">
            <Spinner /> Loading folders…
          </p>
        )}
        {!isLoading && folders.length === 0 && (
          <div className="rounded-2xl border border-dashed border-gray-300 bg-white/50 px-6 py-12 text-center">
            <p className="text-sm text-gray-500">No folders yet.</p>
            {caps.canManage && (
              <p className="mt-1 text-xs text-gray-400">
                Create one above to start uploading recordings.
              </p>
            )}
          </div>
        )}

        <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
          {folders.map((f) => (
            <div
              key={f.id}
              className="group relative flex flex-col justify-between rounded-2xl border border-white/60 bg-white/80 p-4 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
            >
              {editing?.id === f.id ? (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (editing.name.trim()) rename.mutate({ id: f.id, n: editing.name.trim() });
                  }}
                >
                  <input
                    autoFocus
                    className="w-full rounded-lg border border-gray-300 bg-white px-2 py-1 text-sm outline-none focus:border-[#d28aa6]"
                    value={editing.name}
                    onChange={(e) => setEditing({ id: f.id, name: e.target.value })}
                    onBlur={() => editing.name.trim() && rename.mutate({ id: f.id, n: editing.name.trim() })}
                  />
                  <div className="mt-2 flex gap-1.5">
                    <button className="rounded bg-[#dd9aa6] px-2 py-0.5 text-xs font-medium text-white">
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditing(null)}
                      className="rounded border border-gray-300 px-2 py-0.5 text-xs"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                <>
                  <button
                    onClick={() => onOpen(f.id, f.name)}
                    className="flex items-start gap-2 text-left"
                  >
                    <span className="mt-0.5 text-xl leading-none">📁</span>
                    <span className="min-w-0">
                      <span className="block break-words text-sm font-semibold text-ink group-hover:text-[#8c3a55]">
                        {f.name}
                      </span>
                      <span className="text-xs text-gray-400">Open calls →</span>
                    </span>
                  </button>

                  {caps.canManage && (
                    <div className="mt-3 flex items-center gap-1.5 border-t border-gray-100 pt-2">
                      <button
                        onClick={() => setUploadFor({ id: f.id, name: f.name })}
                        className="rounded-lg border border-[#c98aa0] px-2 py-1 text-xs font-medium text-[#8c3a55] hover:bg-[#dd9aa6]/10"
                      >
                        ⬆ Upload
                      </button>
                      <span className="flex-1" />
                      <button
                        title="Rename folder"
                        onClick={() => setEditing({ id: f.id, name: f.name })}
                        className="rounded p-1 text-gray-400 hover:bg-black/5 hover:text-gray-700"
                      >
                        <IconPencil />
                      </button>
                      <button
                        title="Remove folder and all its recordings"
                        onClick={() => {
                          if (
                            window.confirm(
                              `Remove folder “${f.name}” and all its recordings/reports?`,
                            )
                          )
                            remove.mutate(f.id);
                        }}
                        className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
                      >
                        <IconTrash className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      </div>

      <Pager offset={offset} limit={LIMIT} total={data?.total ?? 0} onPage={setOffset} />

      {uploadFor && (
        <UploadDialog
          portfolioId={portfolioId}
          agentId={uploadFor.id}
          onClose={() => setUploadFor(null)}
          onUploaded={(n) => {
            qc.invalidateQueries({ queryKey: ["calls", portfolioId] });
            push(`Queued ${n} recording(s) in “${uploadFor.name}”.`, "success");
          }}
        />
      )}
    </div>
  );
}
