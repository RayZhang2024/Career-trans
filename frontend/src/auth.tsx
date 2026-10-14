import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { ApiError, SessionApi, type User } from "./api";

type AuthStatus = "checking" | "authenticated" | "unauthenticated" | "unavailable";
type Auth = { status: AuthStatus; user: User | null; api: SessionApi; signInNotice: string | null; login(email: string, password: string): Promise<void>; register(email: string, password: string): Promise<void>; logout(): void; logoutWithNotice(notice: string): void; retryRestore(): void };
const TOKEN = "career-trans.access-token";
const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(() => sessionStorage.getItem(TOKEN) ? "checking" : "unauthenticated");
  const [user, setUser] = useState<User | null>(null);
  const [signInNotice, setSignInNotice] = useState<string | null>(null);
  const api = useMemo(() => new SessionApi(sessionStorage.getItem(TOKEN), () => clear("Your session expired. Sign in again to continue.")), []);
  function clear(notice: string | null = null) { sessionStorage.removeItem(TOKEN); api.replaceToken(null); setUser(null); setStatus("unauthenticated"); setSignInNotice(notice); }
  async function restore() { try { const found = await api.request<User>("/api/v1/users/me"); setUser(found); setStatus("authenticated"); } catch (error) { if (error instanceof ApiError && error.status === 401) clear("Your session expired. Sign in again to continue."); else if (!(error instanceof DOMException)) setStatus("unavailable"); } }
  useEffect(() => { if (status === "checking") void restore(); }, [status]);
  async function login(email: string, password: string) {
    setSignInNotice(null);
    const response = await api.request<{ access_token: string }>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }, false);
    sessionStorage.setItem(TOKEN, response.access_token); api.replaceToken(response.access_token); setStatus("checking"); await restore();
  }
  async function register(email: string, password: string) { await api.request("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }, false); }
  return <AuthContext.Provider value={{ status, user, api, signInNotice, login, register, logout: () => clear(), logoutWithNotice: (notice) => clear(notice), retryRestore: () => setStatus("checking") }}>{children}</AuthContext.Provider>;
}
export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error("AuthProvider is required."); return value; }
export { ApiError };
