import { create } from "zustand";

// Caller's role per portfolio (populated from the portfolios list). Drives UI gating;
// the backend RBAC matrix is the real enforcement — this just hides what they can't do.
interface RoleState {
  roles: Record<string, string>;
  // Org-level admin (super admin) — independent of any portfolio, so org-wide controls
  // (e.g. the Activity log) show even with no portfolio selected or created yet.
  isOrgAdmin: boolean;
  setRoles: (map: Record<string, string>) => void;
  setOrgAdmin: (v: boolean) => void;
  // Wipe all cached roles + org-admin flag — called on logout/login so a new sign-in never
  // inherits the previous user's privileges (which would briefly flash their admin controls).
  reset: () => void;
}

export const useRoles = create<RoleState>((set) => ({
  roles: {},
  isOrgAdmin: false,
  setRoles: (map) => set((s) => ({ roles: { ...s.roles, ...map } })),
  setOrgAdmin: (v) => set({ isOrgAdmin: v }),
  reset: () => set({ roles: {}, isOrgAdmin: false }),
}));

export type Caps = { canManage: boolean; isAgent: boolean; isSuperAdmin: boolean };

export function capsFor(role: string | null | undefined): Caps {
  const r = (role || "").toUpperCase();
  const isSuperAdmin = r === "ADMIN";
  // SUPERVISOR + ADMIN (+ legacy MANAGER) can manage portfolio content.
  const canManage = isSuperAdmin || r === "SUPERVISOR" || r === "MANAGER";
  return { canManage, isAgent: r === "AGENT", isSuperAdmin };
}

export function useCaps(pid: string | null): Caps {
  const roles = useRoles((s) => s.roles);
  return capsFor(pid ? roles[pid] : null);
}
