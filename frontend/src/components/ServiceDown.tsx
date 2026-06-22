import { APP_BACKDROP } from "../lib/theme";

// Shown when the backend/DB is unreachable, instead of letting pages render misleading empty
// states (e.g. "no portfolios"). Auto-reconnects — the health poll flips back when the service
// returns; the button forces an immediate re-check.
export function ServiceDown({ onRetry, retrying }: { onRetry: () => void; retrying?: boolean }) {
  return (
    <div
      style={APP_BACKDROP}
      className="flex h-screen flex-col items-center justify-center p-6 text-center"
    >
      <div className="max-w-md rounded-3xl border border-white/50 bg-white/70 p-10 shadow-[0_20px_60px_-15px_rgba(80,20,40,0.35)] backdrop-blur-xl">
        <div className="mb-3 text-5xl">🍿</div>
        <h1 className="text-2xl font-bold text-[#3a2417]">Something's off — we're on it</h1>
        <p className="mt-3 text-sm leading-relaxed text-[#5a4433]">
          We can't reach the service right now. Grab some snacks while we get things back up —
          this page reconnects on its own the moment it's healthy again.
        </p>
        <button
          onClick={onRetry}
          disabled={retrying}
          className="mt-6 rounded-xl bg-[#dd9aa6] px-5 py-2 text-sm font-semibold text-white transition hover:bg-[#d28aa6] disabled:opacity-60"
        >
          {retrying ? "Checking…" : "Try again"}
        </button>
        <p className="mt-5 text-xs text-[#8a6a55]">
          Service unavailable — the API or database is starting up or down.
        </p>
      </div>
    </div>
  );
}
