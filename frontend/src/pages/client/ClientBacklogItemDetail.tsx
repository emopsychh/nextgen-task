import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { api, isAbortError, type BacklogItem } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { SmartBackButton } from "../../components/SmartBackButton";
import { formatDateTime } from "../../lib/format";

const STAGE_LABEL: Record<string, string> = {
  idea: "Новая",
  in_progress: "Запланирована",
  deferred: "Отложена",
  converted: "Передана в работу",
  done: "Завершена",
};

export function ClientBacklogItemDetail() {
  const { requestId } = useParams();
  const navigate = useNavigate();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const [item, setItem] = useState<BacklogItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!token || !requestId) return;
    const ac = new AbortController();
    setLoading(true);
    setError(null);
    void api<BacklogItem>(`/api/backlog-items/${requestId}/`, { signal: ac.signal }, token)
      .then((data) => {
        if (ac.signal.aborted) return;
        setItem(data);
        setTitle(data.title);
        setNotes(data.notes || "");
      })
      .catch((err) => {
        if (!isAbortError(err)) setError(err instanceof Error ? err.message : "Не удалось загрузить заявку");
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [token, requestId]);

  if (isAgency) return <Navigate to="/" replace />;

  async function save() {
    if (!token || !item || !title.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api<BacklogItem>(
        `/api/backlog-items/${item.id}/`,
        { method: "PATCH", body: JSON.stringify({ title: title.trim(), notes: notes.trim() }) },
        token
      );
      setItem(updated);
      setTitle(updated.title);
      setNotes(updated.notes || "");
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить заявку");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!token || !item) return;
    setDeleting(true);
    setError(null);
    try {
      await api(`/api/backlog-items/${item.id}/`, { method: "DELETE" }, token);
      navigate("/requests", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить заявку");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="task-detail-page backlog-detail-page">
      <div className="task-detail-topbar">
        <SmartBackButton fallback="/requests" className="task-back" title="К бэклогу">
          <span className="task-back-label">Беклог</span>
        </SmartBackButton>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}
      {loading ? (
        <div className="empty-linked workspace-empty data-loading-state">
          <span className="data-loading-spinner" aria-hidden />
          <p className="muted">Загружаем заявку…</p>
        </div>
      ) : !item ? (
        <div className="empty-linked workspace-empty"><p className="muted">Заявка не найдена.</p></div>
      ) : (
        <section className="backlog-detail-card">
          <div className="backlog-detail-status">
            <span className="task-status-pill status-progress">{STAGE_LABEL[item.status] || "Новая"}</span>
            <span className="muted">Создана {formatDateTime(item.created_at)}</span>
          </div>

          {editing ? (
            <form className="backlog-detail-form stack" onSubmit={(event) => { event.preventDefault(); void save(); }}>
              <div className="field">
                <label>Название</label>
                <input value={title} onChange={(event) => setTitle(event.target.value)} required autoFocus />
              </div>
              <div className="field">
                <label>Описание</label>
                <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={7} />
              </div>
              <div className="request-card-actions">
                <button className="btn btn-primary" disabled={saving || !title.trim()}>
                  {saving ? "Сохраняем…" : "Сохранить"}
                </button>
                <button type="button" className="btn btn-ghost" disabled={saving} onClick={() => setEditing(false)}>
                  Отмена
                </button>
              </div>
            </form>
          ) : (
            <>
              <h1 className="backlog-detail-title">{item.title}</h1>
              <div className="backlog-detail-description">
                {item.notes || "Описание пока не добавлено."}
              </div>
              <div className="backlog-detail-actions">
                {item.can_edit ? <button type="button" className="btn btn-ghost" onClick={() => setEditing(true)}>Изменить</button> : null}
                {item.can_delete ? <button type="button" className="btn btn-ghost backlog-btn-danger" onClick={() => setConfirmDelete(true)}>Удалить</button> : null}
                {item.converted_task ? (
                  <Link to={`/tasks/${item.converted_task}`} className="btn btn-primary">Открыть задачу</Link>
                ) : null}
              </div>
            </>
          )}
        </section>
      )}

      <ConfirmDialog
        open={confirmDelete}
        danger
        title={item ? `Удалить «${item.title}»?` : "Удалить заявку?"}
        description="Заявку можно удалить, пока её не передали в работу."
        confirmLabel={deleting ? "Удаляем…" : "Удалить"}
        cancelLabel="Оставить"
        onCancel={() => !deleting && setConfirmDelete(false)}
        onConfirm={() => void remove()}
      />
    </div>
  );
}
