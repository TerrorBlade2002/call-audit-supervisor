import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { capsFor } from "../lib/roles";

// Multi-checkbox of the portfolios the caller may manage — the target selector for KB documents
// and checklists, so an artefact is saved to exactly the chosen portfolios (and only those).
export function PortfolioPicker({
  selected,
  onChange,
  className = "",
}: {
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
  className?: string;
}) {
  const { data: options = [] } = useQuery({
    queryKey: ["manageable-portfolios"],
    queryFn: () =>
      api
        .listPortfolios({ limit: 200 })
        .then((r) => r.items.filter((p) => capsFor(p.my_role).canManage)),
  });

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(next);
  };

  return (
    <div className={`flex flex-wrap gap-2 ${className}`}>
      {options.map((p) => (
        <label
          key={p.id}
          className={`flex cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm ${
            selected.has(p.id)
              ? "border-[#dd9aa6] bg-[#dd9aa6]/10 text-ink"
              : "border-gray-200 bg-white text-gray-600"
          }`}
        >
          <input type="checkbox" checked={selected.has(p.id)} onChange={() => toggle(p.id)} />
          <span className="max-w-[180px] truncate">{p.name}</span>
        </label>
      ))}
      {options.length === 0 && (
        <span className="text-xs text-gray-400">No portfolios you can manage.</span>
      )}
    </div>
  );
}
