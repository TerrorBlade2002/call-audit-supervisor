import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AgentsPane } from "./components/AgentsPane";
import { BatchSummary } from "./components/BatchSummary";
import { CallsPane } from "./components/CallsPane";
import { ChecklistBuilder } from "./components/ChecklistBuilder";
import { FoldersGrid } from "./components/FoldersGrid";
import { Login } from "./components/Login";
import { PortfoliosPane } from "./components/PortfoliosPane";
import { ServiceDown } from "./components/ServiceDown";
import { ReportView } from "./components/ReportView";
import { ActivityPanel } from "./components/ActivityPanel";
import { ExportMenu } from "./components/ExportMenu";
import { KbPanel } from "./components/KbPanel";
import { LifecyclePanel } from "./components/LifecyclePanel";
import { ObjectionsPanel } from "./components/ObjectionsPanel";
import { PromptBuilder } from "./components/PromptBuilder";
import { TranscriptsPanel } from "./components/TranscriptsPanel";
import { UsersPanel } from "./components/UsersPanel";
import { Toaster } from "./components/Toaster";
import { api } from "./lib/api";
import { useAuth } from "./lib/auth";
import { useNav } from "./lib/nav";
import { useCaps, useRoles } from "./lib/roles";
import { APP_BACKDROP } from "./lib/theme";

function usePinned() {
  const [pinned, set] = useState<boolean>(() => {
    try {
      return localStorage.getItem("everest.pinned") === "1";
    } catch {
      return false;
    }
  });
  const setPinned = (v: boolean) => {
    set(v);
    try {
      localStorage.setItem("everest.pinned", v ? "1" : "0");
    } catch {
      /* ignore */
    }
  };
  return { pinned, setPinned };
}

