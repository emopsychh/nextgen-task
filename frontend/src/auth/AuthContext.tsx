import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, type AuthSession, type Portal, type BitrixUser } from "../api/types";

type AuthState = {
  token: string | null;
  portal: Portal | null;
  user: BitrixUser | null;
  loading: boolean;
  error: string | null;
  loginDev: (role: "agency" | "client") => Promise<void>;
  loginBitrix: (payload: Record<string, unknown>) => Promise<void>;
  logout: () => void;
  setPortalRole: (role: "agency" | "client") => Promise<void>;
};

const STORAGE_KEY = "nextgen_auth";

const AuthContext = createContext<AuthState | null>(null);

function loadStored(): AuthSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AuthSession) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const stored = loadStored();
  const [token, setToken] = useState<string | null>(stored?.access ?? null);
  const [portal, setPortal] = useState<Portal | null>(stored?.portal ?? null);
  const [user, setUser] = useState<BitrixUser | null>(stored?.user ?? null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const persist = useCallback((session: AuthSession | null) => {
    if (!session) {
      localStorage.removeItem(STORAGE_KEY);
      setToken(null);
      setPortal(null);
      setUser(null);
      return;
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    setToken(session.access);
    setPortal(session.portal);
    setUser(session.user);
  }, []);

  const loginDev = useCallback(
    async (role: "agency" | "client") => {
      setError(null);
      const session = await api<AuthSession>("/api/auth/dev/", {
        method: "POST",
        body: JSON.stringify({
          role,
          member_id: `dev-${role}`,
          name: role === "agency" ? "Наше агентство" : "Клиент Demo",
          domain: `${role}.dev.local`,
          first_name: role === "agency" ? "Агентство" : "Клиент",
          last_name: "Demo",
          bitrix_id: `dev-${role}-user`,
        }),
      });
      persist(session);
    },
    [persist]
  );

  const loginBitrix = useCallback(
    async (payload: Record<string, unknown>) => {
      setError(null);
      const session = await api<AuthSession>("/api/bitrix/auth/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      persist(session);
    },
    [persist]
  );

  const logout = useCallback(() => persist(null), [persist]);

  const setPortalRole = useCallback(
    async (role: "agency" | "client") => {
      if (!token || !portal) return;
      const updated = await api<Portal>(
        `/api/portals/${portal.id}/`,
        { method: "PATCH", body: JSON.stringify({ role }) },
        token
      );
      const raw = loadStored();
      if (raw) {
        persist({ ...raw, portal: updated });
      } else {
        setPortal(updated);
      }
    },
    [token, portal, persist]
  );

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const authId = params.get("AUTH_ID") || params.get("auth_id");
    const domain = params.get("DOMAIN") || params.get("domain");
    const memberId = params.get("member_id") || params.get("MEMBER_ID");

    async function boot() {
      try {
        if (authId) {
          await loginBitrix({
            auth: {
              AUTH_ID: authId,
              REFRESH_ID: params.get("REFRESH_ID") || "",
              AUTH_EXPIRES: params.get("AUTH_EXPIRES") || "3600",
              member_id: memberId,
              domain,
            },
            DOMAIN: domain,
            member_id: memberId,
          });
          window.history.replaceState({}, "", window.location.pathname);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Bitrix auth failed");
      } finally {
        setLoading(false);
      }
    }

    void boot();
  }, [loginBitrix]);

  const value = useMemo(
    () => ({
      token,
      portal,
      user,
      loading,
      error,
      loginDev,
      loginBitrix,
      logout,
      setPortalRole,
    }),
    [token, portal, user, loading, error, loginDev, loginBitrix, logout, setPortalRole]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside provider");
  return ctx;
}
