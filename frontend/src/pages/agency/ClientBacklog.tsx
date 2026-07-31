import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import {
  api,
  isAbortError,
  unwrapList,
  type BacklogAssigneeOption,
  type BacklogItem,
  type BacklogPriority,
  type BacklogStatus,
  type Paginated,
  type Project,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { getPortalLabel } from "../../lib/portalLabelCache";

type PendingDelete = { id: number; title: string };

const STATUS_OPTIONS: { id: BacklogStatus; label: string }[] = [
  { id: "idea", label: "Идея" },
  { id: "in_progress", label: "В работе" },
  { id: "deferred", label: "Отложено" },
  { id: "done", label: "Сделано" },
  { id: "converted", label: "В проект/задачу" },
];

const PRIORITY_OPTIONS: { id: BacklogPriority; label: string }[] = [
  { id: 2, label: "Высокий" },
  { id: 1, label: "Обычный" },
  { id: 0, label: "Низкий" },
];

const PRESET_TAGS = ["upsell", "баг", "контент"];

function formatUpdated(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusLabel(status: BacklogStatus): string {
  return STATUS_OPTIONS.find((s) => s.id === status)?.label || status;
}

function priorityLabel(priority: BacklogPriority): string {
  return PRIORITY_OPTIONS.find((p) => p.id === priority)?.label || "Обычный";
}

export function ClientBacklog() {
  const { portalId: routePortalId } = useParams();
  const navigate = useNavigate();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const portalId = useMemo(() => {
    if (!routePortalId) return null;
    const n = Number(routePortalId);
    return Number.isFinite(n) ? n : null;
  }, [routePortalId]);

  const [items, setItems] = useState<BacklogItem[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [assignees, setAssignees] = useState<BacklogAssigneeOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("active");
  const [tagFilter, setTagFilter] = useState("");
  const [assigneeFilter, setAssigneeFilter] = useState("all");

  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [notesOpen, setNotesOpen] = useState(false);
  const [createPriority, setCreatePriority] = useState<BacklogPriority>(1);
  const [createTags, setCreateTags] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [savingId, setSavingId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [convertTaskFor, setConvertTaskFor] = useState<number | null>(null);
  const [convertProjectId, setConvertProjectId] = useState<number | "">("");
  const [converting, setConverting] = useState(false);
  const [dragId, setDragId] = useState<number | null>(null);
  const [pageTitle, setPageTitle] = useState("Бэклог");

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!token || !portalId) return;
      const params = new URLSearchParams({ portal: String(portalId) });
      if (statusFilter) params.set("status", statusFilter);
      if (tagFilter) params.set("tag", tagFilter);
      if (assigneeFilter !== "all") params.set("assignee", assigneeFilter);
      const data = await api<BacklogItem[]>(
        `/api/backlog-items/?${params}`,
        { signal },
        token
      );
      if (signal?.aborted) return;
      setItems(Array.isArray(data) ? data : []);
    },
    [token, portalId, statusFilter, tagFilter, assigneeFilter]
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

  useEffect(() => {
    if (!token || !portalId || !isAgency) return;
    const ac = new AbortController();
    void Promise.all([
      api<Project[] | Paginated<Project>>(
        `/api/projects/?portal=${portalId}`,
        { signal: ac.signal },
        token
      ),
      api<BacklogAssigneeOption[]>("/api/backlog-items/assignees/", { signal: ac.signal }, token),
    ])
      .then(([projectsData, assigneesData]) => {
        if (ac.signal.aborted) return;
        setProjects(unwrapList(projectsData).filter((p) => p.portal === portalId));
        setAssignees(Array.isArray(assigneesData) ? assigneesData : []);
      })
      .catch(() => undefined);
    return () => ac.abort();
  }, [token, portalId, isAgency]);

  const allTags = useMemo(() => {
    const set = new Set<string>(PRESET_TAGS);
    for (const item of items) {
      for (const t of item.tags || []) set.add(t);
    }
    return [...set];
  }, [items]);

  if (!isAgency) {
    return <Navigate to="/" replace />;
  }
  if (!portalId) {
    return <Navigate to="/" replace />;
  }

  function toggleCreateTag(tag: string) {
    setCreateTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  }

  async function createItem() {
    if (!token || !portalId) return;
    const t = title.trim();
    if (!t) return;
    setCreating(true);
    setError(null);
    try {
      await api<BacklogItem>(
        "/api/backlog-items/",
        {
          method: "POST",
          body: JSON.stringify({
            portal: portalId,
            title: t,
            notes: notes.trim(),
            priority: createPriority,
            tags: createTags,
          }),
        },
        token
      );
      setTitle("");
      setNotes("");
      setNotesOpen(false);
      setCreatePriority(1);
      setCreateTags([]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать");
    } finally {
      setCreating(false);
    }
  }

  async function patchItem(id: number, body: Record<string, unknown>) {
    if (!token) return;
    setSavingId(id);
    setError(null);
    try {
      const updated = await api<BacklogItem>(
        `/api/backlog-items/${id}/`,
        { method: "PATCH", body: JSON.stringify(body) },
        token
      );
      setItems((prev) => {
        const next = prev.map((it) => (it.id === id ? updated : it));
        next.sort((a, b) => {
          if (a.is_pinned !== b.is_pinned) return a.is_pinned ? -1 : 1;
          if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
          if (a.priority !== b.priority) return b.priority - a.priority;
          return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
        });
        return next;
      });
      // If filters hide the new state, refresh list.
      if (
        (statusFilter === "active" &&
          (updated.status === "done" || updated.status === "converted")) ||
        (statusFilter !== "active" &&
          statusFilter &&
          statusFilter !== updated.status) ||
        (tagFilter && !(updated.tags || []).includes(tagFilter))
      ) {
        await load();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSavingId(null);
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
    const t = editTitle.trim();
    if (!t) return;
    await patchItem(id, { title: t, notes: editNotes.trim() });
    cancelEdit();
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

  async function convertToProject(id: number) {
    if (!token) return;
    setConverting(true);
    setError(null);
    try {
      const res = await api<BacklogItem & { project_id: number }>(
        `/api/backlog-items/${id}/convert-project/`,
        { method: "POST", body: "{}" },
        token
      );
      await load();
      if (res.project_id) navigate(`/projects/${res.project_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать проект");
    } finally {
      setConverting(false);
    }
  }

  async function convertToTask() {
    if (!token || !convertTaskFor || !convertProjectId) return;
    setConverting(true);
    setError(null);
    try {
      const res = await api<BacklogItem & { task_id: number }>(
        `/api/backlog-items/${convertTaskFor}/convert-task/`,
        {
          method: "POST",
          body: JSON.stringify({ project: convertProjectId }),
        },
        token
      );
      setConvertTaskFor(null);
      setConvertProjectId("");
      await load();
      if (res.task_id) navigate(`/tasks/${res.task_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать задачу");
    } finally {
      setConverting(false);
    }
  }

  async function onDropReorder(targetId: number) {
    if (!token || !portalId || dragId == null || dragId === targetId) {
      setDragId(null);
      return;
    }
    const from = items.findIndex((i) => i.id === dragId);
    const to = items.findIndex((i) => i.id === targetId);
    if (from < 0 || to < 0) {
      setDragId(null);
      return;
    }
    const next = [...items];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setItems(next);
    setDragId(null);
    try {
      const data = await api<BacklogItem[]>(
        "/api/backlog-items/reorder/",
        {
          method: "POST",
          body: JSON.stringify({
            portal: portalId,
            ordered_ids: next.map((i) => i.id),
          }),
        },
        token
      );
      if (Array.isArray(data)) setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось изменить порядок");
      await load();
    }
  }

  return (
    <div className="tasks-page backlog-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{pageTitle}</h1>
          <p className="page-sub">Идеи и черновики только для агентства</p>
        </div>
        {!loading && items.length > 0 ? (
          <span className="backlog-count muted">{items.length}</span>
        ) : null}
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      <div className="backlog-filters">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          aria-label="Фильтр статуса"
        >
          <option value="active">Активные</option>
          <option value="">Все статусы</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
        <select
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
          aria-label="Фильтр тега"
        >
          <option value="">Все теги</option>
          {allTags.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select
          value={assigneeFilter}
          onChange={(e) => setAssigneeFilter(e.target.value)}
          aria-label="Фильтр ответственного"
        >
          <option value="all">Все ответственные</option>
          <option value="me">Мои</option>
          {assignees.map((a) => (
            <option key={a.id} value={String(a.id)}>
              {a.display_name}
            </option>
          ))}
        </select>
      </div>

      <form
        className="backlog-composer"
        onSubmit={(e) => {
          e.preventDefault();
          void createItem();
        }}
      >
        <div className="backlog-composer-main">
          <input
            className="backlog-composer-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void createItem();
              }
            }}
            placeholder="Быстрая идея…"
            aria-label="Заголовок"
            autoFocus
            disabled={creating}
          />
          {notesOpen ? (
            <textarea
              className="backlog-composer-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Контекст, ссылки, детали…"
              aria-label="Заметки"
              rows={3}
              disabled={creating}
            />
          ) : null}
        </div>
        <div className="backlog-composer-meta">
          <select
            value={createPriority}
            onChange={(e) => setCreatePriority(Number(e.target.value) as BacklogPriority)}
            aria-label="Приоритет"
            disabled={creating}
          >
            {PRIORITY_OPTIONS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
          <div className="backlog-tag-picks">
            {PRESET_TAGS.map((tag) => (
              <button
                key={tag}
                type="button"
                className={`backlog-tag${createTags.includes(tag) ? " is-on" : ""}`}
                onClick={() => toggleCreateTag(tag)}
                disabled={creating}
              >
                {tag}
              </button>
            ))}
          </div>
        </div>
        <div className="backlog-composer-bar">
          <button
            type="button"
            className={`backlog-notes-toggle${notesOpen || notes ? " is-on" : ""}`}
            onClick={() => setNotesOpen((v) => !v)}
            disabled={creating}
          >
            {notesOpen ? "Скрыть заметку" : notes.trim() ? "Заметка · есть текст" : "Заметка"}
          </button>
          <button
            type="submit"
            className="btn btn-accent backlog-composer-submit"
            disabled={creating || !title.trim()}
          >
            {creating ? "…" : "Добавить"}
          </button>
        </div>
      </form>

      <section className="backlog-list-section">
        {loading ? (
          <div className="empty-linked workspace-empty data-loading-state">
            <span className="data-loading-spinner" aria-hidden />
            <p className="muted">Загружаем бэклог…</p>
          </div>
        ) : items.length === 0 ? (
          <div className="backlog-empty">
            <p className="muted">Пока пусто — набросайте первую идею сверху.</p>
          </div>
        ) : (
          <ul className="backlog-list">
            {items.map((item) => {
              const editing = editingId === item.id;
              const busy = savingId === item.id || converting;
              return (
                <li
                  key={item.id}
                  className={`backlog-item${editing ? " is-editing" : ""}${
                    item.is_pinned ? " is-pinned" : ""
                  }${dragId === item.id ? " is-dragging" : ""}`}
                  draggable={!editing}
                  onDragStart={() => setDragId(item.id)}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => void onDropReorder(item.id)}
                  onDragEnd={() => setDragId(null)}
                >
                  {editing ? (
                    <div className="backlog-item-edit">
                      <input
                        className="backlog-composer-title"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        disabled={busy}
                        aria-label="Заголовок"
                      />
                      <textarea
                        className="backlog-composer-notes"
                        value={editNotes}
                        onChange={(e) => setEditNotes(e.target.value)}
                        disabled={busy}
                        rows={3}
                        aria-label="Заметки"
                      />
                      <div className="backlog-item-actions">
                        <button
                          type="button"
                          className="btn btn-accent"
                          disabled={busy || !editTitle.trim()}
                          onClick={() => void saveEdit(item.id)}
                        >
                          Сохранить
                        </button>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={busy}
                          onClick={cancelEdit}
                        >
                          Отмена
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="backlog-item-top">
                        <div className="backlog-item-heading">
                          <span className="backlog-drag" title="Перетащить" aria-hidden>
                            ⋮⋮
                          </span>
                          <button
                            type="button"
                            className={`backlog-pin${item.is_pinned ? " is-on" : ""}`}
                            title={item.is_pinned ? "Открепить" : "Закрепить"}
                            disabled={busy}
                            onClick={() =>
                              void patchItem(item.id, { is_pinned: !item.is_pinned })
                            }
                          >
                            {item.is_pinned ? "★" : "☆"}
                          </button>
                          <strong className="backlog-item-title">{item.title}</strong>
                          <span
                            className={`backlog-priority backlog-priority-${item.priority}`}
                            title={priorityLabel(item.priority)}
                          >
                            {priorityLabel(item.priority)}
                          </span>
                        </div>
                        <time className="backlog-item-time muted" dateTime={item.updated_at}>
                          {formatUpdated(item.updated_at)}
                        </time>
                      </div>
                      {item.notes ? (
                        <p className="backlog-item-notes">{item.notes}</p>
                      ) : null}
                      <div className="backlog-item-controls">
                        <select
                          value={item.status}
                          disabled={busy}
                          aria-label="Статус"
                          onChange={(e) =>
                            void patchItem(item.id, {
                              status: e.target.value as BacklogStatus,
                            })
                          }
                        >
                          {STATUS_OPTIONS.map((s) => (
                            <option key={s.id} value={s.id}>
                              {s.label}
                            </option>
                          ))}
                        </select>
                        <select
                          value={item.priority}
                          disabled={busy}
                          aria-label="Приоритет"
                          onChange={(e) =>
                            void patchItem(item.id, {
                              priority: Number(e.target.value) as BacklogPriority,
                            })
                          }
                        >
                          {PRIORITY_OPTIONS.map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.label}
                            </option>
                          ))}
                        </select>
                        <select
                          value={item.assignee ?? ""}
                          disabled={busy}
                          aria-label="Ответственный"
                          onChange={(e) =>
                            void patchItem(item.id, {
                              assignee: e.target.value ? Number(e.target.value) : null,
                            })
                          }
                        >
                          <option value="">Без ответственного</option>
                          {assignees.map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.display_name}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="backlog-tag-picks">
                        {PRESET_TAGS.map((tag) => {
                          const on = (item.tags || []).includes(tag);
                          return (
                            <button
                              key={tag}
                              type="button"
                              className={`backlog-tag${on ? " is-on" : ""}`}
                              disabled={busy}
                              onClick={() => {
                                const next = on
                                  ? (item.tags || []).filter((t) => t !== tag)
                                  : [...(item.tags || []), tag];
                                void patchItem(item.id, { tags: next });
                              }}
                            >
                              {tag}
                            </button>
                          );
                        })}
                        {(item.tags || [])
                          .filter((t) => !PRESET_TAGS.includes(t))
                          .map((tag) => (
                            <span key={tag} className="backlog-tag is-on">
                              {tag}
                            </span>
                          ))}
                      </div>
                      <div className="backlog-item-meta">
                        <span className="muted">
                          {item.assignee_name || item.created_by_name || statusLabel(item.status)}
                        </span>
                        <div className="backlog-item-actions">
                          {item.converted_project ? (
                            <Link
                              className="btn btn-ghost backlog-item-btn"
                              to={`/projects/${item.converted_project}`}
                            >
                              Проект
                            </Link>
                          ) : (
                            <button
                              type="button"
                              className="btn btn-ghost backlog-item-btn"
                              disabled={busy}
                              onClick={() => void convertToProject(item.id)}
                            >
                              В проект
                            </button>
                          )}
                          {item.converted_task ? (
                            <Link
                              className="btn btn-ghost backlog-item-btn"
                              to={`/tasks/${item.converted_task}`}
                            >
                              Задача
                            </Link>
                          ) : (
                            <button
                              type="button"
                              className="btn btn-ghost backlog-item-btn"
                              disabled={busy}
                              onClick={() => {
                                setConvertTaskFor(item.id);
                                setConvertProjectId(projects[0]?.id ?? "");
                              }}
                            >
                              В задачу
                            </button>
                          )}
                          <button
                            type="button"
                            className="btn btn-ghost backlog-item-btn"
                            disabled={busy}
                            onClick={() => startEdit(item)}
                          >
                            Изменить
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost backlog-item-btn"
                            disabled={busy}
                            onClick={() =>
                              setPendingDelete({ id: item.id, title: item.title })
                            }
                          >
                            Удалить
                          </button>
                        </div>
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

      <ConfirmDialog
        open={convertTaskFor != null}
        title="Создать задачу из бэклога"
        description="Выберите проект клиента, в который попадёт задача."
        confirmLabel={converting ? "Создаём…" : "Создать задачу"}
        cancelLabel="Отмена"
        onCancel={() => {
          if (!converting) {
            setConvertTaskFor(null);
            setConvertProjectId("");
          }
        }}
        onConfirm={() => void convertToTask()}
      >
        <div className="field" style={{ marginTop: 12 }}>
          <label>Проект</label>
          {projects.length === 0 ? (
            <p className="muted">Сначала создайте проект у клиента.</p>
          ) : (
            <select
              value={convertProjectId}
              onChange={(e) =>
                setConvertProjectId(e.target.value ? Number(e.target.value) : "")
              }
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </ConfirmDialog>
    </div>
  );
}
