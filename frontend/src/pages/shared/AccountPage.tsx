import { useEffect, useState, type FormEvent } from "react";
import { api, type Portal } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";

export function AccountPage() {
  const { token, user, portal, logout } = useAuth();
  const toast = useFlashToast();
  const [portalInfo, setPortalInfo] = useState<Portal | null>(portal);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setPortalInfo(portal);
  }, [portal]);

  useEffect(() => {
    if (!token || !portal?.id) return;
    let cancelled = false;
    void api<Portal>(`/api/portals/${portal.id}/`, {}, token)
      .then((data) => {
        if (!cancelled) setPortalInfo(data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token, portal?.id]);

  const organization = (portalInfo?.organization || "").trim();
  const email = (user?.email || "").trim();
  const username = (user?.username || "").trim();
  const hasProfile = Boolean(username || email || organization);

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
        {hasProfile ? (
          <section className="account-card">
            <h2 className="section-title">Профиль</h2>
            <dl className="account-meta">
              {organization ? (
                <div>
                  <dt>Организация</dt>
                  <dd>{organization}</dd>
                </div>
              ) : null}
              {username ? (
                <div>
                  <dt>Логин</dt>
                  <dd>{username}</dd>
                </div>
              ) : null}
              {email ? (
                <div>
                  <dt>Email</dt>
                  <dd>{email}</dd>
                </div>
              ) : null}
            </dl>
          </section>
        ) : null}

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
