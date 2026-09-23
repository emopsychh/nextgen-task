import { useState, type FormEvent } from "react";
import { api } from "../api/types";
import { ModalPortal } from "./ModalPortal";
import { useAuth } from "../auth/AuthContext";

type Props = {
  open: boolean;
  onClose: () => void;
};

export function ChangePasswordModal({ open, onClose }: Props) {
  const { token, user } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const needsCurrent = Boolean(user); // always ask if we don't know; API enforces

  if (!open) return null;

  async function onSubmit(e: FormEvent) {
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
      setDone(true);
      setCurrentPassword("");
      setNewPassword("");
      setConfirm("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сменить пароль");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModalPortal>
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card stack"
        role="dialog"
        aria-modal="true"
        aria-label="Смена пароля"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="section-title" style={{ margin: 0 }}>
          Сменить пароль
        </h2>
        {done ? (
          <>
            <p className="muted">Пароль обновлён.</p>
            <button type="button" className="btn btn-primary" onClick={onClose}>
              Закрыть
            </button>
          </>
        ) : (
          <form className="stack" onSubmit={(e) => void onSubmit(e)}>
            <label className="field">
              <span>Текущий пароль</span>
              <input
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                disabled={busy}
                placeholder={needsCurrent ? undefined : "если уже задан"}
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
            <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
              <button type="button" className="btn btn-ghost" disabled={busy} onClick={onClose}>
                Отмена
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? "Сохраняем…" : "Сохранить"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
    </ModalPortal>
  );
}
