import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  api,
  AUTH_EXPIRED_EVENT,
  AUTH_REFRESHED_EVENT,
  type AuthSession,
  type Portal,
  type BitrixUser,
} from "../api/types";

type AuthState = {
  token: string | null;
  portal: Portal | null;
  user: BitrixUser | null;
  loading: boolean;
  error: string | null;
  loginDev: (role: "agency" | "client") => Promise<void>;
  loginBitrix: (payload: Record<string, unknown>) => Promise<void>;
  logout: () => void;
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

function normalizeDomain(domain: string | null | undefined): string {
  return String(domain || "")
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .split("/")[0]
    .trim();
}

/** Agency and client Bitrix iframes share the same Nextgen origin — never reuse
 *  a stored JWT from another portal or постановщик stays the previous opener. */
function storedSessionMatchesBitrix(
  session: AuthSession | null,
  domain: string | null,
  memberId: string | null
): boolean {
  if (!session?.access || !session.portal) return false;
  const storedMember = String(session.portal.member_id || "").trim();
  const incomingMember = String(memberId || "").trim();
  if (incomingMember && storedMember && incomingMember !== storedMember) {
    return false;
  }
  const storedDomain = normalizeDomain(session.portal.domain);
  const incomingDomain = normalizeDomain(domain);
  if (incomingDomain && storedDomain && incomingDomain !== storedDomain) {
    return false;
  }
  return true;
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
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 20_000);
      try {
        const session = await api<AuthSession>("/api/bitrix/auth/", {
          method: "POST",
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        persist(session);
      } finally {
        window.clearTimeout(timer);
      }
    },
    [persist]
  );

  const logout = useCallback(() => persist(null), [persist]);

  useEffect(() => {
    // Bitrix auth is handed off in the URL fragment (never sent to servers /
    // logs). Fall back to the query string for backward compatibility.
    const hash = window.location.hash.startsWith("#")
      ? window.location.hash.slice(1)
      : "";
    const params = new URLSearchParams(hash || window.location.search);
    const authId = params.get("AUTH_ID") || params.get("auth_id");
    const domain = params.get("DOMAIN") || params.get("domain");
    const memberId = params.get("member_id") || params.get("MEMBER_ID");

    async function boot() {
      try {
        if (authId) {
          const previous = loadStored();
          const samePortal = storedSessionMatchesBitrix(previous, domain, memberId);
          // Drop agency session when opening from a client Bitrix (and vice versa).
          if (previous && !samePortal) {
            persist(null);
          }
          // Only paint the previous session if it belongs to this Bitrix portal.
          if (samePortal && previous?.access) {
            setLoading(false);
          }
          try {
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
          } catch (e) {
            // Never keep another portal's JWT after a failed Bitrix handshake.
            if (!samePortal || !loadStored()?.access) {
              persist(null);
              setError(e instanceof Error ? e.message : "Bitrix auth failed");
            }
          }
        }
      } finally {
        setLoading(false);
      }
    }

    void boot();
  }, [loginBitrix, persist]);

  // Keep in-memory token in sync with the api-layer refresh flow, and log out
  // when the refresh token is no longer valid.
  useEffect(() => {
    function onRefreshed(e: Event) {
      const detail = (e as CustomEvent).detail as { access?: string } | undefined;
      if (detail?.access) setToken(detail.access);
    }
    function onExpired() {
      persist(null);
    }
    window.addEventListener(AUTH_REFRESHED_EVENT, onRefreshed);
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => {
      window.removeEventListener(AUTH_REFRESHED_EVENT, onRefreshed);
      window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
    };
  }, [persist]);

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
    }),
    [token, portal, user, loading, error, loginDev, loginBitrix, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside provider");
  return ctx;
}
