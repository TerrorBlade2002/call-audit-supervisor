import { useAuth } from "./auth";

const BASE = "/api";

// ---- types (mirror the backend schemas) ----
export interface Portfolio {
  id: string;
  name: string;
  created_at: string;
  my_role: string | null; // caller's role here: ADMIN / SUPERVISOR / AGENT
}
export interface Agent {
  id: string;
  portfolio_id: string;
  name: string;
  external_ref: string | null;
  created_at: string;
}
export interface Call {
  id: string;
  agent_id: string;
  duration_sec: number | null;
  batch_id: string | null;
  status: string | null;
  last_error: string | null;
  report_id: string | null;
  option: string | null;
  created_at: string;
  completed_at: string | null;
}
export interface ReportItem {
  id: string;
  checklist_item_id: string;
  section: string;
  text: string;
  answer: "PASS" | "FAIL" | "NA" | null;
  raw_answer: string | null;
  options: string[] | null;
  confidence: number | null;
  evidence_quote: string | null;
  evidence_offset_sec: number | null;
  comment: string | null;
  decided_by: string | null;
  needs_human_review: boolean;
  user_note: string | null;
}
export interface ReportObjection {
  text: string;
  category: string | null;
  cleared: boolean;
}
export interface Report {
  id: string;
  call_id: string;
  checklist_id: string | null;
  option: string | null;
  agent_name: string | null;
  flagged_for_review: boolean;
  flag_reason: string | null;
  narrative: Record<string, unknown> | null;
  items: ReportItem[];
  objections: ReportObjection[];
}
export interface PresignItem {
  filename: string;
  key: string;
  upload_url: string;
}
export interface PresignResponse {
  bucket: string;
  expires_in: number;
  uploads: PresignItem[];
}
export interface ObjectionCluster {
  representative_text: string;
  count: number;
  cleared_count: number;
  never_cleared: boolean;
  examples: string[];
}

export interface ChecklistItemModel {
  id?: string;
  section: string;
  text: string;
  answer_type: "PASS_FAIL" | "PASS_FAIL_NA" | "CHOICE" | "TEXT";
  options: string[] | null;
  is_subjective: boolean;
  risk: "NORMAL" | "ELEVATED" | "CRITICAL";
  guidance: string | null;
  rubric_slice?: string | null;
  sort_order?: number;
}
export interface ChecklistSummary {
  id: string;
  name: string;
  is_default: boolean;
  version: number;
  status: string;
  requires_kb: boolean;
  updated_at: string | null;
}
export interface KbDocument {
  id: string;
  filename: string | null;
  page_count: number | null;
  sha256: string;
  created_at: string;
}
export interface ActivityEntry {
  actor: string;
  action: string;
}
export interface ObjectionLogEntry {
  call_id: string;
  created_at: string;
  text: string;
  agent: string | null;
  cleared: boolean;
}
export interface TranscriptLogEntry {
  call_id: string;
  agent_name: string | null;
  created_at: string;
}
export interface PortfolioUser {
  id: string;
  email: string;
  name: string;
  role: string;
}
export interface LifecycleEntry {
  call_id: string;
  batch_id: string | null;
  option: string | null;
  folder: string | null;
  state: string | null;
  attempts: number;
  uploaded_at: string | null;
  report_at: string | null;
}
export interface LifecycleStep {
  step: string;
  ok: boolean;
  at?: string | null;
  detail?: string;
}
export interface LifecycleError {
  stage: string;
  attempt: number;
  fatal: boolean;
  error_class: string;
  message: string | null;
  traceback: string | null;
  at: string;
}
export interface LifecycleDetail {
  call_id: string;
  portfolio_id: string;
  batch_id: string | null;
  folder: string | null;
  agent_name: string | null;
  option: string;
  state: string | null;
  attempts: number;
  last_error: string | null;
  agents: string[];
  steps: LifecycleStep[];
  errors: LifecycleError[];
}
export interface AgentSnapshot {
  agent: string;
  calls: number;
  passes: number;
  fails: number;
  critical_fails: number;
  calls_fail_gt_pass: number;
  worst_report_id: string | null;
}
export interface FailedItem {
  text: string;
  section: string;
  risk: string;
  agents_failed: number;
  calls_failed: number;
}
export interface WorstCall {
  call_id: string;
  report_id: string;
  agent: string;
  fails: number;
  critical: number;
  flagged: boolean;
  needs_review: boolean;
}
export interface ChecklistSummary {
  total_calls: number;
  agents: number;
  clean: number;
  need_review: number;
  critical_fails: number;
  failed_processing: number;
  missing_agent_name: number;
  per_agent: AgentSnapshot[];
  top_failed_items: FailedItem[];
  worst_calls: WorstCall[];
}
export interface AgentPrompt {
  id: string;
  agent: string;
  portfolio_id: string | null;
  agent_id: string | null;
  name: string;
  content: string;
  in_use: boolean;
  created_at: string;
  updated_at: string;
}
export interface ReportTemplate {
  id: string;
  portfolio_id: string | null;
  agent_id: string | null;
  name: string;
  content: string;
  in_use: boolean;
  created_at: string;
  updated_at: string;
}
export interface OutputSchema {
  id: string;
  agent: string;
  portfolio_id: string | null;
  agent_id: string | null;
  name: string;
  content: Record<string, unknown>;
  in_use: boolean;
  created_at: string;
  updated_at: string;
}
// Binding scope: { } = global, { portfolioId } = portfolio-wide, { portfolioId, agentId } = folder.
export interface Scope {
  portfolioId?: string | null;
  agentId?: string | null;
}
export interface ChecklistDetail extends ChecklistSummary {
  items: ChecklistItemModel[];
}

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function rawFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = useAuth.getState().token;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string>),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(`${BASE}${path}`, { ...init, headers });
  if (resp.status === 401) {
    useAuth.getState().logout();
    throw new ApiError(401, "unauthorized");
  }
  if (!resp.ok) {
    const text = await resp.text();
    throw new ApiError(resp.status, text || resp.statusText);
  }
  return resp;
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await rawFetch(path, init);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export interface PageOpts {
  limit?: number;
  offset?: number;
}
export interface Paged<T> {
  items: T[];
  total: number;
}

