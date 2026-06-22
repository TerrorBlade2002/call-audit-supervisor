import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

// In-app batch "smoke" triage for checklist results (deterministic). Feedback summary is
// download-only (HTML). Goal: decide the next 3 calls to inspect in ~20 seconds.
export function BatchSummary({
  portfolioId,
  batchId,
  onOpenReport,
  onClose,
}: {
  portfolioId: string;
  batchId: string;
  onOpenReport: (reportId: string) => void;
  onClose: () => void;
}) {
  const { data: s, isLoading } = useQuery({
    queryKey: ["checklist-summary", portfolioId, batchId],
    queryFn: () => api.getChecklistSummary(portfolioId, batchId),
  });

  const tile = (label: string, value: number, warn = false) => (
    <span className="rounded-lg border border-gray-200 bg-white/70 px-3 py-1.5 text-sm">
      <span className={`font-semibold ${warn && value > 0 ? "text-rose-600" : "text-ink"}`}>
        {value}
      </span>{" "}
      <span className="text-gray-500">{label}</span>
    </span>
  );

  return (
    <div className="mx-auto max-w-4xl p-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <button onClick={onClose} className="text-sm text-[#c0567f] hover:underline">
          ← Back to calls
        </button>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => api.downloadChecklistSummaryCsv(portfolioId, batchId)}
            className="rounded-lg border border-gray-300 px-2.5 py-1 text-sm hover:bg-gray-50"
          >
            ⤓ Checklist CSV
          </button>
          <button
            onClick={() => api.downloadFeedbackSummaryHtml(portfolioId, batchId)}
            className="rounded-lg border border-gray-300 px-2.5 py-1 text-sm hover:bg-gray-50"
            title="Per-agent coaching briefs + objection rollups (HTML download)"
          >
            ⤓ Feedback summary (HTML)
          </button>
        </div>
      </div>
      <h2 className="mb-3 text-lg font-semibold text-ink">Batch summary</h2>

      {isLoading || !s ? (
        <p className="text-sm text-gray-400">Loading…</p>
      ) : (
        <div className="space-y-6">
          {/* batch line */}
          <div className="flex flex-wrap gap-2">
            {tile("calls", s.total_calls)}
            {tile("agents", s.agents)}
            {tile("clean", s.clean)}
            {tile("need review", s.need_review, true)}
            {tile("critical fails", s.critical_fails, true)}
            {tile("failed processing", s.failed_processing, true)}
            {s.missing_agent_name > 0 && tile("no agent name", s.missing_agent_name, true)}
          </div>

          {s.per_agent.length === 0 ? (
            <p className="rounded-lg border border-gray-200 bg-white/70 p-4 text-sm text-gray-500">
              No checklist results in this batch (it may be feedback-only or raw-only). Use the
              feedback summary download above.
            </p>
          ) : (
            <>
              {/* per-agent snapshot */}
              <section>
                <h3 className="mb-2 text-sm font-semibold text-ink">Per-agent snapshot</h3>
                <div className="overflow-hidden rounded-lg border border-gray-200 bg-white/70">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50/80 text-left text-xs uppercase text-gray-500">
                      <tr>
                        <th className="px-3 py-2">Agent</th>
                        <th className="px-3 py-2">Calls</th>
                        <th className="px-3 py-2">Pass / Fail</th>
                        <th className="px-3 py-2">Critical</th>
                        <th className="px-3 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.per_agent.map((a) => (
                        <tr key={a.agent} className="border-t border-gray-200/60">
                          <td className="px-3 py-2 font-medium text-ink">
                            {a.agent}
                            {a.calls_fail_gt_pass > 0 && (
                              <span className="ml-2 rounded bg-rose-100 px-1.5 py-0.5 text-xs font-semibold text-rose-700">
                                ⚠ {a.calls_fail_gt_pass} fail&gt;pass
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-gray-600">{a.calls}</td>
                          <td className="px-3 py-2">
                            <span className="text-emerald-700">{a.passes}</span>
                            <span className="text-gray-400"> / </span>
                            <span className="text-rose-700">{a.fails}</span>
                          </td>
                          <td className="px-3 py-2">
                            <span className={a.critical_fails > 0 ? "font-semibold text-rose-700" : "text-gray-400"}>
                              {a.critical_fails}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-right">
                            {a.worst_report_id && (
                              <button
                                onClick={() => onOpenReport(a.worst_report_id!)}
                                className="text-xs font-medium text-[#c0567f] hover:underline"
                              >
                                Open worst
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              {/* top failed items */}
              <section>
                <h3 className="mb-2 text-sm font-semibold text-ink">
                  Top failed items (by # agents)
                </h3>
                <ul className="space-y-1">
                  {s.top_failed_items.map((it, i) => (
                    <li
                      key={i}
                      className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white/70 px-3 py-1.5 text-sm"
                    >
                      <span className="font-medium text-ink">{it.text}</span>
                      <span className="text-xs text-gray-400">· {it.section}</span>
                      {it.risk.toUpperCase() === "CRITICAL" && (
                        <span className="rounded bg-rose-100 px-1.5 py-0.5 text-xs font-semibold text-rose-700">
                          CRITICAL
                        </span>
                      )}
                      <span className="ml-auto text-xs text-gray-600">
                        failed by <b className="text-rose-700">{it.agents_failed}</b> agent(s) ·{" "}
                        {it.calls_failed} call(s)
                      </span>
                    </li>
                  ))}
                </ul>
              </section>

              {/* worst calls */}
              <section>
                <h3 className="mb-2 text-sm font-semibold text-ink">Worst calls to open first</h3>
                <ul className="space-y-1">
                  {s.worst_calls.map((c) => (
                    <li
                      key={c.report_id}
                      className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 bg-white/70 px-3 py-1.5 text-sm"
                    >
                      <span className="font-mono text-xs text-gray-500">{c.call_id.slice(0, 8)}</span>
                      <span className="text-ink">{c.agent}</span>
                      <span className="text-xs text-rose-700">{c.fails} fail</span>
                      {c.critical > 0 && (
                        <span className="text-xs font-semibold text-rose-700">
                          · {c.critical} critical
                        </span>
                      )}
                      {c.flagged && <span className="text-xs text-amber-600">· flagged</span>}
                      {c.needs_review && (
                        <span className="text-xs text-amber-600">· needs review</span>
                      )}
                      <button
                        onClick={() => onOpenReport(c.report_id)}
                        className="ml-auto text-xs font-medium text-[#c0567f] hover:underline"
                      >
                        Open report
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            </>
          )}
        </div>
      )}
    </div>
  );
}
