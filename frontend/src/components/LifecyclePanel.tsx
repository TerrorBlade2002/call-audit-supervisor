import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";

// Super-admin observability: per-call analysis lifecycle (STT → which agents ran → report) +
// failure tracebacks. Read-only; everything is derived from the durable job/report state.
export function LifecyclePanel({ onClose }: { onClose: () => void }) {
  const [selected, setSelected] = useState<string | null>(null);

  const { data: calls = [], isLoading } = useQuery({
    queryKey: ["lifecycle"],
    queryFn: () => api.getLifecycle(),
    refetchInterval: 5000,
  });
  const { data: detail } = useQuery({
    queryKey: ["lifecycle-detail", selected],
    queryFn: () => api.getLifecycleDetail(selected!),
    enabled: !!selected,
  });

  const stateColor = (s: string | null) =>
    s === "DONE"
      ? "text-emerald-700"
      : s === "FAILED"
        ? "text-rose-700"
        : "text-amber-700";

  return (
    <div className="mx-auto max-w-5xl p-6">
      <button onClick={onClose} className="mb-4 text-sm text-[#c0567f] hover:underline">
        ← Back
      </button>
      <h2 className="mb-1 text-lg font-semibold text-ink">Call lifecycle &amp; debug</h2>
      <p className="mb-3 text-xs text-gray-500">
        Each call's flow from upload → STT → agents → report, with failure tracebacks.
      </p>

      <div className="grid gap-4 md:grid-cols-2">
        {/* list */}
        <div className="max-h-[70vh] overflow-auto rounded-xl border border-gray-200 bg-white/70">
          {isLoading && <p className="p-3 text-sm text-gray-400">Loading…</p>}
          {calls.map((c) => (
            <button
              key={c.call_id}
              onClick={() => setSelected(c.call_id)}
              className={`block w-full border-b border-gray-100 px-3 py-2 text-left text-sm last:border-0 ${
                selected === c.call_id ? "bg-[#dd9aa6]/15" : "hover:bg-gray-50"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs text-gray-600">{c.call_id.slice(0, 8)}</span>
                <span className={`text-xs font-semibold ${stateColor(c.state)}`}>
                  {c.state ?? "—"}
                </span>
              </div>
              <div className="text-xs text-gray-500">
                {c.folder ?? "—"} · {c.option ?? "—"}
                {c.attempts > 1 && <span className="text-amber-600"> · {c.attempts} attempts</span>}
              </div>
            </button>
          ))}
          {!isLoading && calls.length === 0 && (
            <p className="p-3 text-sm text-gray-400">No calls yet.</p>
          )}
        </div>

        {/* detail */}
        <div className="rounded-xl border border-gray-200 bg-white/70 p-3">
          {!detail ? (
            <p className="text-sm text-gray-400">Select a call to see its lifecycle.</p>
          ) : (
            <div className="space-y-3 text-sm">
              <div className="text-xs text-gray-500">
                {detail.folder} · agent: {detail.agent_name ?? "—"} · option {detail.option} ·{" "}
                <span className={stateColor(detail.state)}>{detail.state}</span>
              </div>
              <ol className="space-y-1.5">
                {detail.steps.map((s, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className={s.ok ? "text-emerald-600" : "text-gray-400"}>
                      {s.ok ? "✓" : "○"}
                    </span>
                    <span>
                      <span className="font-medium text-ink">{s.step}</span>
                      {s.detail && <span className="text-gray-500"> — {s.detail}</span>}
                      {s.at && (
                        <span className="block text-xs text-gray-400">
                          {new Date(s.at).toLocaleString()}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
              {detail.errors.length > 0 && (
                <div>
                  <h3 className="mb-1 text-sm font-semibold text-rose-700">
                    Errors ({detail.errors.length})
                  </h3>
                  {detail.errors.map((e, i) => (
                    <details key={i} className="mb-2 rounded-lg border border-rose-200 bg-rose-50/60 p-2">
                      <summary className="cursor-pointer text-xs font-medium text-rose-700">
                        {e.stage} · attempt {e.attempt} · {e.error_class}
                        {e.fatal && " · FATAL"}
                      </summary>
                      {e.message && <p className="mt-1 text-xs text-rose-800">{e.message}</p>}
                      {e.traceback && (
                        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-white/80 p-2 text-[0.7rem] text-gray-700">
                          {e.traceback}
                        </pre>
                      )}
                    </details>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
