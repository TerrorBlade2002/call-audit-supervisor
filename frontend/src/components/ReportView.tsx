import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ApiError, type ReportItem } from "../lib/api";

interface Narrative {
  already_ideal?: boolean;
  coaching?: string;
  compliance?: string;
  feedback?: { strengths?: string[]; development?: string[] };
  ideal_conversation?: { speaker: string; text: string }[];
}

function Badge({ answer }: { answer: string | null }) {
  const a = (answer || "NA").toUpperCase();
  const cls =
    a === "PASS"
      ? "bg-emerald-400/15 text-emerald-300"
      : a === "FAIL"
        ? "bg-rose-400/15 text-rose-300"
        : "bg-slate-400/15 text-slate-300";
  return <span className={`rounded px-2 py-0.5 text-xs font-bold tracking-wide ${cls}`}>{a}</span>;
}

function overall(items: ReportItem[], compliance: boolean): string {
  const scope = items.filter(
    (i) => (i.section || "").toLowerCase().includes("complian") === compliance,
  );
  if (!scope.length) return "NA";
  return scope.some((i) => i.answer === "FAIL") ? "FAIL" : "PASS";
}

const AGENT_SIGNALS = [
  "this is an attempt to collect",
  "all calls are recorded",
  "everest receivable",
  "debt collector",
  "mini-miranda",
  "may i speak",
  "on behalf of",
  "verify the date of birth",
];

// Normalize any speaker label → "Agent"/"Consumer" deterministically (mirrors the backend
// renderer): keyword labels win; stray diarization labels (A/B) are disambiguated by which
// one's lines carry agent-only phrases. Keeps agent left, consumer right, every time.
function roleMap(turns: { speaker: string; text: string }[]): Record<string, "Agent" | "Consumer"> {
  const labels: string[] = [];
  for (const t of turns) {
    const s = (t.speaker || "").trim();
    if (!labels.includes(s)) labels.push(s);
  }
  const map: Record<string, "Agent" | "Consumer"> = {};
  const ambiguous: string[] = [];
  for (const lbl of labels) {
    const low = lbl.toLowerCase();
    if (low.startsWith("agent") || low.includes("collector") || low.includes("represent"))
      map[lbl] = "Agent";
    else if (
      low.startsWith("consumer") ||
      low.includes("customer") ||
      low.includes("debtor") ||
      low.includes("caller")
    )
      map[lbl] = "Consumer";
    else ambiguous.push(lbl);
  }
  if (ambiguous.length) {
    const scores: Record<string, number> = {};
    for (const lbl of ambiguous) {
      const text = turns
        .filter((t) => (t.speaker || "").trim() === lbl)
        .map((t) => t.text || "")
        .join(" ")
        .toLowerCase();
      scores[lbl] = AGENT_SIGNALS.reduce((n, sig) => n + (text.split(sig).length - 1), 0);
    }
    let agentLbl: string | null = null;
    let best = 0;
    for (const lbl of ambiguous)
      if (scores[lbl] > best) {
        best = scores[lbl];
        agentLbl = lbl;
      }
    for (const lbl of ambiguous)
      map[lbl] = lbl === agentLbl && scores[lbl] > 0 ? "Agent" : "Consumer";
  }
  return map;
}

