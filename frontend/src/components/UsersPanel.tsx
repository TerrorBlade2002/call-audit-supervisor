import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { useNav } from "../lib/nav";
import { IconChevronLeft, IconTrash } from "./ui";

// Super-admin middle-pane view to create / list / remove a portfolio's supervisors & agents.
// Replaces the old cramped modal — full width, labelled fields, and explicit navigation back to
// the portfolio list or into the folders for this portfolio.
export function UsersPanel({
  portfolioId,
  onBackToPortfolios,
  onOpenFolders,
}: {
  portfolioId: string;
  onBackToPortfolios: () => void;
  onOpenFolders: () => void;
}) {
  const qc = useQueryClient();
  const portfolioName = useNav((s) => s.portfolioName);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("SUPERVISOR");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const { data: users = [] } = useQuery({
    queryKey: ["portfolio-users", portfolioId],
    queryFn: () => api.listPortfolioUsers(portfolioId),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["portfolio-users", portfolioId] });

  const create = useMutation({
    mutationFn: () => api.createPortfolioUser(portfolioId, email.trim().toLowerCase(), password, role),
    onSuccess: () => {
      setMsg({ kind: "ok", text: `Saved ${email.trim().toLowerCase()} as ${role.toLowerCase()}.` });
      setEmail("");
      setPassword("");
      invalidate();
    },
    onError: (e) =>
      setMsg({
        kind: "err",
        text: e instanceof ApiError ? `Couldn't save (${e.status}).` : "Couldn't save user.",
      }),
  });
  const del = useMutation({
    mutationFn: (userId: string) => api.deletePortfolioUser(portfolioId, userId),
    onSuccess: invalidate,
  });

  const label = "mb-1 block text-xs font-semibold uppercase tracking-wide text-gray-500";
  const field =
    "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm outline-none focus:border-[#d28aa6]";

  return (
    <div className="mx-auto max-w-3xl p-6">
      <button
        onClick={onBackToPortfolios}
        className="mb-3 flex items-center gap-1 rounded-lg px-1.5 py-1 text-sm font-medium text-[#c0567f] hover:bg-black/5"
      >
        <IconChevronLeft className="h-4 w-4" /> Choose portfolio
      </button>

      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-ink">Users</h2>
          <p className="text-sm text-gray-500">
            People who can sign in to{" "}
            <span className="font-medium text-ink">{portfolioName || "this portfolio"}</span>.
          </p>
        </div>
        <button
          onClick={onOpenFolders}
          className="rounded-lg border border-[#c98aa0] px-3 py-1.5 text-sm font-medium text-[#8c3a55] hover:bg-[#dd9aa6]/10"
        >
          Open folders →
        </button>
      </div>

      <form
        className="rounded-2xl border border-gray-200 bg-white/70 p-5 shadow-sm"
        onSubmit={(e) => {
          e.preventDefault();
          if (email.trim() && password) create.mutate();
        }}
      >
        <h3 className="mb-4 text-sm font-semibold text-ink">Add or update a user</h3>
        <div className="space-y-4">
          <div>
            <label htmlFor="up-email" className={label}>
              Email address
            </label>
            <input
              id="up-email"
              className={field}
              type="email"
              placeholder="name@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="flex flex-wrap gap-4">
            <div className="min-w-[14rem] flex-1">
              <label htmlFor="up-password" className={label}>
                Password
              </label>
              <input
                id="up-password"
                className={field}
                type="text"
                placeholder="At least 6 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
              />
            </div>
            <div className="w-44">
              <label htmlFor="up-role" className={label}>
                Role
              </label>
              <select
                id="up-role"
                className={field}
                value={role}
                onChange={(e) => setRole(e.target.value)}
              >
                <option value="SUPERVISOR">Supervisor</option>
                <option value="AGENT">Agent</option>
              </select>
            </div>
          </div>
        </div>
        <div className="mt-5 flex items-center gap-3">
          <button
            disabled={create.isPending}
            className="rounded-lg bg-[#dd9aa6] px-5 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            {create.isPending ? "Saving…" : "Save user"}
          </button>
          {msg && (
            <span className={`text-sm ${msg.kind === "err" ? "text-rose-600" : "text-emerald-600"}`}>
              {msg.text}
            </span>
          )}
        </div>
      </form>

      <h3 className="mb-2 mt-7 text-sm font-semibold text-ink">Existing users</h3>
      <div className="space-y-1.5">
        {users.length === 0 && (
          <p className="rounded-xl border border-dashed border-gray-300 bg-white/50 px-4 py-6 text-center text-sm text-gray-400">
            No users yet — add one above.
          </p>
        )}
        {users.map((u) => (
          <div
            key={u.id}
            className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white/70 px-4 py-3"
          >
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#dd9aa6]/20 text-sm font-semibold text-[#8c3a55]">
                {(u.email[0] || "?").toUpperCase()}
              </span>
              <span className="min-w-0">
                <span className="block break-all text-sm text-ink">{u.email}</span>
                <span className="text-xs capitalize text-gray-400">{u.role.toLowerCase()}</span>
              </span>
            </div>
            <button
              onClick={() => {
                if (window.confirm(`Remove ${u.email} from this portfolio?`)) del.mutate(u.id);
              }}
              title="Remove from portfolio"
              className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-red-500 hover:bg-red-50"
            >
              <IconTrash className="h-3.5 w-3.5" /> Remove
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
