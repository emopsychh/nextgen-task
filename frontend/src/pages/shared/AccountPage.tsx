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

  const displayName =
    (user?.display_name || user?.name || "").trim() || "Пользователь";
  const organization = (portalInfo?.organization || portalInfo?.name || "").trim();
  const email = (user?.email || "").trim();
  const username = (user?.username || "").trim();
  const roleLabel = portal?.role === "agency" ? "Агентство" : "Клиент";
  const initials = displayName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() || "")
    .join("") || "NG";

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
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <section className="account-identity" aria-label="Профиль">
        <div className="account-avatar" aria-hidden>
          {user?.avatar_url ? <img src={user.avatar_url} alt="" /> : <span>{initials}</span>}
        </div>
        <div className="account-identity-copy">
          <div className="account-identity-head">
            <strong>{displayName}</strong>
            <span className="account-role">{roleLabel}</span>
          </div>
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
        </div>
        <button type="button" className="account-logout" onClick={logout}>
          Выйти
        </button>
      </section>

      <section className="account-panel" aria-label="Смена пароля">
        <h2>Изменить пароль</h2>
        <form className="account-password-form" onSubmit={(e) => void onChangePassword(e)}>
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
            <button type="submit" className="account-save" disabled={busy}>
              {busy ? "Сохраняем…" : "Сохранить пароль"}
            </button>
          </form>
      </section>
    </div>
  );
}