function fmtTime(sec: number | null): string {
  if (sec == null) return "";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function ReportView({ reportId, onBack }: { reportId: string; onBack: () => void }) {
  const qc = useQueryClient();
  const { data: report, isLoading } = useQuery({
    queryKey: ["report", reportId],
    queryFn: () => api.getReport(reportId),
  });
  const [verifyNotes, setVerifyNotes] = useState("");
  const [verifyMsg, setVerifyMsg] = useState<string | null>(null);
  const [tab, setTab] = useState<string | null>(null); // selected individual report

  // Per-item notes held here so the explicit Save persists them all at once. Initialized once
  // from the loaded report (a window-focus refetch won't clobber unsaved edits).
  const [notes, setNotes] = useState<Record<string, string>>({});
  useEffect(() => {
    if (report) {
      setNotes((prev) =>
        Object.keys(prev).length
          ? prev
          : Object.fromEntries(report.items.map((i) => [i.id, i.user_note ?? ""])),
      );
    }
  }, [report]);
  const dirty =
    report?.items.some((i) => (notes[i.id] ?? i.user_note ?? "") !== (i.user_note ?? "")) ?? false;

  const saveReview = useMutation({
    mutationFn: async () => {
      const changed = (report?.items ?? []).filter(
        (i) => (notes[i.id] ?? i.user_note ?? "") !== (i.user_note ?? ""),
      );
      for (const i of changed) await api.updateNote(i.id, notes[i.id] ?? "");
      await api.saveReport(reportId);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["report", reportId] });
      setVerifyMsg("Saved — recorded in the activity log.");
    },
    onError: (e) =>
      setVerifyMsg(
        e instanceof ApiError && e.status === 403 ? "You may not edit this report." : "Save failed.",
      ),
  });

  const verify = useMutation({
    mutationFn: (judgement: string) => api.submitVerification(reportId, judgement, verifyNotes),
    onSuccess: (_d, j) => setVerifyMsg(`Recorded: ${j}`),
    onError: () => setVerifyMsg("You may not submit verifications."),
  });
  const download = useMutation({
    mutationFn: () => api.downloadRecording(reportId),
    onSuccess: ({ url }) => window.open(url, "_blank"),
    onError: () => setVerifyMsg("Download not permitted."),
  });
  // Download ONE individual report (feedback | checklist | ideal) as HTML (no PDF page-cutoff).
  const sectionHtml = useMutation({
    mutationFn: (s: string) => api.downloadSectionHtml(reportId, s),
    onError: () => setVerifyMsg("Download failed."),
  });
  const dlTranscript = useMutation({
    mutationFn: () => api.downloadReportTranscript(reportId),
    onError: () => setVerifyMsg("Transcript download failed."),
  });

  if (isLoading || !report)
    return <div className="min-h-full bg-[#0f1115] p-6 text-slate-400">Loading report…</div>;

  const n = (report.narrative ?? {}) as Narrative;
  const comp = overall(report.items, true);
  const qual = overall(report.items, false);

  // The individual reports available for this call (per the chosen OPTION). Raw transcript is
  // always present (download-only); feedback/checklist/ideal depend on what the option produced.
  const hasFeedback = !!(
    n.coaching ||
    n.compliance ||
    (n.feedback &&
      ((n.feedback.strengths?.length ?? 0) > 0 || (n.feedback.development?.length ?? 0) > 0)) ||
    report.objections.length > 0
  );
  const hasIdeal = n.ideal_conversation !== undefined || n.already_ideal !== undefined;
  const hasChecklist = report.items.length > 0;
  const tabs = [
    ...(hasFeedback ? [{ key: "feedback", label: "Feedback" }] : []),
    ...(hasChecklist ? [{ key: "checklist", label: "Checklist" }] : []),
    ...(hasIdeal ? [{ key: "ideal", label: "Ideal Conversation" }] : []),
    { key: "transcript", label: "Raw Transcript" },
  ];
  const activeTab = tab ?? tabs[0]?.key ?? "transcript";

  return (
    <div className="min-h-full bg-[#0f1115] text-slate-200">
      <div
        className="mx-auto max-w-5xl px-6 py-8"
        style={{
          backgroundImage:
            "radial-gradient(36rem 36rem at 20% 12%, rgba(45,212,191,.10), transparent 60%), radial-gradient(34rem 34rem at 85% 88%, rgba(244,114,182,.09), transparent 60%)",
        }}
      >
        {/* toolbar */}
        <div className="mb-6 flex items-center justify-between">
          <button onClick={onBack} className="text-sm text-teal-300 hover:underline">
            ← Back to calls
          </button>
          <Btn onClick={() => download.mutate()}>Recording</Btn>
        </div>

        {/* header */}
        <header className="mb-6 text-center">
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-sky-300">
            Everest Receivable Services
          </div>
          <h1 className="mt-2 bg-gradient-to-r from-teal-300 to-sky-300 bg-clip-text font-display text-3xl font-bold text-transparent">
            Debt Collection Call Quality Report
          </h1>
          {report.agent_name && (
            <div className="mt-2 text-sm text-slate-300">
              Agent: <span className="font-semibold text-slate-100">{report.agent_name}</span>
            </div>
          )}
          <div className="mt-4 flex flex-wrap justify-center gap-3">
            <Pill className={report.flagged_for_review ? "text-rose-300" : "text-emerald-300"}>
              {report.flagged_for_review ? "Flagged for Review" : "Reviewed"}
            </Pill>
            {report.items.length > 0 && (
              <>
                <Pill>
                  Compliance <Badge answer={comp} />
                </Pill>
                <Pill>
                  Quality <Badge answer={qual} />
                </Pill>
              </>
            )}
          </div>
          {report.flagged_for_review && report.flag_reason && (
            <div className="mx-auto mt-4 max-w-2xl rounded-lg border border-rose-400/40 bg-rose-400/10 px-4 py-2 text-sm text-rose-200">
              ⚠ {report.flag_reason}
            </div>
          )}
        </header>

        {/* individual-report tabs + per-report download */}
        <div className="mb-5 flex flex-wrap items-center gap-1 border-b border-white/10 pb-2">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`rounded-t-lg px-3 py-1.5 text-sm font-medium transition ${
                activeTab === t.key
                  ? "bg-white/[0.08] text-sky-300"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {t.label}
            </button>
          ))}
          {activeTab !== "transcript" && (
            <span className="ml-auto">
              <Btn onClick={() => sectionHtml.mutate(activeTab)} busy={sectionHtml.isPending}>
                {sectionHtml.isPending ? "Preparing…" : "Download this report (HTML)"}
              </Btn>
            </span>
          )}
        </div>

        {activeTab === "feedback" && (
          <>
            {n.coaching && (
              <GlassCard title="Coaching & Improvement Areas">
                <Prose text={n.coaching} />
              </GlassCard>
            )}
            {n.compliance && (
              <GlassCard title="Compliance & Quality Issues">
                <Prose text={n.compliance} />
              </GlassCard>
            )}
            {n.feedback && (
              <GlassCard title="Constructive Feedback">
                <div className="grid gap-6 md:grid-cols-2">
                  <div>
                    <h3 className="mb-2 font-display font-semibold text-emerald-300">Strengths</h3>
                    <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">
                      {(n.feedback.strengths ?? []).map((s, i) => <li key={i}>{s}</li>)}
                    </ul>
                  </div>
                  <div>
                    <h3 className="mb-2 font-display font-semibold text-rose-300">
                      Areas for Development
                    </h3>
                    <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">
                      {(n.feedback.development ?? []).map((s, i) => <li key={i}>{s}</li>)}
                    </ul>
                  </div>
                </div>
              </GlassCard>
            )}
            {report.objections.length > 0 && (
              <GlassCard title="Consumer Objections">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[0.7rem] uppercase tracking-wide text-slate-400">
                      <th className="py-1 pr-2">Objection</th>
                      <th className="px-2">Category</th>
                      <th className="px-2">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.objections.map((o, i) => (
                      <tr key={i} className="border-t border-white/5">
                        <td className="py-2 pr-2 text-slate-300">{o.text}</td>
                        <td className="px-2 text-slate-400">{o.category ?? "—"}</td>
                        <td className="px-2">
                          <Badge answer={o.cleared ? "PASS" : "FAIL"} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </GlassCard>
            )}
          </>
        )}

        {activeTab === "checklist" && hasChecklist && (
          <GlassCard title="Quality & Compliance Report Card">
            <div className="space-y-4">
              {groupBySection(report.items).map(([section, rows]) => (
                <div key={section} className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
                  <h3 className="mb-2 font-display text-sm font-semibold text-slate-200">
                    {section}
                  </h3>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-[0.7rem] uppercase tracking-wide text-slate-400">
                        <th className="py-1 pr-2">Item</th>
                        <th className="px-2">Status</th>
                        <th className="px-2">Evidence / Notes</th>
                        <th className="pl-2">Your note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((it) => (
                        <ItemRow
                          key={it.id}
                          item={it}
                          value={notes[it.id] ?? it.user_note ?? ""}
                          onChange={(v) => setNotes((nn) => ({ ...nn, [it.id]: v }))}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          </GlassCard>
        )}

        {activeTab === "ideal" && (
          <GlassCard title="Ideal Rewritten Conversation">
            {n.already_ideal && !(n.ideal_conversation && n.ideal_conversation.length) ? (
              <p className="text-sm text-slate-400">
                The call was already compliant — no rewrite was generated.
              </p>
            ) : (
              <div className="mx-auto flex max-w-2xl flex-col gap-2">
                {(() => {
                  const turns = n.ideal_conversation ?? [];
                  const roles = roleMap(turns);
                  return turns.map((t, i) => {
                    const role = roles[(t.speaker || "").trim()] ?? "Consumer";
                    const agent = role === "Agent";
                    return (
                      <div
                        key={i}
                        className={`max-w-[82%] rounded-2xl px-4 py-2.5 text-sm ${
                          agent
                            ? "mr-auto rounded-tl-sm bg-slate-700/70 text-slate-100"
                            : "ml-auto rounded-tr-sm bg-teal-400 text-slate-900"
                        }`}
                      >
                        <span className="font-bold">{role}: </span>
                        {t.text}
                      </div>
                    );
                  });
                })()}
              </div>
            )}
          </GlassCard>
        )}

        {activeTab === "transcript" && (
          <GlassCard title="Raw Transcript">
            <p className="text-sm text-slate-400">
              The raw STT transcript is download-only (no in-app preview).
            </p>
            <div className="mt-3">
              <Btn onClick={() => dlTranscript.mutate()} busy={dlTranscript.isPending}>
                {dlTranscript.isPending ? "Preparing…" : "Download transcript (.txt)"}
              </Btn>
            </div>
          </GlassCard>
        )}

        <GlassCard title="Verification">
          <textarea
            className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-slate-200 outline-none placeholder:text-slate-500"
            rows={2}
            placeholder="Evaluation notes…"
            value={verifyNotes}
            onChange={(e) => setVerifyNotes(e.target.value)}
          />
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {["CORRECT", "WRONG", "CANT_SAY"].map((j) => (
              <Btn key={j} onClick={() => verify.mutate(j)}>
                {j === "CANT_SAY" ? "Can't say" : j[0] + j.slice(1).toLowerCase()}
              </Btn>
            ))}
            {verifyMsg && <span className="text-sm text-slate-400">{verifyMsg}</span>}
          </div>
          <div className="mt-4 flex items-center justify-end gap-3 border-t border-white/10 pt-4">
            {dirty && <span className="text-xs text-amber-300">Unsaved changes</span>}
            <Btn primary onClick={() => saveReview.mutate()} busy={saveReview.isPending}>
              {saveReview.isPending ? "Saving…" : "Save review"}
            </Btn>
          </div>
        </GlassCard>
      </div>
    </div>
  );
}

function ItemRow({
  item,
  value,
  onChange,
}: {
  item: ReportItem;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <tr className="border-t border-white/5 align-top">
      <td className="py-2 pr-2 text-slate-200">
        {item.text}
        {item.needs_human_review && (
          <span className="ml-2 rounded bg-amber-400/15 px-1.5 text-[0.65rem] text-amber-300">
            needs review
          </span>
        )}
      </td>
      <td className="px-2">
        <Badge answer={item.answer} />
        {item.raw_answer && <span className="ml-1.5 text-xs text-slate-400">{item.raw_answer}</span>}
      </td>
      <td className="px-2 text-xs text-slate-400">
        {item.evidence_quote && (
          <span>
            “{item.evidence_quote}”
            {item.evidence_offset_sec != null && (
              <span className="text-sky-300"> ({fmtTime(item.evidence_offset_sec)})</span>
            )}
          </span>
        )}
        {item.comment && <div className="mt-1 text-slate-300">{item.comment}</div>}
      </td>
      <td className="pl-2">
        <input
          className="w-36 rounded border border-white/10 bg-white/[0.04] px-1.5 py-1 text-xs text-slate-200 outline-none placeholder:text-slate-500"
          value={value}
          placeholder="Add note…"
          onChange={(e) => onChange(e.target.value)}
        />
      </td>
    </tr>
  );
}

function groupBySection(items: ReportItem[]): [string, ReportItem[]][] {
  const order: string[] = [];
  const map = new Map<string, ReportItem[]>();
  for (const it of items) {
    if (!map.has(it.section)) {
      map.set(it.section, []);
      order.push(it.section);
    }
    map.get(it.section)!.push(it);
  }
  return order.map((s) => [s, map.get(s)!]);
}

function GlassCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-5 rounded-xl border border-white/10 bg-white/[0.04] p-6 shadow-[0_8px_32px_rgba(0,0,0,.25)] backdrop-blur">
      <h2 className="mb-4 font-display text-xl font-bold text-sky-300">{title}</h2>
      {children}
    </section>
  );
}

function Prose({ text }: { text: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed text-slate-300">
      {text
        .split("\n")
        .filter((b) => b.trim())
        .map((b, i) => <p key={i}>{b}</p>)}
    </div>
  );
}

function Pill({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-sm font-semibold ${className}`}
    >
      {children}
    </span>
  );
}

function Btn({
  children,
  onClick,
  primary,
  busy,
}: {
  children: React.ReactNode;
  onClick: () => void;
  primary?: boolean;
  busy?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:opacity-50 ${
        primary
          ? "bg-teal-400 text-slate-900 hover:bg-teal-300"
          : "border border-white/15 bg-white/[0.05] text-slate-200 hover:bg-white/[0.1]"
      }`}
    >
      {children}
    </button>
  );
}
