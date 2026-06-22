import { create } from "zustand";

const TOKEN_KEY = "everest.token";
const EMAIL_KEY = "everest.email";

// "Remember me" → localStorage (survives browser restart); unchecked → sessionStorage
// (cleared when the tab closes). On load we read whichever holds the token.
function read(key: string): string | null {
  return localStorage.getItem(key) ?? sessionStorage.getItem(key);
}

interface AuthState {
  token: string | null;
  email: string | null;
  setAuth: (token: string, email: string, remember?: boolean) => void;
  logout: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  token: read(TOKEN_KEY),
  email: read(EMAIL_KEY),
  setAuth: (token, email, remember = true) => {
    const keep = remember ? localStorage : sessionStorage;
    const drop = remember ? sessionStorage : localStorage;
    keep.setItem(TOKEN_KEY, token);
    keep.setItem(EMAIL_KEY, email);
    drop.removeItem(TOKEN_KEY);
    drop.removeItem(EMAIL_KEY);
    set({ token, email });
  },
  logout: () => {
    for (const s of [localStorage, sessionStorage]) {
      s.removeItem(TOKEN_KEY);
      s.removeItem(EMAIL_KEY);
    }
    set({ token: null, email: null });
  },
}));
