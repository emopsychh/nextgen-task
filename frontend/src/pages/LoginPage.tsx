import { Brand } from "../components/Brand";
import { useAuth } from "../auth/AuthContext";
import { useState } from "react";

/** Dev-only role buttons — never shown in production builds. */
const showDevLogin =
  import.meta.env.DEV === true || import.meta.env.VITE_DEV_AUTH === "1";

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
          Откройте приложение из меню Битрикс24 — вход выполнится автоматически.
        </p>
        {(error || bootError) && (
          <div className="error-banner">
            {error || bootError}
            {(error || bootError || "").toLowerCase().includes("privileges") ||
            (error || bootError || "").toLowerCase().includes("insufficient") ? (
              <div style={{ marginTop: 8, fontWeight: 500 }}>
                На портале в карточке локального приложения откройте «Настройка прав»,
                включите как минимум <strong>user</strong> и <strong>task</strong>, сохраните и
                нажмите «Переустановить». Ставить нужно от администратора портала.
              </div>
            ) : null}
          </div>
        )}
        {showDevLogin ? (
          <>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              Dev-режим: вход без Битрикс
            </p>
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={() => void enter("agency")}
            >
              Войти как агентство
            </button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => void enter("client")}>
              Войти как клиент
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}
