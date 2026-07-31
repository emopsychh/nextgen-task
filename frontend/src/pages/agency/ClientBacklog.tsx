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

const FUNNEL_STAGES: { id: BacklogStatus; label: string; hint: string }[] = [
  { id: "idea", label: "Идея", hint: "Новые мысли" },
  { id: "in_progress", label: "В работе", hint: "Разбираем" },
  { id: "deferred", label: "Отложено", hint: "На потом" },
  { id: "done", label: "Готово", hint: "Закрыто" },
  { id: "converted", label: "В работу", hint: "Проект / задача" },
];

const PRIORITY_OPTIONS: { id: BacklogPriority; label: string }[] = [
  { id: 2, label: "Высокий" },
  { id: 1, label: "Обычный" },
  { id: 0, label: "Низкий" },
];

const PRESET_TAGS = ["upsell", "баг", "контент"];

function sortItems(list: BacklogItem[]): BacklogItem[] {
  return [...list].sort((a, b) => {
    if (a.is_pinned !== b.is_pinned) return a.is_pinned ? -1 : 1;
    if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
    if (a.priority !== b.priority) return b.priority - a.priority;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  });
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
  const [tagFilter, setTagFilter] = useState("");
  const [assigneeFilter, setAssigneeFilter] = useState("all");
  const [hideClosed, setHideClosed] = useState(true);

  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [createPriority, setCreatePriority] = useState<BacklogPriority>(1);
  const [createTags, setCreateTags] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);

  const [expandedId, setExpandedId] = useState<number | null>(null);
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
  const [dragOverStage, setDragOverStage] = useState<BacklogStatus | null>(null);
  const [pageTitle, setPageTitle] = useState("Бэклог");

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!token || !portalId) return;
      const params = new URLSearchParams({ portal: String(portalId) });
      if (tagFilter) params.set("tag", tagFilter);
      if (assigneeFilter !== "all") params.set("assignee", assigneeFilter);
      const data = await api<BacklogItem[]>(
        `/api/backlog-items/?${params}`,
        { signal },
        token
      );
      if (signal?.aborted) return;
      setItems(sortItems(Array.isArray(data) ? data : []));
    },
    [token, portalId, tagFilter, assigneeFilter]
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

  const visibleStages = useMemo(
    () =>
      hideClosed
        ? FUNNEL_STAGES.filter((s) => s.id !== "done" && s.id !== "converted")
        : FUNNEL_STAGES,
    [hideClosed]
  );

  const columns = useMemo(() => {
    const map = Object.fromEntries(
      FUNNEL_STAGES.map((s) => [s.id, [] as BacklogItem[]])
    ) as Record<BacklogStatus, BacklogItem[]>;
    for (const item of items) {
      (map[item.status] || map.idea).push(item);
    }
    return map;
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
      setCreatePriority(1);
      setCreateTags([]);
      setShowCreate(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать");
    } finally {
      setCreating(false);
    }
  }

  async function patchItem(id: number, body: Record<string, unknown>) {
    if (!token) return null;
    setSavingId(id);
    setError(null);
    try {
      const updated = await api<BacklogItem>(
        `/api/backlog-items/${id}/`,
        { method: "PATCH", body: JSON.stringify(body) },
        token
      );
      setItems((prev) => sortItems(prev.map((it) => (it.id === id ? updated : it))));
      return updated;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
      return null;
    } finally {
      setSavingId(null);
    }
  }

  async function persistOrder(ordered: BacklogItem[]) {
    if (!token || !portalId) return;
    setItems(ordered);
    try {
      const data = await api<BacklogItem[]>(
        "/api/backlog-items/reorder/",
        {
          method: "POST",
          body: JSON.stringify({
            portal: portalId,
            ordered_ids: ordered.map((i) => i.id),
          }),
        },
        token
      );
      if (Array.isArray(data)) setItems(sortItems(data));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось изменить порядок");
      await load();
    }
  }

  async function dropOnStage(stage: BacklogStatus, beforeId?: number) {
    if (dragId == null) return;
    const moving = items.find((i) => i.id === dragId);
    if (!moving) {
      setDragId(null);
      setDragOverStage(null);
      return;
    }

    const others = items.filter((i) => i.id !== dragId);
    const targetCol = others.filter((i) => i.status === stage);
    const rest = others.filter((i) => i.status !== stage);
    let insertAt = targetCol.length;
    if (beforeId != null) {
      const idx = targetCol.findIndex((i) => i.id === beforeId);
      if (idx >= 0) insertAt = idx;
    }
    const moved = { ...moving, status: stage };
    targetCol.splice(insertAt, 0, moved);
    const next = sortItems([...rest, ...targetCol]).map((it, index) => ({
      ...it,
      sort_order: index,
    }));
    // Keep funnel order stable: reorder by stage then sort_order within.
    const byStage: BacklogItem[] = [];
    for (const s of FUNNEL_STAGES) {
      byStage.push(...next.filter((i) => i.status === s.id));
    }
    const ordered = byStage.map((it, index) => ({ ...it, sort_order: index }));

    setItems(ordered);
    setDragId(null);
    setDragOverStage(null);

    if (moving.status !== stage) {
      const ok = await patchItem(moving.id, { status: stage });
      if (!ok) {
        await load();
        return;
      }
    }
    await persistOrder(ordered);
  }

  function startEdit(item: BacklogItem) {
    setExpandedId(item.id);
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
      if (expandedId === pendingDelete.id) setExpandedId(null);
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

  return (
    <div className="tasks-page backlog-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{pageTitle}</h1>
          <p className="page-sub">Воронка идей — тяните карточки по этапам</p>
        </div>
        <div className="backlog-header-actions">
          {!loading && items.length > 0 ? (
            <span className="backlog-count muted">{items.length}</span>
          ) : null}
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setShowCreate((v) => !v)}
          >
            {showCreate ? "Закрыть" : "Новая идея"}
          </button>
        </div>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      {showCreate ? (
        <form
          className="connect-panel create-backlog-panel stack"
          onSubmit={(e) => {
            e.preventDefault();
            void createItem();
          }}
        >
          <div>
            <h2 className="section-title">Новая идея</h2>
            <p className="muted">Попадёт в этап «Идея». Клиент и Bitrix не видят.</p>
          </div>
          <div className="field">
            <label>Заголовок</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Например, Доработать отчёты"
              required
              autoFocus
              disabled={creating}
            />
          </div>
          <div className="field">
            <label>Заметки</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Контекст, ссылки, детали…"
              rows={3}
              disabled={creating}
            />
          </div>
          <div className="field">
            <label>Приоритет</label>
            <div className="task-filters">
              {PRIORITY_OPTIONS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={`task-filter-chip${createPriority === p.id ? " active" : ""}`}
                  disabled={creating}
                  onClick={() => setCreatePriority(p.id)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div className="field">
            <label>Теги</label>
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
          <button
            type="submit"
            className="btn btn-accent"
            disabled={creating || !title.trim()}
            style={{ alignSelf: "start" }}
          >
            {creating ? "Создаём…" : "Добавить в бэклог"}
          </button>
        </form>
      ) : null}

      <div className="backlog-filters">
        <div className="backlog-filter-group" role="group" aria-label="Теги">
          <span className="backlog-filter-label">Теги</span>
          <div className="task-filters">
            <button
              type="button"
              className={`task-filter-chip${tagFilter === "" ? " active" : ""}`}
              aria-pressed={tagFilter === ""}
              onClick={() => setTagFilter("")}
            >
              Все
            </button>
            {allTags.map((t) => (
              <button
                key={t}
                type="button"
                className={`task-filter-chip${tagFilter === t ? " active" : ""}`}
                aria-pressed={tagFilter === t}
                onClick={() => setTagFilter(t)}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <div className="backlog-filter-group" role="group" aria-label="Ответственный">
          <span className="backlog-filter-label">Кто</span>
          <div className="task-filters">
            <button
              type="button"
              className={`task-filter-chip${assigneeFilter === "all" ? " active" : ""}`}
              aria-pressed={assigneeFilter === "all"}
              onClick={() => setAssigneeFilter("all")}
            >
              Все
            </button>
            <button
              type="button"
              className={`task-filter-chip${assigneeFilter === "me" ? " active" : ""}`}
              aria-pressed={assigneeFilter === "me"}
              onClick={() => setAssigneeFilter("me")}
            >
              Мои
            </button>
            {assignees.map((a) => (
              <button
                key={a.id}
                type="button"
                className={`task-filter-chip${
                  assigneeFilter === String(a.id) ? " active" : ""
                }`}
                aria-pressed={assigneeFilter === String(a.id)}
                onClick={() => setAssigneeFilter(String(a.id))}
              >
                {a.display_name}
              </button>
            ))}
          </div>
        </div>
        <div className="backlog-filter-group">
          <span className="backlog-filter-label">Вид</span>
          <div className="task-filters">
            <button
              type="button"
              className={`task-filter-chip${hideClosed ? " active" : ""}`}
              aria-pressed={hideClosed}
              onClick={() => setHideClosed(true)}
            >
              Активная воронка
            </button>
            <button
              type="button"
              className={`task-filter-chip${!hideClosed ? " active" : ""}`}
              aria-pressed={!hideClosed}
              onClick={() => setHideClosed(false)}
            >
              Все этапы
            </button>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="empty-linked workspace-empty data-loading-state">
          <span className="data-loading-spinner" aria-hidden />
          <p className="muted">Загружаем воронку…</p>
        </div>
      ) : (
        <div
          className="backlog-funnel"
          style={{ ["--funnel-cols" as string]: String(visibleStages.length) }}
        >
          {visibleStages.map((stage, stageIndex) => {
            const colItems = columns[stage.id] || [];
            return (
              <section
                key={stage.id}
                className={`backlog-funnel-col backlog-funnel-col-${stage.id}${
                  dragOverStage === stage.id ? " is-drop" : ""
                }`}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOverStage(stage.id);
                }}
                onDragLeave={() => {
                  setDragOverStage((cur) => (cur === stage.id ? null : cur));
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  void dropOnStage(stage.id);
                }}
              >
                <header className="backlog-funnel-head">
                  <div className="backlog-funnel-step">
                    <span className="backlog-funnel-num">{stageIndex + 1}</span>
                    <div>
                      <h2 className="backlog-funnel-title">{stage.label}</h2>
                      <p className="backlog-funnel-hint muted">{stage.hint}</p>
                    </div>
                  </div>
                  <span className="backlog-funnel-count">{colItems.length}</span>
                </header>
                <div className="backlog-funnel-cards">
                  {colItems.length === 0 ? (
                    <p className="backlog-funnel-empty muted">Перетащите сюда</p>
                  ) : (
                    colItems.map((item) => {
                      const busy = savingId === item.id || converting;
                      const expanded = expandedId === item.id;
                      const editing = editingId === item.id;
                      return (
                        <article
                          key={item.id}
                          className={`backlog-card${item.is_pinned ? " is-pinned" : ""}${
                            dragId === item.id ? " is-dragging" : ""
                          }${expanded ? " is-expanded" : ""}`}
                          draggable={!editing}
                          onDragStart={(e) => {
                            e.dataTransfer.effectAllowed = "move";
                            setDragId(item.id);
                          }}
                          onDragEnd={() => {
                            setDragId(null);
                            setDragOverStage(null);
                          }}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            void dropOnStage(stage.id, item.id);
                          }}
                        >
                          <div className="backlog-card-top">
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
                            <button
                              type="button"
                              className="backlog-card-title-btn"
                              onClick={() =>
                                setExpandedId((cur) => (cur === item.id ? null : item.id))
                              }
                            >
                              <strong className="backlog-item-title">{item.title}</strong>
                            </button>
                            <span
                              className={`backlog-priority backlog-priority-${item.priority}`}
                            >
                              {priorityLabel(item.priority)}
                            </span>
                          </div>
                          {(item.tags || []).length > 0 ? (
                            <div className="backlog-card-tags">
                              {(item.tags || []).map((tag) => (
                                <span key={tag} className="backlog-tag is-on">
                                  {tag}
                                </span>
                              ))}
                            </div>
                          ) : null}
                          {item.assignee_name ? (
                            <p className="backlog-card-assignee muted">{item.assignee_name}</p>
                          ) : null}

                          {expanded ? (
                            <div className="backlog-card-body">
                              {editing ? (
                                <div className="backlog-item-edit">
                                  <input
                                    className="backlog-composer-title"
                                    value={editTitle}
                                    onChange={(e) => setEditTitle(e.target.value)}
                                    disabled={busy}
                                  />
                                  <textarea
                                    className="backlog-composer-notes"
                                    value={editNotes}
                                    onChange={(e) => setEditNotes(e.target.value)}
                                    disabled={busy}
                                    rows={3}
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
                                  {item.notes ? (
                                    <p className="backlog-item-notes">{item.notes}</p>
                                  ) : (
                                    <p className="muted">Без заметок</p>
                                  )}
                                  <div className="backlog-item-controls">
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
                                          assignee: e.target.value
                                            ? Number(e.target.value)
                                            : null,
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
                                  </div>
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
                                        setPendingDelete({
                                          id: item.id,
                                          title: item.title,
                                        })
                                      }
                                    >
                                      Удалить
                                    </button>
                                  </div>
                                </>
                              )}
                            </div>
                          ) : null}
                        </article>
                      );
                    })
                  )}
                </div>
              </section>
            );
          })}
        </div>
      )}

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
