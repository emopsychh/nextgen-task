import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import {
  api,
  isAbortError,
  unwrapList,
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

/** Stable tag ids stored in API; labels shown in UI. */
const PRESET_TAGS: { id: string; label: string; hint: string }[] = [
  {
    id: "upsell",
    label: "Допродажа",
    hint: "Можно предложить клиенту доп. работы или пакет",
  },
  {
    id: "bug",
    label: "Баг",
    hint: "Ошибка, поломка или регресс",
  },
  {
    id: "content",
    label: "Контент",
    hint: "Тексты, дизайн, материалы, наполнение",
  },
  {
    id: "integration",
    label: "Интеграция",
    hint: "Bitrix, CRM, сервисы и связки",
  },
  {
    id: "process",
    label: "Процесс",
    hint: "Как работаем внутри, а не фича продукта",
  },
];

const LEGACY_TAG_MAP: Record<string, string> = {
  upsell: "upsell",
  баг: "bug",
  bug: "bug",
  контент: "content",
  content: "content",
  интеграция: "integration",
  integration: "integration",
  процесс: "process",
  process: "process",
  допродажа: "upsell",
};

function normalizeTag(raw: string): string {
  const key = (raw || "").trim().toLowerCase();
  return LEGACY_TAG_MAP[key] || LEGACY_TAG_MAP[raw] || key;
}

function tagLabel(id: string): string {
  const normalized = normalizeTag(id);
  return PRESET_TAGS.find((t) => t.id === normalized)?.label || id;
}

function itemHasTag(item: BacklogItem, tagId: string): boolean {
  return (item.tags || []).some((t) => normalizeTag(t) === tagId);
}

function toggleTagList(current: string[], tagId: string): string[] {
  const normalized = [...new Set((current || []).map(normalizeTag).filter(Boolean))];
  return normalized.includes(tagId)
    ? normalized.filter((t) => t !== tagId)
    : [...normalized, tagId];
}

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tagFilter, setTagFilter] = useState("");
  const [hideClosed, setHideClosed] = useState(true);

  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [createPriority, setCreatePriority] = useState<BacklogPriority>(1);
  const [createTags, setCreateTags] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);

  const [selectedId, setSelectedId] = useState<number | null>(null);
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
      const data = await api<BacklogItem[]>(
        `/api/backlog-items/?portal=${portalId}`,
        { signal },
        token
      );
      if (signal?.aborted) return;
      setItems(sortItems(Array.isArray(data) ? data : []));
    },
    [token, portalId]
  );

  useEffect(() => {
    if (!portalId) return;
    const cached = getPortalLabel(portalId);
    setPageTitle(cached ? `Бэклог · ${cached}` : "Бэклог");
  }, [portalId]);

  useEffect(() => {
    if (selectedId == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelectedId(null);
        setEditTitle("");
        setEditNotes("");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId]);

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
    void api<Project[] | Paginated<Project>>(
      `/api/projects/?portal=${portalId}`,
      { signal: ac.signal },
      token
    )
      .then((projectsData) => {
        if (ac.signal.aborted) return;
        setProjects(unwrapList(projectsData).filter((p) => p.portal === portalId));
      })
      .catch(() => undefined);
    return () => ac.abort();
  }, [token, portalId, isAgency]);

  const visibleStages = useMemo(
    () =>
      hideClosed
        ? FUNNEL_STAGES.filter((s) => s.id !== "done" && s.id !== "converted")
        : FUNNEL_STAGES,
    [hideClosed]
  );

  const visibleItems = useMemo(() => {
    if (!tagFilter) return items;
    return items.filter((item) => itemHasTag(item, tagFilter));
  }, [items, tagFilter]);

  const columns = useMemo(() => {
    const map = Object.fromEntries(
      FUNNEL_STAGES.map((s) => [s.id, [] as BacklogItem[]])
    ) as Record<BacklogStatus, BacklogItem[]>;
    for (const item of visibleItems) {
      (map[item.status] || map.idea).push(item);
    }
    return map;
  }, [visibleItems]);

  const selected = useMemo(
    () => (selectedId == null ? null : items.find((i) => i.id === selectedId) || null),
    [items, selectedId]
  );

  // Title / notes autosave — same as chips (status, priority, tags).
  useEffect(() => {
    if (!isAgency || selectedId == null || !selected) return;
    const nextTitle = editTitle.trim();
    const nextNotes = editNotes.trim();
    if (!nextTitle) return;
    if (
      nextTitle === selected.title &&
      nextNotes === (selected.notes || "").trim()
    ) {
      return;
    }
    const timer = window.setTimeout(() => {
      if (!token) return;
      setSavingId(selectedId);
      void api<BacklogItem>(
        `/api/backlog-items/${selectedId}/`,
        {
          method: "PATCH",
          body: JSON.stringify({ title: nextTitle, notes: nextNotes }),
        },
        token
      )
        .then((updated) => {
          setItems((prev) => sortItems(prev.map((it) => (it.id === selectedId ? updated : it))));
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : "Не удалось сохранить");
        })
        .finally(() => setSavingId((cur) => (cur === selectedId ? null : cur)));
    }, 450);
    return () => window.clearTimeout(timer);
  }, [
    isAgency,
    token,
    editTitle,
    editNotes,
    selectedId,
    selected?.title,
    selected?.notes,
  ]);

  if (!isAgency) {
    return <Navigate to="/" replace />;
  }
  if (!portalId) {
    return <Navigate to="/" replace />;
  }

  function toggleCreateTag(tagId: string) {
    setCreateTags((prev) => toggleTagList(prev, tagId));
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

  function openItem(item: BacklogItem) {
    setSelectedId(item.id);
    setEditTitle(item.title);
    setEditNotes(item.notes || "");
  }

  function closeItem() {
    setSelectedId(null);
    setEditTitle("");
    setEditNotes("");
  }

  async function confirmDelete() {
    if (!token || !pendingDelete) return;
    setDeleting(true);
    setError(null);
    try {
      await api(`/api/backlog-items/${pendingDelete.id}/`, { method: "DELETE" }, token);
      setItems((prev) => prev.filter((it) => it.id !== pendingDelete.id));
      if (selectedId === pendingDelete.id) closeItem();
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
                  key={tag.id}
                  type="button"
                  className={`backlog-tag${createTags.includes(tag.id) ? " is-on" : ""}`}
                  title={tag.hint}
                  onClick={() => toggleCreateTag(tag.id)}
                  disabled={creating}
                >
                  {tag.label}
                </button>
              ))}
            </div>
            <p className="backlog-tag-hint muted">Наведите на тег — краткое пояснение</p>
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

      <div className="backlog-toolbar">
        <div className="task-filters" role="group" aria-label="Теги">
          <button
            type="button"
            className={`task-filter-chip${tagFilter === "" ? " active" : ""}`}
            aria-pressed={tagFilter === ""}
            onClick={() => setTagFilter("")}
          >
            Все теги
          </button>
          {PRESET_TAGS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`task-filter-chip${tagFilter === t.id ? " active" : ""}`}
              aria-pressed={tagFilter === t.id}
              title={t.hint}
              onClick={() => setTagFilter(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="task-filters backlog-view-toggle" role="group" aria-label="Вид воронки">
          <button
            type="button"
            className={`task-filter-chip${hideClosed ? " active" : ""}`}
            aria-pressed={hideClosed}
            onClick={() => setHideClosed(true)}
          >
            Активные
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

      {loading ? (
        <div className="empty-linked workspace-empty data-loading-state">
          <span className="data-loading-spinner" aria-hidden />
          <p className="muted">Загружаем воронку…</p>
        </div>
      ) : (
        <div className="backlog-funnel">
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
                      return (
                        <article
                          key={item.id}
                          className={`backlog-card${item.is_pinned ? " is-pinned" : ""}${
                            dragId === item.id ? " is-dragging" : ""
                          }`}
                          draggable
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
                          onClick={() => openItem(item)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              openItem(item);
                            }
                          }}
                          role="button"
                          tabIndex={0}
                        >
                          <div className="backlog-card-top">
                            <button
                              type="button"
                              className={`backlog-pin${item.is_pinned ? " is-on" : ""}`}
                              title={item.is_pinned ? "Открепить" : "Закрепить"}
                              disabled={busy}
                              onClick={(e) => {
                                e.stopPropagation();
                                void patchItem(item.id, { is_pinned: !item.is_pinned });
                              }}
                            >
                              {item.is_pinned ? "★" : "☆"}
                            </button>
                            <strong className="backlog-item-title">{item.title}</strong>
                            <span
                              className={`backlog-priority backlog-priority-${item.priority}`}
                            >
                              {priorityLabel(item.priority)}
                            </span>
                          </div>
                          {(item.tags || []).length > 0 ? (
                            <div className="backlog-card-tags">
                              {[...new Set((item.tags || []).map(normalizeTag))].map(
                                (tag) => (
                                  <span
                                    key={tag}
                                    className="backlog-tag is-on"
                                    title={
                                      PRESET_TAGS.find((t) => t.id === tag)?.hint ||
                                      undefined
                                    }
                                  >
                                    {tagLabel(tag)}
                                  </span>
                                )
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

      {selected ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => {
            if (savingId == null && !converting) closeItem();
          }}
        >
          <div
            className="modal-card modal-card-wide backlog-item-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="backlog-item-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="backlog-modal-head">
              <div>
                <p className="backlog-modal-stage muted">
                  {FUNNEL_STAGES.find((s) => s.id === selected.status)?.label ||
                    selected.status}
                  {savingId === selected.id ? " · сохраняем…" : ""}
                </p>
                <h3 id="backlog-item-title" className="modal-title">
                  Идея в бэклоге
                </h3>
              </div>
              <button
                type="button"
                className={`backlog-pin${selected.is_pinned ? " is-on" : ""}`}
                title={selected.is_pinned ? "Открепить" : "Закрепить"}
                disabled={savingId === selected.id}
                onClick={() =>
                  void patchItem(selected.id, { is_pinned: !selected.is_pinned })
                }
              >
                {selected.is_pinned ? "★" : "☆"}
              </button>
            </div>

            <div className="backlog-modal-scroll">
              <div className="stack backlog-modal-body">
                <div className="field">
                  <label>Заголовок</label>
                  <input
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    disabled={savingId === selected.id}
                    autoFocus
                  />
                </div>
                <div className="field">
                  <label>Заметки</label>
                  <textarea
                    value={editNotes}
                    onChange={(e) => setEditNotes(e.target.value)}
                    rows={4}
                    disabled={savingId === selected.id}
                    placeholder="Контекст, ссылки, детали…"
                  />
                </div>
                <div className="field">
                  <label>Этап</label>
                  <div className="task-filters">
                    {FUNNEL_STAGES.map((s) => (
                      <button
                        key={s.id}
                        type="button"
                        className={`task-filter-chip${
                          selected.status === s.id ? " active" : ""
                        }`}
                        disabled={savingId === selected.id || converting}
                        onClick={() => void patchItem(selected.id, { status: s.id })}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="field">
                  <label>Приоритет</label>
                  <div className="task-filters">
                    {PRIORITY_OPTIONS.map((p) => (
                      <button
                        key={p.id}
                        type="button"
                        className={`task-filter-chip${
                          selected.priority === p.id ? " active" : ""
                        }`}
                        disabled={savingId === selected.id}
                        onClick={() => void patchItem(selected.id, { priority: p.id })}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="field">
                  <label>Теги</label>
                  <div className="backlog-tag-picks">
                    {PRESET_TAGS.map((tag) => {
                      const on = itemHasTag(selected, tag.id);
                      return (
                        <button
                          key={tag.id}
                          type="button"
                          className={`backlog-tag${on ? " is-on" : ""}`}
                          title={tag.hint}
                          disabled={savingId === selected.id}
                          onClick={() => {
                            void patchItem(selected.id, {
                              tags: toggleTagList(selected.tags || [], tag.id),
                            });
                          }}
                        >
                          {tag.label}
                        </button>
                      );
                    })}
                  </div>
                  <p className="backlog-tag-hint muted">
                    Подсказка по тегу — при наведении
                  </p>
                </div>
              </div>
            </div>

            <div className="backlog-modal-actions">
              <div className="backlog-modal-actions-primary">
                {selected.converted_project ? (
                  <Link
                    className="btn btn-ghost"
                    to={`/projects/${selected.converted_project}`}
                    onClick={closeItem}
                  >
                    Открыть проект
                  </Link>
                ) : (
                  <button
                    type="button"
                    className="btn btn-ghost"
                    disabled={converting || savingId === selected.id}
                    onClick={() => void convertToProject(selected.id)}
                  >
                    В проект
                  </button>
                )}
                {selected.converted_task ? (
                  <Link
                    className="btn btn-ghost"
                    to={`/tasks/${selected.converted_task}`}
                    onClick={closeItem}
                  >
                    Открыть задачу
                  </Link>
                ) : (
                  <button
                    type="button"
                    className="btn btn-ghost"
                    disabled={converting || savingId === selected.id}
                    onClick={() => {
                      setConvertTaskFor(selected.id);
                      setConvertProjectId(projects[0]?.id ?? "");
                    }}
                  >
                    В задачу
                  </button>
                )}
              </div>
              <div className="backlog-modal-actions-secondary">
                <button
                  type="button"
                  className="btn btn-ghost backlog-btn-danger"
                  disabled={converting || savingId === selected.id}
                  onClick={() =>
                    setPendingDelete({ id: selected.id, title: selected.title })
                  }
                >
                  Удалить
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={closeItem}
                >
                  Готово
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

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
