import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

// Append-only raw-transcript log: call id + folder + upload time, with a per-call .txt download.
export function TranscriptsPanel({
  portfolioId,
  onClose,
}: {
  portfolioId: string;
  onClose: () => void;
}) {
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ["transcript-log", portfolioId],
    queryFn: () => api.getTranscriptLog(portfolioId),
  });

  return (
    <div className="mx-auto max-w-4xl p-6">
      <button onClick={onClose} className="mb-4 text-sm text-[#c0567f] hover:underline">
        ← Back
      </button>
      <h2 className="mb-3 text-lg font-semibold text-ink">Raw Transcripts</h2>
      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white/70">
        <table className="w-full text-sm">
          <thead className="bg-gray-50/90 text-left text-xs uppercase text-gray-500">
            <tr>
              <th className="px-4 py-2">Call</th>
              <th className="px-4 py-2">Folder</th>
              <th className="px-4 py-2">Uploaded</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.call_id} className="border-t border-gray-200/60">
                <td className="px-4 py-2 font-mono text-xs text-gray-600">
                  {r.call_id.slice(0, 8)}
                </td>
                <td className="px-4 py-2 text-ink">{r.agent_name ?? "—"}</td>
                <td className="px-4 py-2 text-gray-500">
                  {new Date(r.created_at).toLocaleString()}
                </td>
                <td className="px-4 py-2 text-right">
                  <button
                    onClick={() => api.downloadTranscript(portfolioId, r.call_id)}
                    className="text-xs font-medium text-[#c0567f] hover:underline"
                  >
                    Download .txt
                  </button>
                </td>
              </tr>
            ))}
            {!isLoading && rows.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-gray-400">
                  No transcripts yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
