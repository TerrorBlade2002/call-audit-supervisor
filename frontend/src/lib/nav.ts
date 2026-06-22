import { create } from "zustand";

// Display names for the breadcrumb. Selection itself lives in the URL (pid/aid); this just
// remembers the human-readable labels so the breadcrumb reads "Everest › Night shift" rather
// than raw ids. Panes populate it as their data loads (so it survives a reload too).
interface NavState {
  portfolioName: string | null;
  folderName: string | null;
  setPortfolioName: (n: string | null) => void;
  setFolderName: (n: string | null) => void;
}

export const useNav = create<NavState>((set) => ({
  portfolioName: null,
  folderName: null,
  setPortfolioName: (n) => set((s) => (s.portfolioName === n ? s : { portfolioName: n })),
  setFolderName: (n) => set((s) => (s.folderName === n ? s : { folderName: n })),
}));
