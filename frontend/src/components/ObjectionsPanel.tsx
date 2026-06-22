import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

// Append-only objection log (from the feedback agent): call id + upload time + objection text.
export function ObjectionsPanel({
  portfolioId,
  onClose,
}: {
  portfolioId: string;
  onClose: () => void;
}) {
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ["objection-log", portfolioId],
    queryFn: () => api.getObjectionLog(portfolioId),
  });

  return (
    <div className="mx-auto max-w-4xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <button onClick={onClose} className="text-sm text-[#c0567f] hover:underline">
          ← Back
        </button>
        <button
          onClick={() => api.downloadObjectionCsv(portfolioId)}
          disabled={!rows.length}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-50"
        >
          Download CSV
        </button>
      </div>
      <h2 className="mb-3 text-lg font-semibold text-ink">Key Objections</h2>
      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white/70">
        <table className="w-full text-sm">
          <thead className="bg-gray-50/90 text-left text-xs uppercase text-gray-500">
            <tr>
              <th className="px-4 py-2">Call</th>
              <th className="px-4 py-2">Agent</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Uploaded</th>
              <th className="px-4 py-2">Objection</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-gray-200/60">
                <td className="px-4 py-2 font-mono text-xs text-gray-600">
                  {r.call_id.slice(0, 8)}
                </td>
                <td className="px-4 py-2 text-ink">{r.agent ?? "—"}</td>
                <td className="px-4 py-2">
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-semibold ${
                      r.cleared ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"
                    }`}
                  >
                    {r.cleared ? "PASS" : "FAIL"}
                  </span>
                </td>
                <td className="px-4 py-2 text-gray-500">
                  {new Date(r.created_at).toLocaleString()}
                </td>
                <td className="px-4 py-2 text-ink">{r.text}</td>
              </tr>
            ))}
            {!isLoading && rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-gray-400">
                  No objections logged yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
