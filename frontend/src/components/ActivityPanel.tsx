import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export function ActivityPanel({ onClose }: { onClose: () => void }) {
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ["activity"],
    queryFn: () => api.getActivity(),
    refetchInterval: 5000,
  });

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <button onClick={onClose} className="text-sm font-medium text-[#c0567f] hover:underline">
          ← Back to calls
        </button>
        <h2 className="text-lg font-semibold text-ink">Activity Log</h2>
        <span className="text-xs text-gray-400">super admin only</span>
      </div>

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        {isLoading && <p className="p-4 text-sm text-gray-400">Loading…</p>}
        {rows.map((r, i) => (
          <div
            key={i}
            className="flex items-center gap-2 border-b border-gray-100 px-4 py-2.5 text-sm last:border-0"
          >
            <span className="font-medium text-ink">{r.actor}</span>
            <span className="text-gray-500">{r.action}</span>
          </div>
        ))}
        {!isLoading && rows.length === 0 && (
          <p className="p-6 text-center text-sm text-gray-400">No activity recorded yet.</p>
        )}
      </div>
      <p className="mt-3 text-xs text-gray-500">
        Every add / modify / delete across all portfolios — who did what.
      </p>
    </div>
  );
}
