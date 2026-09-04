import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api, isAbortError, type BacklogItem } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { formatDateTime } from "../../lib/format";

type PendingDelete = { id: number; title: string };
type DraftEdit = { id: number; title: string; notes: string };

function isPending(item: BacklogItem): boolean {
  return item.source === "client" && item.status !== "converted" && !item.converted_task;
}

export function ClientTaskRequests() {
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();
  const portalId = portal?.id ?? null;

  const [items, setItems] = useState<BacklogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [edit, setEdit] = useState<DraftEdit | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!token || !portalId) return;
      const data = await api<BacklogItem[]>(
        `/api/backlog-items/?portal=${portalId}`,
        { signal },
        token
      );
      if (signal?.aborted) return;
      setItems(Array.isArray(data) ? data : []);
    },
    [token, portalId]
  );

  useEffect(() => {
    if (!token || !portalId || isAgency) return;
    setLoading(true);
    setError(null);
    const ac = new AbortController();
    void load(ac.signal)
      .catch((e) => {
        if (!isAbortError(e)) setError(e instanceof Error ? e.message : "Ошибка");
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [token, portalId, isAgency, load]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: Boolean(token && portalId && !isAgency),
    onEvent: () => {
      void load().catch(() => undefined);
    },
  });

  const pending = useMemo(() => items.filter(isPending), [items]);
  const accepted = useMemo(() => items.filter((item) => !isPending(item)), [items]);

  if (isAgency) {
    return <Navigate to="/" replace />;
  }

  async function createRequest() {
    if (!token || !portalId) return;
    const nextTitle = title.trim();
    if (!nextTitle) return;
    setCreating(true);
    setError(null);
    try {
      const created = await api<BacklogItem>(
        "/api/backlog-items/",
        {
          method: "POST",
          body: JSON.stringify({
            portal: portalId,
            title: nextTitle,
            notes: notes.trim(),
          }),
        },
        token
      );
      setItems((prev) => [created, ...prev]);
      setTitle("");
      setNotes("");
      toast.show("Агентство увидит её в бэклоге", "Заявка отправлена");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить заявку");
    } finally {
      setCreating(false);
    }
  }

  async function confirmDelete() {
    if (!token || !pendingDelete) return;
    setDeleting(true);
    setError(null);
    try {
      await api(`/api/backlog-items/${pendingDelete.id}/`, { method: "DELETE" }, token);
      setItems((prev) => prev.filter((item) => item.id !== pendingDelete.id));
      setPendingDelete(null);
      toast.show("Заявка удалена");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить");
    } finally {
      setDeleting(false);
    }
  }

  function startEdit(item: BacklogItem) {
    setEdit({ id: item.id, title: item.title, notes: item.notes || "" });
  }

  async function saveEdit() {
    if (!token || !edit) return;
    const nextTitle = edit.title.trim();
    if (!nextTitle) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api<BacklogItem>(
        `/api/backlog-items/${edit.id}/`,
        {
          method: "PATCH",
          body: JSON.stringify({ title: nextTitle, notes: edit.notes.trim() }),
        },
        token
      );
      setItems((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setEdit(null);
      toast.show("Агентство увидит обновлённый текст", "Заявка сохранена");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="tasks-page request-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">На согласование</h1>
          <p className="page-sub">
            Заявка не попадёт в проект, пока агентство не добавит её в работу. Пока не
            приняли — можно править или удалить.
          </p>
        </div>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <form
        className="connect-panel create-backlog-panel stack"
        onSubmit={(e) => {
          e.preventDefault();
          void createRequest();
        }}
      >
        <div>
          <h2 className="section-title">Новая заявка</h2>
          <p className="muted">Коротко, что нужно сделать. Проект выберет агентство.</p>
        </div>
        <div className="field">
          <label>Название</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Например, Сверстать главную"
            required
          />
        </div>
        <div className="field">
          <label>Описание</label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={4}
            placeholder="Контекст, ссылки, что уже есть"
          />
        </div>
        <button
          type="submit"
          className="btn btn-primary"
          disabled={creating || !title.trim()}
          style={{ alignSelf: "start" }}
        >
          {creating ? "Отправляем…" : "Отправить на согласование"}
        </button>
      </form>

      <section className="request-section">
        <div className="overview-card-head">
          <h2 className="section-title">Ожидают</h2>
          {!loading && pending.length > 0 ? (
            <span className="muted">{pending.length}</span>
          ) : null}
        </div>
        {loading && items.length === 0 ? (
          <div className="empty-linked workspace-empty data-loading-state">
            <span className="data-loading-spinner" aria-hidden />
            <p className="muted">Загружаем заявки…</p>
          </div>
        ) : pending.length === 0 ? (
          <div className="empty-linked workspace-empty">
            <p className="muted">Пока нет заявок на согласование.</p>
          </div>
        ) : (
          <ul className="request-list">
            {pending.map((item) => {
              const isEditing = edit?.id === item.id;
              return (
              <li key={item.id} className={`request-card${isEditing ? " is-editing" : ""}`}>
                {isEditing && edit ? (
                  <form
                    className="request-edit stack"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void saveEdit();
                    }}
                  >
                    <div className="field">
                      <label>Название</label>
                      <input
                        value={edit.title}
                        onChange={(e) =>
                          setEdit((cur) => (cur ? { ...cur, title: e.target.value } : cur))
                        }
                        required
                        autoFocus
                      />
                    </div>
                    <div className="field">
                      <label>Описание</label>
                      <textarea
                        value={edit.notes}
                        onChange={(e) =>
                          setEdit((cur) => (cur ? { ...cur, notes: e.target.value } : cur))
                        }
                        rows={4}
                      />
                    </div>
                    <div className="request-card-actions">
                      <button
                        type="submit"
                        className="btn btn-primary"
                        disabled={saving || !edit.title.trim()}
                      >
                        {saving ? "Сохраняем…" : "Сохранить"}
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={saving}
                        onClick={() => setEdit(null)}
                      >
                        Отмена
                      </button>
                    </div>
                  </form>
                ) : (
                  <>
                <div className="request-card-copy">
                  <strong>{item.title}</strong>
                  {item.notes ? <p>{item.notes}</p> : null}
                  <span className="muted">{formatDateTime(item.created_at)}</span>
                </div>
                <div className="request-card-actions">
                  {item.can_edit ? (
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => startEdit(item)}
                    >
                      Изменить
                    </button>
                  ) : null}
                  {item.can_delete ? (
                    <button
                      type="button"
                      className="btn btn-ghost backlog-btn-danger"
                      onClick={() => setPendingDelete({ id: item.id, title: item.title })}
                    >
                      Удалить
                    </button>
                  ) : null}
                </div>
                  </>
                )}
              </li>
              );
            })}
          </ul>
        )}
      </section>

      {accepted.length > 0 ? (
        <section className="request-section">
          <div className="overview-card-head">
            <h2 className="section-title">Приняты в работу</h2>
          </div>
          <ul className="request-list">
            {accepted.map((item) => (
              <li key={item.id} className="request-card is-accepted">
                <div className="request-card-copy">
                  <strong>{item.title}</strong>
                  <p className="muted">
                    {item.converted_project_name
                      ? `Проект «${item.converted_project_name}»`
                      : "Добавлена в работу"}
                  </p>
                </div>
                {item.converted_task ? (
                  <Link to={`/tasks/${item.converted_task}`} className="btn btn-ghost">
                    Открыть задачу
                  </Link>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        danger
        title={pendingDelete ? `Удалить «${pendingDelete.title}»?` : "Удалить заявку?"}
        description="Заявку можно удалить только пока агентство не приняло её в работу."
        confirmLabel={deleting ? "Удаляем…" : "Удалить"}
        cancelLabel="Оставить"
        onCancel={() => {
          if (!deleting) setPendingDelete(null);
        }}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}
