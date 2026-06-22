import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, ApiError, type Scope } from "../lib/api";
import { renderMarkdown } from "../lib/markdown";

// Judge stages (separate LLM calls). The folder/portfolio is the BINDING scope; within a scope
// each stage has its own prompt. "template" is the deterministic HTML report layout for the scope.
const AGENT_TABS = [
  { key: "feedback", label: "Feedback" },
  { key: "checklist", label: "Checklist" },
  { key: "ideal", label: "Ideal rewriter" },
  { key: "merged", label: "Merged (feedback + checklist)" },
] as const;

// Super-admin "Agent Studio": pick a binding scope (Global → Portfolio → Folder), then edit each
// stage's prompt and the report template for that scope. Resolution is most-specific-first, so a
// folder overrides its portfolio, which overrides Global, which falls back to the built-in default.
export function PromptBuilder({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<string>("feedback");
  const [mode, setMode] = useState<"prompt" | "schema">("prompt");
  const [portfolioId, setPortfolioId] = useState<string | null>(null);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const scope: Scope = { portfolioId, agentId };
  const isTemplate = tab === "template";
  const kind: "template" | "prompt" | "schema" = isTemplate ? "template" : mode;

  const { data: portfolios = [] } = useQuery({
    queryKey: ["studio-portfolios"],
    queryFn: () => api.listPortfolios({ limit: 100 }).then((r) => r.items),
  });
  const { data: folders = [] } = useQuery({
    queryKey: ["studio-folders", portfolioId],
    queryFn: () => api.listAgents(portfolioId!, { limit: 100 }).then((r) => r.items),
    enabled: !!portfolioId,
  });
  const { data: defaults = {} } = useQuery({
    queryKey: ["prompt-defaults"],
    queryFn: () => api.promptDefaults(),
  });
  const { data: schemaDefaults = {} } = useQuery({
    queryKey: ["schema-defaults"],
    queryFn: () => api.outputSchemaDefaults(),
  });

  // Saved items at the CURRENT scope + tab + kind.
  const promptsQ = useQuery({
    queryKey: ["studio-prompts", tab, portfolioId, agentId],
    queryFn: () => api.listPrompts(tab, scope),
    enabled: kind === "prompt",
  });
  const schemasQ = useQuery({
    queryKey: ["studio-schemas", tab, portfolioId, agentId],
    queryFn: () => api.listOutputSchemas(tab, scope),
    enabled: kind === "schema",
  });
  const templatesQ = useQuery({
    queryKey: ["studio-templates", portfolioId, agentId],
    queryFn: () => api.listTemplates(scope),
    enabled: isTemplate,
  });
  const { data: fields = {} } = useQuery({
    queryKey: ["template-fields"],
    queryFn: () => api.templateFields(),
    enabled: isTemplate,
  });

  const asText = (c: unknown) =>
    typeof c === "string" ? c : JSON.stringify(c, null, 2);
  const saved =
    kind === "template" ? templatesQ.data ?? [] : kind === "schema" ? schemasQ.data ?? [] : promptsQ.data ?? [];
  const active = saved.find((p) => p.in_use);

  // Reset the folder when the portfolio changes (folders belong to a portfolio).
  useEffect(() => {
    setAgentId(null);
  }, [portfolioId]);

  const invalidate = () =>
    qc.invalidateQueries({
      queryKey:
        kind === "template"
          ? ["studio-templates", portfolioId, agentId]
          : kind === "schema"
            ? ["studio-schemas", tab, portfolioId, agentId]
            : ["studio-prompts", tab, portfolioId, agentId],
    });
  const err = (e: unknown, fallback: string) => {
    if (e instanceof ApiError) {
      let detail = e.message;
      try {
        detail = JSON.parse(e.message).detail ?? e.message; // unwrap {"detail": "..."}
      } catch {
        /* not JSON — use the raw text */
      }
      return setMsg(`${fallback}: ${detail}`);
    }
    setMsg(e instanceof Error && e.message ? `${fallback}: ${e.message}` : fallback);
  };

  const save = useMutation({
    mutationFn: async (): Promise<void> => {
      const nm = name.trim() || "Untitled";
      if (kind === "template") {
        await api.createTemplate(nm, content, scope);
      } else if (kind === "schema") {
        let parsed: Record<string, unknown>;
        try {
          parsed = JSON.parse(content);
        } catch {
          throw new Error("not valid JSON");
        }
        await api.createOutputSchema(tab, nm, parsed, scope);
      } else {
        await api.createPrompt(tab, nm, content, scope);
      }
    },
    onSuccess: () => {
      setMsg('Saved. Click "Use this" to make it active for this scope.');
      invalidate();
    },
    onError: (e) => err(e, "Save failed"),
  });
  const activate = useMutation({
    mutationFn: async (id: string): Promise<void> => {
      if (kind === "template") await api.activateTemplate(id);
      else if (kind === "schema") await api.activateOutputSchema(id);
      else await api.activatePrompt(id);
    },
    onSuccess: () => {
      setMsg("Activated for this scope.");
      invalidate();
    },
    onError: (e) => err(e, "Activation failed"),
  });
  const del = useMutation({
    mutationFn: async (id: string): Promise<void> => {
      if (kind === "template") await api.deleteTemplate(id);
      else if (kind === "schema") await api.deleteOutputSchema(id);
      else await api.deletePrompt(id);
    },
    onSuccess: () => {
      setMsg("Deleted.");
      invalidate();
    },
    onError: (e) => err(e, "Delete failed"),
  });

  const scopeLabel = !portfolioId
    ? "Global default (all portfolios)"
    : !agentId
      ? `${portfolios.find((p) => p.id === portfolioId)?.name ?? "Portfolio"} — whole portfolio`
      : `${folders.find((f) => f.id === agentId)?.name ?? "Folder"} (folder)`;

  return (
    <div className="mx-auto max-w-4xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <button onClick={onClose} className="text-sm text-[#c0567f] hover:underline">
          ← Back
        </button>
        {msg && <span className="text-sm text-gray-500">{msg}</span>}
      </div>
      <h2 className="text-lg font-semibold text-ink">Agent Studio</h2>
      <p className="mb-3 text-xs text-gray-500">
        Bind prompts and the report template per <b>folder</b> (within a portfolio), per portfolio,
        or globally. The most specific binding wins. The output format + impartiality rules are
        always enforced by the system and can't be edited away.
      </p>

      {/* scope selectors */}
      <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-gray-200 bg-white/60 p-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">Bind to</span>
        <select
          className="rounded-lg border border-gray-300 px-2 py-1.5 text-sm"
          value={portfolioId ?? ""}
          onChange={(e) => setPortfolioId(e.target.value || null)}
        >
          <option value="">Global default</option>
          {portfolios.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <select
          className="rounded-lg border border-gray-300 px-2 py-1.5 text-sm disabled:opacity-50"
          value={agentId ?? ""}
          onChange={(e) => setAgentId(e.target.value || null)}
          disabled={!portfolioId}
        >
          <option value="">Whole portfolio</option>
          {folders.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
        <span className="text-xs text-gray-500">
          Editing: <span className="font-semibold text-ink">{scopeLabel}</span>
        </span>
      </div>

      {/* tabs: judge stages + report template */}
      <div className="mb-4 flex flex-wrap gap-1 border-b border-gray-200">
        {AGENT_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => {
              setTab(t.key);
              setMsg(null);
            }}
            className={`rounded-t-lg px-3 py-1.5 text-sm font-medium ${
              tab === t.key ? "bg-[#dd9aa6]/15 text-[#8c3a55]" : "text-gray-500 hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
        <button
          onClick={() => {
            setTab("template");
            setMsg(null);
          }}
          className={`ml-2 rounded-t-lg px-3 py-1.5 text-sm font-medium ${
            isTemplate ? "bg-[#dd9aa6]/15 text-[#8c3a55]" : "text-gray-500 hover:text-ink"
          }`}
        >
          Report template
        </button>
      </div>

      {!isTemplate && (
        <div className="mb-3 inline-flex rounded-lg border border-gray-200 p-0.5 text-xs">
          {(["prompt", "schema"] as const).map((m) => (
            <button
              key={m}
              onClick={() => {
                setMode(m);
                setMsg(null);
              }}
              className={`rounded-md px-2.5 py-1 font-medium ${
                mode === m ? "bg-[#dd9aa6]/20 text-[#8c3a55]" : "text-gray-500 hover:text-ink"
              }`}
            >
              {m === "prompt" ? "Prompt" : "Output schema"}
            </button>
          ))}
        </div>
      )}

      <p className="mb-3 text-sm">
        <span className="text-gray-500">In use for this scope: </span>
        {active ? (
          <span className="font-semibold text-emerald-700">{active.name}</span>
        ) : (
          <span className="font-medium text-gray-500">
            {kind === "template"
              ? "Built-in renderer"
              : kind === "schema"
                ? "Built-in schema"
                : "Inherits parent scope / built-in default"}
          </span>
        )}
      </p>

      {/* editor */}
      <div className="rounded-xl border border-gray-200 bg-white/70 p-3">
        <div className="mb-2 flex items-center gap-2">
          <input
            className="min-w-0 flex-1 rounded border border-gray-300 px-2 py-1 text-sm"
            placeholder={isTemplate ? "Template name (e.g. “Everest report v2”)" : "Prompt name"}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          {kind !== "template" && (
            <button
              onClick={() =>
                setContent(
                  kind === "schema"
                    ? JSON.stringify(schemaDefaults[tab] ?? {}, null, 2)
                    : defaults[tab] ?? "",
                )
              }
              className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50"
              title="Load the built-in default as a starting point"
            >
              Load default
            </button>
          )}
          {kind === "prompt" && (
            <button
              onClick={() => setPreview((v) => !v)}
              className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50"
            >
              {preview ? "Write" : "Preview"}
            </button>
          )}
        </div>
        {kind === "prompt" && preview ? (
          <div
            className="prose-sm max-h-72 overflow-auto rounded border border-gray-200 bg-white p-3 text-sm text-ink"
            // eslint-disable-next-line react/no-danger
            dangerouslySetInnerHTML={{ __html: renderMarkdown(content || "_(empty)_") }}
          />
        ) : (
          <textarea
            className="h-72 w-full rounded border border-gray-200 px-2 py-1 font-mono text-xs"
            placeholder={
              kind === "template"
                ? "<h1>{{agent_name}}</h1>\n{{#items}}<li>{{text}} — {{answer}}</li>{{/items}}"
                : kind === "schema"
                  ? '{\n  "type": "object",\n  "properties": { … },\n  "required": [ … ]\n}'
                  : "# Role\nYou are…  (markdown)"
            }
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            onClick={() => save.mutate()}
            disabled={!content.trim() || save.isPending}
            className="rounded-lg bg-[#dd9aa6] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </div>
        {isTemplate && (
          <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50/70 p-2 text-xs text-gray-600">
            <span className="font-semibold text-gray-700">Available fields:</span>{" "}
            <code className="break-words">{Object.keys(fields).join(", ") || "—"}</code>
            <p className="mt-1 text-gray-500">
              Use <code>{"{{field}}"}</code>, <code>{"{{{raw}}}"}</code>,{" "}
              <code>{"{{#list}}…{{/list}}"}</code>, <code>{"{{^empty}}…{{/empty}}"}</code>. Lists:
              items, objections, ideal_conversation, strengths, development. Custom schema fields
              are under <code>{"{{extra.*}}"}</code>. Saving validates every reference — unknown
              fields are rejected.
            </p>
          </div>
        )}
        {kind === "schema" && (
          <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50/70 p-2 text-xs text-gray-600">
            A custom JSON schema becomes this stage's Structured-Output contract. It must keep the
            operational core (the built-in default shows it — click <b>Load default</b>); extra
            fields you add are deterministic too and reachable in the report template as{" "}
            <code>{"{{extra.*}}"}</code>. Saving validates the schema — missing core fields or
            unsupported keywords are rejected.
          </div>
        )}
      </div>

      {/* saved items at this scope */}
      <h3 className="mb-2 mt-5 text-sm font-semibold text-ink">
        Saved {kind === "template" ? "templates" : kind === "schema" ? "schemas" : "prompts"} ·{" "}
        {scopeLabel}
      </h3>
      <div className="space-y-1.5">
        {saved.length === 0 && (
          <p className="text-sm text-gray-400">None at this scope yet.</p>
        )}
        {saved.map((p) => (
          <div
            key={p.id}
            className="flex items-center justify-between gap-2 rounded-lg border border-gray-200 bg-white/70 px-3 py-2"
          >
            <div className="min-w-0">
              <span className="block truncate text-sm font-medium text-ink">{p.name}</span>
              <span className="text-xs text-gray-400">
                {new Date(p.created_at).toLocaleString()}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span
                className={`rounded px-2 py-0.5 text-xs font-semibold ${
                  p.in_use ? "bg-emerald-100 text-emerald-700" : "bg-gray-100 text-gray-500"
                }`}
              >
                {p.in_use ? "In use" : "Not in use"}
              </span>
              <button
                onClick={() => {
                  setName(p.name);
                  setContent(asText(p.content));
                  setPreview(false);
                }}
                className="text-xs text-gray-500 hover:text-ink hover:underline"
              >
                Edit copy
              </button>
              {!p.in_use && (
                <button
                  onClick={() => activate.mutate(p.id)}
                  className="text-xs font-medium text-[#c0567f] hover:underline"
                >
                  Use this
                </button>
              )}
              <button
                onClick={() => {
                  if (window.confirm(`Delete “${p.name}”?`)) del.mutate(p.id);
                }}
                className="text-xs text-red-500 hover:underline"
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
