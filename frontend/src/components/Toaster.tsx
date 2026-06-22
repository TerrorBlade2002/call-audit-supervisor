import { useToasts } from "../lib/toast";

const STYLE: Record<string, string> = {
  success: "border-emerald-300/60 bg-emerald-50/95 text-emerald-900",
  error: "border-rose-300/60 bg-rose-50/95 text-rose-900",
  info: "border-sky-300/60 bg-sky-50/95 text-sky-900",
};

const ICON: Record<string, string> = { success: "✓", error: "✕", info: "ℹ" };

export function Toaster() {
  const { toasts, dismiss } = useToasts();
  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <button
          key={t.id}
          onClick={() => dismiss(t.id)}
          className={`pointer-events-auto flex max-w-sm items-start gap-2.5 rounded-xl border px-4 py-3 text-left text-sm font-medium shadow-lg backdrop-blur ${STYLE[t.kind]}`}
        >
          <span className="mt-0.5 font-bold">{ICON[t.kind]}</span>
          <span>{t.message}</span>
        </button>
      ))}
    </div>
  );
}
