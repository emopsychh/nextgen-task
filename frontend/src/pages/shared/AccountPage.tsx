import { useState, type FormEvent } from "react";
import { api } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";

function roleLabel(role: string | undefined): string {
  if (role === "agency") return "Агентство";
  if (role === "client") return "Клиент";
  return "—";
}

export function AccountPage() {
  const { token, user, portal, logout } = useAuth();
  const toast = useFlashToast();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const displayName =
    (user?.display_name || [user?.name, user?.last_name].filter(Boolean).join(" ") || "").trim() ||
    "—";
  const portalTitle = (portal?.name || portal?.domain || "").trim() || "—";

  async function onChangePassword(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    setError(null);
    if (newPassword.length < 8) {
      setError("Новый пароль — минимум 8 символов");
      return;
    }
    if (newPassword !== confirm) {
      setError("Пароли не совпадают");
      return;
    }
    setBusy(true);
    try {
      await api(
        "/api/auth/change-password/",
        {
          method: "POST",
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
          }),
        },
        token
      );
      setCurrentPassword("");
      setNewPassword("");
      setConfirm("");
      toast.show("Пароль обновлён");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сменить пароль");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="account-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Личный кабинет</h1>
          <p className="page-sub">Профиль, пароль и выход из системы</p>
        </div>
      </div>

      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <div className="account-grid">
        <section className="account-card">
          <h2 className="section-title">Профиль</h2>
          <dl className="account-meta">
            <div>
              <dt>Имя</dt>
              <dd>{displayName}</dd>
            </div>
            {user?.username ? (
              <div>
                <dt>Логин</dt>
                <dd>{user.username}</dd>
              </div>
            ) : null}
            <div>
              <dt>Email</dt>
              <dd>{user?.email?.trim() || "—"}</dd>
            </div>
            <div>
              <dt>Кабинет</dt>
              <dd>{portalTitle}</dd>
            </div>
            <div>
              <dt>Роль</dt>
              <dd>{roleLabel(portal?.role)}</dd>
            </div>
            {portal?.domain ? (
              <div>
                <dt>Портал</dt>
                <dd>{portal.domain}</dd>
              </div>
            ) : null}
          </dl>
        </section>

        <section className="account-card">
          <h2 className="section-title">Смена пароля</h2>
          <p className="muted account-card-lead">
            Пароль нужен для входа через веб. Минимум 8 символов.
          </p>
          <form className="stack" onSubmit={(e) => void onChangePassword(e)}>
            <label className="field">
              <span>Текущий пароль</span>
              <input
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                disabled={busy}
              />
            </label>
            <label className="field">
              <span>Новый пароль</span>
              <input
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                disabled={busy}
                required
                minLength={8}
              />
            </label>
            <label className="field">
              <span>Ещё раз</span>
              <input
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                disabled={busy}
                required
                minLength={8}
              />
            </label>
            {error ? <div className="error-banner">{error}</div> : null}
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? "Сохраняем…" : "Сохранить пароль"}
            </button>
          </form>
        </section>

        <section className="account-card account-card-session">
          <h2 className="section-title">Сессия</h2>
          <p className="muted account-card-lead">
            Выйти из кабинета на этом устройстве. Данные клиентов и задач не удалятся.
          </p>
          <button type="button" className="btn btn-ghost account-logout" onClick={logout}>
            Выйти
          </button>
        </section>
      </div>
    </div>
  );
}