function pageQuery(p?: PageOpts): string {
  const q = new URLSearchParams();
  if (p?.limit != null) q.set("limit", String(p.limit));
  if (p?.offset != null) q.set("offset", String(p.offset));
  const s = q.toString();
  return s ? `?${s}` : "";
}

// Paginated list: items come from the body, the total from the X-Total-Count header.
async function reqPaged<T>(path: string): Promise<Paged<T>> {
  const resp = await rawFetch(path);
  const items = (await resp.json()) as T[];
  const total = Number(resp.headers.get("X-Total-Count") ?? items.length);
  return { items, total };
}

export const api = {
  // Readiness probe (unauthenticated). Throws on network failure or non-200 (DB/API down),
  // which the app uses to show a graceful "service unavailable" page instead of empty states.
  health: async (): Promise<{ status: string }> => {
    const resp = await fetch(`${BASE}/readyz`);
    if (!resp.ok) throw new ApiError(resp.status, "service not ready");
    return (await resp.json()) as { status: string };
  },

  devLogin: (email: string, asAdmin: boolean, role?: string) =>
    req<{ access_token: string }>("/auth/dev-login", {
      method: "POST",
      body: JSON.stringify({ email, as_admin: asAdmin, role: role ?? null }),
    }),
  login: (email: string, password: string) =>
    req<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  listPortfolios: async (page?: PageOpts): Promise<Paged<Portfolio> & { isOrgAdmin: boolean }> => {
    const resp = await rawFetch(`/portfolios${pageQuery(page)}`);
    const items = (await resp.json()) as Portfolio[];
    const total = Number(resp.headers.get("X-Total-Count") ?? items.length);
    const isOrgAdmin = resp.headers.get("X-Is-Org-Admin") === "true";
    return { items, total, isOrgAdmin };
  },
  createPortfolio: (name: string) =>
    req<Portfolio>("/portfolios", { method: "POST", body: JSON.stringify({ name }) }),
  renamePortfolio: (pid: string, name: string) =>
    req<Portfolio>(`/portfolios/${pid}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  deletePortfolio: (pid: string) => req<void>(`/portfolios/${pid}`, { method: "DELETE" }),
  // Per-portfolio user accounts (super admin).
  listPortfolioUsers: (pid: string) => req<PortfolioUser[]>(`/portfolios/${pid}/users`),
  createPortfolioUser: (pid: string, email: string, password: string, role: string) =>
    req<PortfolioUser>(`/portfolios/${pid}/users`, {
      method: "POST",
      body: JSON.stringify({ email, password, role }),
    }),
  deletePortfolioUser: (pid: string, userId: string) =>
    req<void>(`/portfolios/${pid}/users/${userId}`, { method: "DELETE" }),

  listAgents: (pid: string, page?: PageOpts) =>
    reqPaged<Agent>(`/portfolios/${pid}/agents${pageQuery(page)}`),
  renameAgent: (pid: string, aid: string, name: string) =>
    req<Agent>(`/portfolios/${pid}/agents/${aid}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deleteAgent: (pid: string, aid: string) =>
    req<void>(`/portfolios/${pid}/agents/${aid}`, { method: "DELETE" }),
  createAgent: (pid: string, name: string) =>
    req<Agent>(`/portfolios/${pid}/agents`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  presign: (pid: string, aid: string, filenames: string[]) =>
    req<PresignResponse>(`/portfolios/${pid}/agents/${aid}/uploads:presign`, {
      method: "POST",
      body: JSON.stringify({ files: filenames.map((filename) => ({ filename })) }),
    }),
  registerCalls: (pid: string, aid: string, keys: string[]) =>
    req<{ batch_id: string; calls: Call[] }>(`/portfolios/${pid}/agents/${aid}/calls`, {
      method: "POST",
      body: JSON.stringify({ items: keys.map((key) => ({ key })) }),
    }),
  // Server-side upload proxy: bytes go browser → API → R2 (no bucket CORS needed). The
  // request fails loudly if a file doesn't land, instead of silently registering an
  // un-transcribable call. The whole batch carries one processing OPTION + checklist/KB choice.
  uploadRecordings: async (
    pid: string,
    aid: string,
    files: File[],
    opts?: { option?: string; checklistId?: string | null; kbDocIds?: string[] | null },
  ) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    form.append("option", opts?.option ?? "FULL");
    if (opts?.checklistId) form.append("checklist_id", opts.checklistId);
    if (opts?.kbDocIds && opts.kbDocIds.length) form.append("kb_doc_ids", opts.kbDocIds.join(","));
    const token = useAuth.getState().token;
    const resp = await fetch(`${BASE}/portfolios/${pid}/agents/${aid}/recordings`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new ApiError(resp.status, text || resp.statusText);
    }
    return (await resp.json()) as { batch_id: string; calls: Call[] };
  },
  listCalls: (pid: string, aid: string, page?: PageOpts) =>
    reqPaged<Call>(`/portfolios/${pid}/agents/${aid}/calls${pageQuery(page)}`),
  // Per-portfolio in-flight upload headroom (max 10 processing at once).
  getUploadQuota: (pid: string) =>
    req<{ max: number; in_flight: number; remaining: number }>(
      `/portfolios/${pid}/upload-quota`,
    ),
  // Removes the call + its recording and transcript from R2 (server-side).
  deleteCall: (pid: string, aid: string, callId: string) =>
    req<void>(`/portfolios/${pid}/agents/${aid}/calls/${callId}`, { method: "DELETE" }),

  getReport: (reportId: string) => req<Report>(`/reports/${reportId}`),
  saveReport: (reportId: string) =>
    req<void>(`/reports/${reportId}/save`, { method: "POST" }),
  updateNote: (itemId: string, note: string) =>
    req<void>(`/report-items/${itemId}/note`, {
      method: "PATCH",
      body: JSON.stringify({ note }),
    }),
  submitVerification: (reportId: string, judgement: string, notes: string) =>
    req<unknown>(`/reports/${reportId}/verification`, {
      method: "POST",
      body: JSON.stringify({ judgement, notes }),
    }),
  downloadRecording: (reportId: string) =>
    req<{ url: string; expires_in: number }>(`/reports/${reportId}/recording:download`),
  // Presigned URL for the rendered HTML report artifact in the R2 reports bucket.
  downloadReport: (reportId: string) =>
    req<{ url: string; expires_in: number }>(`/reports/${reportId}/report:download`),
  // Presigned URL for the rendered PDF (Chromium-generated, stored in R2).
  downloadReportPdf: (reportId: string) =>
    req<{ url: string; expires_in: number }>(`/reports/${reportId}/report.pdf:download`),
  // Individual report (feedback | checklist | ideal) as standalone HTML (no PDF page-cutoff).
  downloadSectionHtml: (reportId: string, section: string) =>
    downloadBlob(`/reports/${reportId}/section.html?section=${section}`, `${section}_report.html`),
  // Raw transcript (.txt) for this report's call — download only, no in-app preview.
  downloadReportTranscript: (reportId: string) =>
    downloadBlob(`/reports/${reportId}/transcript.txt`, "transcript.txt"),

  objections: (pid: string) => req<ObjectionCluster[]>(`/portfolios/${pid}/objections`),

  listChecklists: (pid: string) => req<ChecklistSummary[]>(`/portfolios/${pid}/checklists`),
  getChecklist: (pid: string, cid: string) =>
    req<ChecklistDetail>(`/portfolios/${pid}/checklists/${cid}`),
  updateChecklist: (
    pid: string,
    cid: string,
    name: string,
    items: ChecklistItemModel[],
    requiresKb: boolean,
  ) =>
    req<ChecklistDetail>(`/portfolios/${pid}/checklists/${cid}`, {
      method: "PUT",
      body: JSON.stringify({ name, items, requires_kb: requiresKb }),
    }),
  // Merged checklist CSV (all calls judged under this checklist) — fetched with auth then
  // triggered as a browser download.
  downloadChecklistCsv: async (pid: string, cid: string, filename: string) => {
    const token = useAuth.getState().token;
    const resp = await fetch(`${BASE}/portfolios/${pid}/checklists/${cid}/export.csv`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!resp.ok) throw new ApiError(resp.status, (await resp.text()) || resp.statusText);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },
  // Checklist CSV for just one upload batch (the per-batch download affordance).
  downloadBatchCsv: async (pid: string, batchId: string) => {
    const token = useAuth.getState().token;
    const resp = await fetch(`${BASE}/portfolios/${pid}/batches/${batchId}/checklist.csv`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!resp.ok) throw new ApiError(resp.status, (await resp.text()) || resp.statusText);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `batch_${batchId.slice(0, 8)}_checklist.csv`;
    a.click();
    URL.revokeObjectURL(url);
  },
  // Parse an uploaded .txt (Everest checklist format) into editable items for the builder.
  // Throws ApiError(422) when the file doesn't match the format.
  parseChecklistTxt: async (
    pid: string,
    file: File,
  ): Promise<{ name: string | null; items: ChecklistItemModel[] }> => {
    const form = new FormData();
    form.append("file", file, file.name);
    const token = useAuth.getState().token;
    const resp = await fetch(`${BASE}/portfolios/${pid}/checklists/parse`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    if (!resp.ok) throw new ApiError(resp.status, (await resp.text()) || resp.statusText);
    return (await resp.json()) as { name: string | null; items: ChecklistItemModel[] };
  },

  // Knowledge base (supervisor/super-admin only — gated server-side by KB_MANAGE)
  listKb: (pid: string) => req<KbDocument[]>(`/portfolios/${pid}/kb`),
  uploadKb: async (pid: string, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    const token = useAuth.getState().token;
    const resp = await fetch(`${BASE}/portfolios/${pid}/kb/upload`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    if (!resp.ok) throw new ApiError(resp.status, (await resp.text()) || resp.statusText);
    return (await resp.json()) as KbDocument[];
  },
  deleteKb: (pid: string, docId: string) =>
    req<void>(`/portfolios/${pid}/kb/${docId}`, { method: "DELETE" }),

  getActivity: () => req<ActivityEntry[]>("/admin/activity"),

  // Call lifecycle / observability (super admin).
  getLifecycle: (portfolio?: string) =>
    req<LifecycleEntry[]>(`/admin/lifecycle${portfolio ? `?portfolio=${portfolio}` : ""}`),
  getLifecycleDetail: (callId: string) =>
    req<LifecycleDetail>(`/admin/lifecycle/${callId}`),

  // Objection log (append-only) + CSV.
  getObjectionLog: (pid: string) => req<ObjectionLogEntry[]>(`/portfolios/${pid}/objection-log`),
  downloadObjectionCsv: (pid: string) =>
    downloadBlob(`/portfolios/${pid}/objection-log.csv`, "objection_log.csv"),

  // Transcript log (append-only) + per-call .txt download.
  getTranscriptLog: (pid: string) => req<TranscriptLogEntry[]>(`/portfolios/${pid}/transcripts`),
  downloadTranscript: (pid: string, callId: string) =>
    downloadBlob(
      `/portfolios/${pid}/calls/${callId}/transcript.txt`,
      `transcript_${callId.slice(0, 8)}.txt`,
    ),

  // Batch smoke summary — checklist triage (JSON, in-app) + CSV; feedback summary (HTML).
  getChecklistSummary: (pid: string, bid: string) =>
    req<ChecklistSummary>(`/portfolios/${pid}/batches/${bid}/checklist-summary`),
  downloadChecklistSummaryCsv: (pid: string, bid: string) =>
    downloadBlob(
      `/portfolios/${pid}/batches/${bid}/checklist-summary.csv`,
      `batch_${bid.slice(0, 8)}_summary.csv`,
    ),
  downloadFeedbackSummaryHtml: (pid: string, bid: string) =>
    downloadBlob(
      `/portfolios/${pid}/batches/${bid}/feedback-summary.html`,
      `batch_${bid.slice(0, 8)}_feedback.html`,
    ),

  // Agent Studio (super admin) — prompt bodies + output report templates, scoped per
  // (portfolio, folder). Scope is encoded as query/body params; omitting both = the global tier.
  listPrompts: (agent?: string, scope: Scope = {}) =>
    req<AgentPrompt[]>(`/admin/prompts${_scopeQuery({ agent, ...scope })}`),
  promptDefaults: () => req<Record<string, string>>("/admin/prompts/defaults"),
  createPrompt: (agent: string, name: string, content: string, scope: Scope = {}) =>
    req<AgentPrompt>("/admin/prompts", {
      method: "POST",
      body: JSON.stringify({
        agent, name, content,
        portfolio_id: scope.portfolioId ?? null, agent_id: scope.agentId ?? null,
      }),
    }),
  activatePrompt: (id: string) =>
    req<AgentPrompt>(`/admin/prompts/${id}/activate`, { method: "POST" }),
  deletePrompt: (id: string) => req<void>(`/admin/prompts/${id}`, { method: "DELETE" }),

  // Report templates (deterministic HTML layout), same scoping.
  templateFields: () => req<Record<string, unknown>>("/admin/report-templates/fields"),
  listTemplates: (scope: Scope = {}) =>
    req<ReportTemplate[]>(`/admin/report-templates${_scopeQuery(scope)}`),
  createTemplate: (name: string, content: string, scope: Scope = {}) =>
    req<ReportTemplate>("/admin/report-templates", {
      method: "POST",
      body: JSON.stringify({
        name, content,
        portfolio_id: scope.portfolioId ?? null, agent_id: scope.agentId ?? null,
      }),
    }),
  activateTemplate: (id: string) =>
    req<ReportTemplate>(`/admin/report-templates/${id}/activate`, { method: "POST" }),
  deleteTemplate: (id: string) =>
    req<void>(`/admin/report-templates/${id}`, { method: "DELETE" }),

  // Output schemas (custom Structured Outputs) per stage, same scoping.
  outputSchemaDefaults: () =>
    req<Record<string, Record<string, unknown>>>("/admin/output-schemas/defaults"),
  listOutputSchemas: (agent: string, scope: Scope = {}) =>
    req<OutputSchema[]>(`/admin/output-schemas${_scopeQuery({ agent, ...scope })}`),
  createOutputSchema: (
    agent: string,
    name: string,
    content: Record<string, unknown>,
    scope: Scope = {},
  ) =>
    req<OutputSchema>("/admin/output-schemas", {
      method: "POST",
      body: JSON.stringify({
        agent, name, content,
        portfolio_id: scope.portfolioId ?? null, agent_id: scope.agentId ?? null,
      }),
    }),
  activateOutputSchema: (id: string) =>
    req<OutputSchema>(`/admin/output-schemas/${id}/activate`, { method: "POST" }),
  deleteOutputSchema: (id: string) =>
    req<void>(`/admin/output-schemas/${id}`, { method: "DELETE" }),
};

// Build a ?portfolio_id=&agent_id=&agent= query from a scope (omits empty values).
function _scopeQuery(p: { agent?: string; portfolioId?: string | null; agentId?: string | null }): string {
  const q = new URLSearchParams();
  if (p.agent) q.set("agent", p.agent);
  if (p.portfolioId) q.set("portfolio_id", p.portfolioId);
  if (p.agentId) q.set("agent_id", p.agentId);
  const s = q.toString();
  return s ? `?${s}` : "";
}

// Fetch an authed file response and trigger a browser download.
async function downloadBlob(path: string, filename: string): Promise<void> {
  const token = useAuth.getState().token;
  const resp = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!resp.ok) throw new ApiError(resp.status, (await resp.text()) || resp.statusText);
  const url = URL.createObjectURL(await resp.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export { ApiError };
