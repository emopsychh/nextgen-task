import { useState } from "react";
import { Brand } from "../components/Brand";
import { useAuth } from "../auth/AuthContext";

export function LoginPage({ bootError }: { bootError?: string | null }) {
  const { loginDev } = useAuth();
  const [error, setError] = useState<string | null>(bootError || null);
  const [busy, setBusy] = useState(false);

  async function enter(role: "agency" | "client") {
    setBusy(true);
    setError(null);
    try {
      await loginDev(role);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка входа");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card stack">
        <Brand />
        <p className="muted" style={{ marginTop: -4 }}>
          Локальная разработка: войдите как агентство или клиент. В Битрикс вход выполняется
          автоматически через placement.
        </p>
        {(error || bootError) && <div className="error-banner">{error || bootError}</div>}
        <button className="btn btn-primary" disabled={busy} onClick={() => void enter("agency")}>
          Войти как агентство
        </button>
        <button className="btn btn-ghost" disabled={busy} onClick={() => void enter("client")}>
          Войти как клиент
        </button>
      </div>
    </div>
  );
}
