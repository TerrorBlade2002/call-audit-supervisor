import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { APP_BACKDROP as BACKDROP } from "../lib/theme";

const FIELD_SHADOW = "shadow-[0_12px_28px_-10px_rgba(120,30,30,0.45)]";
const ICON_BOX = { background: "rgba(248,237,216,0.92)" };
const FIELD_BG = { background: "rgba(233,217,188,0.55)" };

function UserIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
      <path d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0 1.8c-4.3 0-8 2.1-8 4.9V21h16v-2.3c0-2.8-3.7-4.9-8-4.9z" />
    </svg>
  );
}

function LockIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
      <path d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm3 8H9V7a3 3 0 0 1 6 0v3z" />
    </svg>
  );
}

export function Login() {
  const setAuth = useAuth((s) => s.setAuth);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { access_token } = await api.login(email.trim().toLowerCase(), password);
      setAuth(access_token, email.trim().toLowerCase(), remember);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Invalid email or password"
          : "Sign-in failed — please try again",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={BACKDROP} className="flex min-h-screen w-full items-center justify-center px-4">
      <form
        onSubmit={submit}
        style={{ fontFamily: "'Lato', system-ui, sans-serif", width: "min(440px, 92vw)" }}
        className="flex flex-col items-center"
      >
        {/* Avatar */}
        <div
          className="mb-9 flex h-28 w-28 items-center justify-center rounded-full"
          style={{ background: "rgba(247,229,201,0.42)" }}
        >
          <svg
            viewBox="0 0 64 64"
            className="h-16 w-16"
            style={{ color: "rgba(166,120,74,0.62)" }}
            fill="currentColor"
            aria-hidden
          >
            <circle cx="32" cy="24" r="11" />
            <path d="M13 53c0-10.5 8.5-17 19-17s19 6.5 19 17z" />
          </svg>
        </div>

        {/* Username */}
        <label className={`flex w-full items-stretch ${FIELD_SHADOW}`}>
          <span className="flex w-[58px] shrink-0 items-center justify-center text-[#3a3733]" style={ICON_BOX}>
            <UserIcon className="h-5 w-5" />
          </span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email"
            aria-label="Email"
            autoComplete="username"
            required
            className="h-[58px] w-full px-4 text-[18px] text-[#3a3733] outline-none placeholder:text-[#7a756d]"
            style={FIELD_BG}
          />
        </label>

        {/* Password */}
        <label className={`mt-3.5 flex w-full items-stretch ${FIELD_SHADOW}`}>
          <span className="flex w-[58px] shrink-0 items-center justify-center text-[#3a3733]" style={ICON_BOX}>
            <LockIcon className="h-5 w-5" />
          </span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            aria-label="Password"
            autoComplete="current-password"
            className="h-[58px] w-full px-4 text-[18px] tracking-[0.18em] text-[#3a3733] outline-none placeholder:tracking-normal placeholder:text-[#7a756d]"
            style={FIELD_BG}
          />
        </label>

        {/* Remember / Forgot */}
        <div className="mt-5 flex w-full items-center justify-between">
          <button type="button" onClick={() => setRemember((v) => !v)} className="flex items-center gap-2.5">
            <span
              className="flex h-[22px] w-[22px] items-center justify-center"
              style={{
                background: remember ? "#5a3b2f" : "rgba(255,255,255,0.15)",
                boxShadow: remember ? "none" : "inset 0 0 0 1.5px rgba(255,255,255,0.6)",
              }}
            >
              {remember && (
                <svg
                  viewBox="0 0 24 24"
                  className="h-3.5 w-3.5"
                  fill="none"
                  stroke="#fff"
                  strokeWidth={3.2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden
                >
                  <path d="M5 12.5l4.5 4.5L19 6.5" />
                </svg>
              )}
            </span>
            <span className="text-[15px] text-[#f3e6d6]">Remember me</span>
          </button>
          <button
            type="button"
            onClick={() => setError("Ask your administrator to reset your password.")}
            className="text-[15px] italic text-[rgba(255,242,231,0.85)] hover:text-white"
          >
            Forgot Password?
          </button>
        </div>

        {error && (
          <p className="mt-4 w-full bg-black/15 px-3 py-2 text-center text-[13px] text-white">{error}</p>
        )}

        {/* Login */}
        <button
          type="submit"
          disabled={busy}
          className="mt-6 h-16 w-full text-[19px] font-bold tracking-[0.28em] text-white transition hover:brightness-105 disabled:opacity-60"
          style={{ background: "#e0a3a6", boxShadow: "0 16px 30px -8px rgba(198,70,84,0.5)" }}
        >
          {busy ? "SIGNING IN…" : "LOGIN"}
        </button>
      </form>
    </div>
  );
}