export function App() {
  const { token, email, logout } = useAuth();
  const [params, setParams] = useSearchParams();
  const { pinned, setPinned } = usePinned();
  const [hovered, setHovered] = useState(false);
  const pid = params.get("pid");
  const aid = params.get("aid");
  const caps = useCaps(pid);
  const isOrgAdmin = useRoles((s) => s.isOrgAdmin);
  const setRoles = useRoles((s) => s.setRoles);
  const setOrgAdmin = useRoles((s) => s.setOrgAdmin);
  const portfolioName = useNav((s) => s.portfolioName);
  const folderName = useNav((s) => s.folderName);
  const setPortfolioName = useNav((s) => s.setPortfolioName);
  const setFolderName = useNav((s) => s.setFolderName);

  // Readiness poll — if the API/DB is unreachable, show a graceful "service down" page rather
  // than letting panes render misleading empty states (e.g. "no portfolios"). Auto-recovers.
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    retry: 1,
    refetchInterval: 15000,
    refetchOnWindowFocus: true,
  });

  // Bootstrap the caller's per-portfolio roles + org-admin flag into the store regardless of
  // which pane is mounted — so a deep-link / refresh straight to a folder (where the left pane
  // shows Folders, not Portfolios) still resolves capabilities correctly. Shares the cache with
  // PortfoliosPane (same query key), so it's not an extra fetch in the normal flow.
  const rolesBoot = useQuery({
    queryKey: ["portfolios", 0],
    queryFn: () => api.listPortfolios({ limit: 25, offset: 0 }),
    enabled: !!token,
  });
  useEffect(() => {
    const d = rolesBoot.data;
    if (!d) return;
    setRoles(Object.fromEntries(d.items.map((p) => [p.id, p.my_role ?? ""])));
    setOrgAdmin(d.isOrgAdmin);
  }, [rolesBoot.data, setRoles, setOrgAdmin]);

  // Keep the breadcrumb labels honest when selection is cleared (e.g. via the breadcrumb itself).
  useEffect(() => {
    if (!pid) setPortfolioName(null);
  }, [pid, setPortfolioName]);
  useEffect(() => {
    if (!aid) setFolderName(null);
  }, [aid, setFolderName]);

  if (health.isError)
    return <ServiceDown onRetry={() => health.refetch()} retrying={health.isFetching} />;

  if (!token) return <Login />;

  const reportId = params.get("report");
  const checklist = params.get("checklist");
  const kb = params.get("kb");
  const activity = params.get("activity");
  const objections = params.get("objections");
  const transcripts = params.get("transcripts");
  const promptsView = params.get("prompts");
  const summary = params.get("summary");
  const lifecycle = params.get("lifecycle");
  const users = params.get("users");

  // Selection lives in the URL (§11), so panes deep-link and the back button works.
  const VIEWS = [
    "report", "checklist", "kb", "activity", "objections", "transcripts", "prompts", "summary",
    "lifecycle", "users",
  ] as const;
  const select = (next: {
    pid?: string;
    aid?: string;
    report?: string;
    checklist?: string;
    kb?: string;
    activity?: string;
    objections?: string;
    transcripts?: string;
    prompts?: string;
    summary?: string;
    lifecycle?: string;
    users?: string;
  }) => {
    const p = new URLSearchParams(params);
    if ("pid" in next) {
      next.pid ? p.set("pid", next.pid) : p.delete("pid");
      ["aid", ...VIEWS].forEach((k) => p.delete(k));
    }
    if ("aid" in next) {
      next.aid ? p.set("aid", next.aid) : p.delete("aid");
      VIEWS.forEach((k) => p.delete(k));
    }
    // Views are mutually exclusive — selecting one clears the others (so switching from, say,
    // Objections to Transcripts actually swaps the panel instead of stacking params).
    const viewKeys = VIEWS.filter((v) => v in next);
    if (viewKeys.length) {
      VIEWS.forEach((v) => p.delete(v));
      for (const v of viewKeys) {
        const val = (next as Record<string, string | undefined>)[v];
        if (val) p.set(v, val);
      }
    }
    setParams(p);
  };

  const selectFolder = (id: string, name: string) => {
    setFolderName(name);
    select({ aid: id });
  };

  // Whether the main area is showing a special panel (each owns its own chrome) vs. the default
  // portfolio → folders → calls drill-down (which gets the breadcrumb).
  const inPanel =
    !!activity || !!lifecycle || !!promptsView || !!objections || !!transcripts || !!kb ||
    !!checklist || !!reportId || !!summary || !!users;
  const showCrumb = !inPanel && !!pid;

  const expanded = pinned || hovered;
  const collapsedSidebar = !expanded;
  const cols = (pinned ? "280px" : "76px") + " minmax(0, 1fr)";
  const crumb = "rounded-md px-1.5 py-0.5 transition hover:bg-black/5 hover:text-[#8c3a55]";

  const renderMain = () => {
    if (activity) return <ActivityPanel onClose={() => select({ activity: undefined })} />;
    if (lifecycle) return <LifecyclePanel onClose={() => select({ lifecycle: undefined })} />;
    if (promptsView) return <PromptBuilder onClose={() => select({ prompts: undefined })} />;
    if (users && pid)
      return (
        <UsersPanel
          portfolioId={pid}
          onBackToPortfolios={() => select({ pid: undefined })}
          onOpenFolders={() => select({ users: undefined })}
        />
      );
    if (objections && pid)
      return <ObjectionsPanel portfolioId={pid} onClose={() => select({ objections: undefined })} />;
    if (transcripts && pid)
      return <TranscriptsPanel portfolioId={pid} onClose={() => select({ transcripts: undefined })} />;
    if (kb && pid) return <KbPanel portfolioId={pid} onClose={() => select({ kb: undefined })} />;
    if (checklist && pid)
      return <ChecklistBuilder portfolioId={pid} onClose={() => select({ checklist: undefined })} />;
    if (reportId)
      return <ReportView reportId={reportId} onBack={() => select({ report: undefined })} />;
    if (summary && pid)
      return (
        <BatchSummary
          portfolioId={pid}
          batchId={summary}
          onOpenReport={(id) => select({ report: id })}
          onClose={() => select({ summary: undefined })}
        />
      );
    if (!pid)
      return (
        <div className="flex h-full flex-col items-center justify-center p-8 text-center">
          <span className="mb-3 text-4xl">📂</span>
          <h2 className="text-lg font-semibold text-ink">Welcome to Everest Auditor</h2>
          <p className="mt-1 max-w-sm text-sm text-gray-500">
            Pick a portfolio on the left to see its folders, then open a folder to review its calls.
          </p>
        </div>
      );
    if (!aid) return <FoldersGrid portfolioId={pid} onOpen={selectFolder} />;
    return (
      <CallsPane
        portfolioId={pid}
        agentId={aid}
        agentName={folderName}
        onOpenReport={(id) => select({ report: id })}
        onOpenSummary={(bid) => select({ summary: bid })}
      />
    );
  };

  return (
    <div style={APP_BACKDROP} className="flex h-screen flex-col">
      <header className="relative z-50 flex items-center justify-between border-b border-white/30 bg-white/30 px-5 py-3 backdrop-blur-xl">
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold text-[#3a2417]">Everest Auditor</span>
        </div>
        <div className="flex items-center gap-3 text-sm text-[#4a3322]">
          {pid && caps.canManage && <ExportMenu portfolioId={pid} />}
          {pid && caps.canManage && (
            <>
              <button
                className="rounded-lg border border-white/50 bg-white/40 px-2.5 py-1 hover:bg-white/70"
                onClick={() => select({ objections: "1" })}
              >
                Objections
              </button>
              <button
                className="rounded-lg border border-white/50 bg-white/40 px-2.5 py-1 hover:bg-white/70"
                onClick={() => select({ transcripts: "1" })}
              >
                Transcripts
              </button>
              <button
                className="rounded-lg border border-white/50 bg-white/40 px-2.5 py-1 hover:bg-white/70"
                onClick={() => select({ kb: "1" })}
              >
                Knowledge base
              </button>
              <button
                className="rounded-lg border border-white/50 bg-white/40 px-2.5 py-1 hover:bg-white/70"
                onClick={() => select({ checklist: "1" })}
              >
                Checklist builder
              </button>
            </>
          )}
          {(isOrgAdmin || caps.isSuperAdmin) && (
            <>
              <button
                className="rounded-lg border border-white/50 bg-white/40 px-2.5 py-1 hover:bg-white/70"
                onClick={() => select({ prompts: "1" })}
              >
                Agent Studio
              </button>
              <button
                className="rounded-lg border border-white/50 bg-white/40 px-2.5 py-1 hover:bg-white/70"
                onClick={() => select({ lifecycle: "1" })}
              >
                Lifecycle
              </button>
              <button
                className="rounded-lg border border-white/50 bg-white/40 px-2.5 py-1 hover:bg-white/70"
                onClick={() => select({ activity: "1" })}
              >
                Activity
              </button>
            </>
          )}
          <span className="font-medium">{email}</span>
          <button
            className="rounded-lg border border-white/50 bg-white/40 px-2.5 py-1 hover:bg-white/70"
            onClick={logout}
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 gap-3 p-3" style={{ gridTemplateColumns: cols }}>
        {/* Left rail: collapses to icons, expands on hover (overlaying the content) or when pinned. */}
        <div
          className="relative z-30"
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          <div
            className="absolute inset-y-0 left-0 transition-[width] duration-200 ease-out [&>aside]:h-full [&>aside]:w-full"
            style={{ width: expanded ? 280 : 76 }}
          >
            {aid && pid ? (
              <AgentsPane
                portfolioId={pid}
                selectedId={aid}
                collapsed={collapsedSidebar}
                pinned={pinned}
                setPinned={setPinned}
                onSelect={selectFolder}
                onBack={() => select({ aid: undefined })}
              />
            ) : (
              <PortfoliosPane
                selectedId={pid}
                collapsed={collapsedSidebar}
                pinned={pinned}
                setPinned={setPinned}
                onSelect={(id) => select({ pid: id })}
                onUsers={(id) => select({ pid: id, users: "1" })}
              />
            )}
          </div>
        </div>

        <div className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-white/50 bg-white/80 shadow-[0_10px_40px_-12px_rgba(80,20,40,0.35)] backdrop-blur-xl">
          {showCrumb && (
            <nav className="flex shrink-0 items-center gap-1 border-b border-white/50 px-5 py-2.5 text-sm text-gray-500">
              <button onClick={() => select({ pid: undefined })} className={crumb}>
                Portfolios
              </button>
              {pid && (
                <>
                  <span className="text-gray-300">›</span>
                  <button
                    onClick={() => select({ aid: undefined })}
                    className={aid ? crumb : "px-1.5 py-0.5 font-semibold text-ink"}
                  >
                    {portfolioName || "Portfolio"}
                  </button>
                </>
              )}
              {aid && (
                <>
                  <span className="text-gray-300">›</span>
                  <span className="px-1.5 py-0.5 font-semibold text-ink">
                    {folderName || "Folder"}
                  </span>
                </>
              )}
            </nav>
          )}
          <div className="min-h-0 flex-1 overflow-auto">{renderMain()}</div>
        </div>
      </div>
      <Toaster />
    </div>
  );
}
