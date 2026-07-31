import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { api, isAbortError, type BacklogItem } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { getPortalLabel } from "../../lib/portalLabelCache";

type PendingDelete = { id: number; title: string };

export function ClientBacklog() {
  const { portalId: routePortalId } = useParams();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const portalId = useMemo(() => {
    if (!routePortalId) return null;
    const n = Number(routePortalId);
    return Number.isFinite(n) ? n : null;
  }, [routePortalId]);

  const [items, setItems] = useState<BacklogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [savingId, setSavingId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [pageTitle, setPageTitle] = useState("Бэклог");

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
    if (!portalId) return;
    const cached = getPortalLabel(portalId);
    setPageTitle(cached ? `Бэклог · ${cached}` : "Бэклог");
  }, [portalId]);

  useEffect(() => {
    if (!token || !portalId || !isAgency) return;
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

  if (!isAgency) {
    return <Navigate to="/" replace />;
  }
  if (!portalId) {
    return <Navigate to="/" replace />;
  }

  async function createItem() {
    if (!token || !portalId) return;
    const t = title.trim();
    if (!t) return;
    setCreating(true);
    setError(null);
    try {
      const created = await api<BacklogItem>(
        "/api/backlog-items/",
        {
          method: "POST",
          body: JSON.stringify({
            portal: portalId,
            title: t,
            notes: notes.trim(),
          }),
        },
        token
      );
      setItems((prev) => [created, ...prev]);
      setTitle("");
      setNotes("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать");
    } finally {
      setCreating(false);
    }
  }

  function startEdit(item: BacklogItem) {
    setEditingId(item.id);
    setEditTitle(item.title);
    setEditNotes(item.notes || "");
  }

  function cancelEdit() {
    setEditingId(null);
    setEditTitle("");
    setEditNotes("");
  }

  async function saveEdit(id: number) {
    if (!token) return;
    const t = editTitle.trim();
    if (!t) return;
    setSavingId(id);
    setError(null);
    try {
      const updated = await api<BacklogItem>(
        `/api/backlog-items/${id}/`,
        {
          method: "PATCH",
          body: JSON.stringify({ title: t, notes: editNotes.trim() }),
        },
        token
      );
      setItems((prev) => {
        const next = prev.map((it) => (it.id === id ? updated : it));
        next.sort(
          (a, b) =>
            new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        );
        return next;
      });
      cancelEdit();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSavingId(null);
    }
  }

  async function confirmDelete() {
    if (!token || !pendingDelete) return;
    setDeleting(true);
    setError(null);
    try {
      await api(`/api/backlog-items/${pendingDelete.id}/`, { method: "DELETE" }, token);
      setItems((prev) => prev.filter((it) => it.id !== pendingDelete.id));
      if (editingId === pendingDelete.id) cancelEdit();
      setPendingDelete(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="tasks-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{pageTitle}</h1>
          <p className="page-sub">
            Внутренние заметки агентства по клиенту. Клиент их не видит, в Bitrix не
            синхронизируются.
          </p>
        </div>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      <form
        className="connect-panel stack"
        onSubmit={(e) => {
          e.preventDefault();
          void createItem();
        }}
      >
        <div>
          <h2 className="section-title">Добавить</h2>
        </div>
        <div className="field">
          <label>Заголовок</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Например, Доработать отчёты"
            required
            disabled={creating}
          />
        </div>
        <div className="field">
          <label>Заметки</label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Контекст, идеи, ссылки — только для агентства"
            disabled={creating}
          />
        </div>
        <button
          type="submit"
          className="btn btn-accent"
          disabled={creating || !title.trim()}
          style={{ alignSelf: "start" }}
        >
          {creating ? "Создаём…" : "Добавить в бэклог"}
        </button>
      </form>

      <section className="stack" style={{ marginTop: "1.25rem" }}>
        <h2 className="section-title">Список</h2>
        {loading ? (
          <div className="empty-linked workspace-empty data-loading-state">
            <span className="data-loading-spinner" aria-hidden />
            <p className="muted">Загружаем бэклог…</p>
          </div>
        ) : items.length === 0 ? (
          <div className="empty-linked workspace-empty">
            <p className="muted">Пока пусто — добавьте первую заметку.</p>
          </div>
        ) : (
          <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {items.map((item) => {
              const editing = editingId === item.id;
              return (
                <li key={item.id} className="connect-panel stack">
                  {editing ? (
                    <>
                      <div className="field">
                        <label>Заголовок</label>
                        <input
                          value={editTitle}
                          onChange={(e) => setEditTitle(e.target.value)}
                          disabled={savingId === item.id}
                        />
                      </div>
                      <div className="field">
                        <label>Заметки</label>
                        <textarea
                          value={editNotes}
                          onChange={(e) => setEditNotes(e.target.value)}
                          disabled={savingId === item.id}
                        />
                      </div>
                      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                        <button
                          type="button"
                          className="btn btn-accent"
                          disabled={savingId === item.id || !editTitle.trim()}
                          onClick={() => void saveEdit(item.id)}
                        >
                          {savingId === item.id ? "Сохраняем…" : "Сохранить"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={savingId === item.id}
                          onClick={cancelEdit}
                        >
                          Отмена
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          gap: "0.75rem",
                          alignItems: "baseline",
                          flexWrap: "wrap",
                        }}
                      >
                        <strong>{item.title}</strong>
                        <span className="muted" style={{ fontSize: "0.85rem" }}>
                          {new Date(item.updated_at).toLocaleString("ru-RU")}
                        </span>
                      </div>
                      {item.notes ? (
                        <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{item.notes}</p>
                      ) : (
                        <p className="muted" style={{ margin: 0 }}>
                          Без заметок
                        </p>
                      )}
                      {item.created_by_name ? (
                        <p className="muted" style={{ margin: 0, fontSize: "0.85rem" }}>
                          {item.created_by_name}
                        </p>
                      ) : null}
                      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          onClick={() => startEdit(item)}
                        >
                          Редактировать
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          onClick={() =>
                            setPendingDelete({ id: item.id, title: item.title })
                          }
                        >
                          Удалить
                        </button>
                      </div>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        danger
        title={
          pendingDelete ? `Удалить «${pendingDelete.title}»?` : "Удалить заметку?"
        }
        description="Заметка будет удалена без возможности восстановить."
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
