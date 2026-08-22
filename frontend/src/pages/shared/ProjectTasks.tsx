import { useEffect, useRef, useState, type MouseEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  isAbortError,
  type Paginated,
  type Project,
  type Task,
  type TaskCounts,
  type TaskStatus,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { DueDatePicker } from "../../components/DueDatePicker";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { useSeenProjects } from "../../hooks/useSeenProjects";
import { dueMeta } from "../../lib/dates";
import {
  readPortalCache,
  readBoardTasksCache,
  writePortalCache,
  writeBoardTasksCache,
} from "../../lib/portalSessionCache";
import { isTaskOverdue, STATUS_LABEL, STATUS_TONE } from "../../lib/status";
import { CalendarGlyph, FlameIcon } from "../../components/icons";
import { SyncHint } from "../../components/SyncHint";
import { displayTimeZone } from "../../lib/timezone";

export function ProjectTasks() {
  const { projectId } = useParams();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const dueTz = displayTimeZone({
    role: portal?.role,
    portalTimezone: portal?.timezone,
  });
  const toast = useFlashToast();

  const numericProjectId = Number(projectId || 0);
  const [project, setProject] = useState<Project | null>(
    () =>
      readPortalCache<Project>("project-meta", numericProjectId) || null
  );
  const { markSeen } = useSeenProjects(project?.portal ?? null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [filter, setFilter] = useState<TaskStatus | "all">("all");
  const [query, setQuery] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [enteringId, setEnteringId] = useState<number | null>(null);
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [counts, setCounts] = useState<TaskCounts>(
    () =>
      readPortalCache<TaskCounts>("task-counts", numericProjectId) || {
        all: 0,
        todo: 0,
        in_progress: 0,
        done: 0,
      }
  );
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [bitrixSyncing, setBitrixSyncing] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const loadedPagesRef = useRef(1);
  const sentinelRef = useRef<HTMLDivElement>(null);
  // Discard responses that resolve after project/filter/search changed.
  const genRef = useRef(0);

  // Filtering / search / sorting now happen server-side, so render as-is.
  const visible = tasks;

  function buildListUrl(page: number, withPull: boolean): string {
    let url = `/api/tasks/?project=${projectId}&page=${page}`;
    if (filter !== "all") url += `&status=${filter}`;
    const q = debouncedQuery.trim();
    if (q) url += `&search=${encodeURIComponent(q)}`;
    if (withPull) url += "&pull=1";
    return url;
  }

  function fetchPage(page: number, withPull: boolean, signal?: AbortSignal) {
    return api<Paginated<Task>>(buildListUrl(page, withPull), { signal }, token!);
  }

  function mergeById(base: Task[], incoming: Task[]): Task[] {
    const seen = new Set(base.map((t) => t.id));
    const merged = base.slice();
    for (const t of incoming) if (!seen.has(t.id)) merged.push(t);
    return merged;
  }

  function dedupeById(list: Task[]): Task[] {
    const seen = new Set<number>();
    return list.filter((t) => (seen.has(t.id) ? false : (seen.add(t.id), true)));
  }

  /** Refresh page-1; drop deleted page-1 rows (do not keep ghosts from prev). */
  function mergePage1(prev: Task[], page1: Task[]): Task[] {
    if (loadedPagesRef.current <= 1) {
      return dedupeById(page1);
    }
    const page1Ids = new Set(page1.map((t) => t.id));
    const older = prev.slice(page1.length).filter((t) => !page1Ids.has(t.id));
    return dedupeById([...page1, ...older]);
  }

  function cacheKeyParts(): [number, string, string] | null {
    const id = Number(projectId);
    if (!id) return null;
    return [id, filter, debouncedQuery.trim()];
  }

  async function loadCounts() {
    if (!token || !projectId) return;
    try {
      const c = await api<TaskCounts>(`/api/tasks/counts/?project=${projectId}`, {}, token);
      setCounts(c);
      writePortalCache("task-counts", Number(projectId), c);
    } catch {
      // non-critical
    }
  }

  async function loadFirst(signal?: AbortSignal) {
    if (!token || !projectId) return;
    const gen = genRef.current;
    const parts = cacheKeyParts();

    // Paint from DB first; Bitrix soft-pull runs in the background.
    const [projectData, taskData] = await Promise.all([
      api<Project>(`/api/projects/${projectId}/`, { signal }, token),
      fetchPage(1, false, signal),
    ]);
    if (gen !== genRef.current || signal?.aborted) return;
    setProject(projectData);
    writePortalCache("project-meta", Number(projectId), projectData);
    setTasks(taskData.results);
    setHasMore(Boolean(taskData.next));
    loadedPagesRef.current = 1;
    if (parts) {
      writeBoardTasksCache(parts[0], parts[1], parts[2], {
        tasks: taskData.results,
        hasMore: Boolean(taskData.next),
      });
    }
    void loadCounts();

    // Background Bitrix status pull — merge when ready.
    setBitrixSyncing(true);
    void fetchPage(1, true, signal)
      .then((pulled) => {
        if (gen !== genRef.current || signal?.aborted) return;
        setTasks((prev) => mergePage1(prev, pulled.results));
        setHasMore(Boolean(pulled.next) || loadedPagesRef.current > 1);
        if (parts) {
          writeBoardTasksCache(parts[0], parts[1], parts[2], {
            tasks: pulled.results,
            hasMore: Boolean(pulled.next),
          });
        }
        void loadCounts();
      })
      .catch((e) => {
        if (!isAbortError(e)) undefined;
      })
      .finally(() => {
        if (gen === genRef.current) setBitrixSyncing(false);
      });
  }

  async function loadMore() {
    if (loadingMore || !hasMore || !token || !projectId) return;
    const gen = genRef.current;
    setLoadingMore(true);
    try {
      const nextPage = loadedPagesRef.current + 1;
      const data = await fetchPage(nextPage, false);
      if (gen !== genRef.current) return;
      setTasks((prev) => mergeById(prev, data.results));
      setHasMore(Boolean(data.next));
      loadedPagesRef.current = nextPage;
    } catch {
      // retry on next scroll
    } finally {
      setLoadingMore(false);
    }
  }

  // Debounce the search box → server-side search.
  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedQuery(query), 300);
    return () => window.clearTimeout(id);
  }, [query]);

  // Opening a project clears it from the "new" badge.
  useEffect(() => {
    const id = Number(projectId);
    if (!id || !project) return;
    markSeen(id);
  }, [projectId, project, markSeen]);

  useEffect(() => {
    // Project routes reuse this component; never keep the previous project's
    // identity or live-sync portal while the new project is resolving.
    const id = Number(projectId || 0);
    setProject(readPortalCache<Project>("project-meta", id));
    setCounts(
      readPortalCache<TaskCounts>("task-counts", id) || {
        all: 0,
        todo: 0,
        in_progress: 0,
        done: 0,
      }
    );
    setError(null);
  }, [projectId]);

  // Reset & reload page 1 whenever project / filter / search change.
  useEffect(() => {
    if (!token || !projectId) return;
    genRef.current += 1;
    const gen = genRef.current;
    setBitrixSyncing(false);
    const ac = new AbortController();
    const parts = cacheKeyParts();
    const cached = parts
      ? readBoardTasksCache(parts[0], parts[1], parts[2])
      : null;
    // An empty cached page is still a valid loaded snapshot. Treating [] as a
    // cache miss made the full-page loader reappear on every revisit.
    if (cached) {
      setTasks(cached.tasks as Task[]);
      setHasMore(Boolean(cached.hasMore));
      setInitialLoading(false);
    } else {
      setInitialLoading(true);
      setTasks([]);
      setHasMore(false);
    }
    loadedPagesRef.current = 1;
    void loadFirst(ac.signal)
      .catch((e) => {
        if (!isAbortError(e)) setError(e instanceof Error ? e.message : "Ошибка");
      })
      .finally(() => {
        if (gen === genRef.current && !ac.signal.aborted) {
          setInitialLoading(false);
        }
      });
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, projectId, filter, debouncedQuery]);

  // Infinite scroll: auto-load the next page when the sentinel comes into view.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) void loadMore();
      },
      { rootMargin: "300px 0px" }
    );
    io.observe(el);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasMore, loadingMore, filter, debouncedQuery]);

  const pullNowRef = useRef(false);
  usePortalLiveSync({
    token,
    portalId: project?.portal ?? null,
    enabled: !!projectId,
    onEvent: () => {
      // The 2.5s local poll will pick up the DB change. Do not turn a
      // pull-complete event into another Bitrix pull.
      pullNowRef.current = false;
    },
  });

  // Soft realtime: refresh page 1 locally; Bitrix catch-up about once a minute.
  useEffect(() => {
    if (!token || !projectId) return;
    let cancelled = false;
    let inFlight = false;
    let tickCount = 0;
    let tickAc: AbortController | null = null;

    async function tick() {
      if (cancelled || inFlight) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      inFlight = true;
      tickCount += 1;
      tickAc?.abort();
      tickAc = new AbortController();
      const signal = tickAc.signal;
      try {
        const wantPull = pullNowRef.current || tickCount % 24 === 0;
        pullNowRef.current = false;
        const data = await fetchPage(1, wantPull, signal);
        if (cancelled || signal.aborted) return;
        setTasks((prev) => mergePage1(prev, data.results));
        if (loadedPagesRef.current <= 1) {
          setHasMore(Boolean(data.next));
        }
        void loadCounts();
      } catch (e) {
        if (!isAbortError(e)) undefined;
      } finally {
        inFlight = false;
      }
    }

    const id = window.setInterval(() => void tick(), 2500);
    const onVisible = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      tickAc?.abort();
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, projectId, filter, debouncedQuery]);

  async function createTask(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api<Task>(
        "/api/tasks/",
        {
          method: "POST",
          body: JSON.stringify({
            project: Number(projectId),
            title,
            description,
            due_date: dueDate || null,
            status: "todo",
          }),
        },
        token
      );
      setTitle("");
      setDescription("");
      setDueDate("");
      setShowCreate(false);
      setEnteringId(created.id);
      toast.show("Она появилась в списке ниже", "Задача создана");
      await loadFirst();
      window.dispatchEvent(new Event("projects-updated"));
      window.setTimeout(() => setEnteringId(null), 900);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать задачу");
    } finally {
      setBusy(false);
    }
  }

  async function deleteTask(task: Task, e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!token || !task.can_delete || deletingId) return;
    if (
      !window.confirm(
        `Удалить задачу «${task.title}»? Её можно удалить только пока в ней нет описания, комментариев, файлов и учёта времени.`
      )
    ) {
      return;
    }
    setDeletingId(task.id);
    setError(null);
    try {
      await api(`/api/tasks/${task.id}/`, { method: "DELETE" }, token);
      setTasks((prev) => prev.filter((t) => t.id !== task.id));
      setCounts((prev) => {
        const next = { ...prev, all: Math.max(0, prev.all - 1) };
        if (task.status in next) {
          const key = task.status as TaskStatus;
          next[key] = Math.max(0, (next[key] || 0) - 1);
        }
        return next;
      });
      toast.show("Задача удалена");
      window.dispatchEvent(new Event("projects-updated"));
      void loadCounts();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить задачу");
    } finally {
      setDeletingId(null);
    }
  }

  const filters: { id: TaskStatus | "all"; label: string; count: number }[] = [
    { id: "all", label: "Все", count: counts.all },
    { id: "todo", label: STATUS_LABEL.todo, count: counts.todo },
    { id: "in_progress", label: STATUS_LABEL.in_progress, count: counts.in_progress },
    { id: "done", label: STATUS_LABEL.done, count: counts.done },
  ];

  return (
    <div className="tasks-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{project?.name || "Задачи"}</h1>
          <p className="page-sub">
            {counts.all
              ? `${counts.done} из ${counts.all} выполнено`
              : "Задачи этого модуля"}
            {bitrixSyncing ? <SyncHint>Обновляем статусы…</SyncHint> : null}
          </p>
        </div>
        <div className="report-header-actions">
          {project?.portal ? (
            <Link
              to={
                isAgency
                  ? `/portals/${project.portal}/reports`
                  : "/reports"
              }
              className="btn btn-ghost"
            >
              Отчёты
            </Link>
          ) : null}
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setShowCreate((v) => !v)}
            data-tour="tour-new-task"
          >
            {showCreate ? "Закрыть" : "Новая задача"}
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      {showCreate && (
        <form className="connect-panel create-task-panel stack" onSubmit={createTask}>
          <div>
            <h2 className="section-title">Новая задача</h2>
            <p className="muted">Название и срок — статус всегда «Ждёт выполнения».</p>
          </div>

          <div className="field">
            <label>Название</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Например, Сверстать главную"
              required
              autoFocus
            />
          </div>

          <div className="field">
            <label>Описание</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Кратко, что нужно сделать"
              rows={3}
            />
          </div>

          <div className="field">
            <label>Срок</label>
            <DueDatePicker
              value={dueDate}
              onChange={setDueDate}
              status="todo"
              timeZone={dueTz}
            />
          </div>

          <button className="btn btn-accent" disabled={busy} style={{ alignSelf: "start" }}>
            {busy ? "Создаём…" : "Создать задачу"}
          </button>
        </form>
      )}

      <div className="task-toolbar">
        <label className="task-search">
          <span className="task-search-icon" aria-hidden>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
              <path
                d="M20 20l-3.5-3.5"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Поиск по названию или описанию"
            aria-label="Поиск задач"
          />
          {query && (
            <button
              type="button"
              className="task-search-clear"
              onClick={() => setQuery("")}
              aria-label="Очистить поиск"
            >
              ×
            </button>
          )}
        </label>
        <div className="task-filters" role="tablist" aria-label="Фильтр по статусу">
          {filters.map((f) => (
            <button
              key={f.id}
              type="button"
              role="tab"
              aria-selected={filter === f.id}
              className={`task-filter-chip${filter === f.id ? " active" : ""}${
                f.id !== "all" ? ` ${STATUS_TONE[f.id]}` : ""
              }`}
              onClick={() => {
                if (f.id === filter) return;
                setTasks([]);
                setHasMore(false);
                setInitialLoading(true);
                setFilter(f.id);
              }}
            >
              <span>{f.label}</span>
              <span className="task-filter-count">{f.count}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="task-list">
        {initialLoading && visible.length === 0 ? (
          <div className="empty-linked task-empty data-loading-state">
            <span className="data-loading-spinner" aria-hidden />
            <p className="muted">Загрузка задач…</p>
          </div>
        ) : visible.length === 0 ? (
          <div className="empty-linked task-empty">
            <p className="muted">
              {counts.all === 0
                ? "Создайте первую задачу — кнопка «Новая задача» сверху."
                : debouncedQuery.trim()
                  ? "Ничего не найдено по запросу."
                  : "В этом статусе задач нет."}
            </p>
          </div>
        ) : (
          visible.map((t) => {
            const due = dueMeta(t.due_date, t.status);
            return (
              <Link
                key={t.id}
                to={`/tasks/${t.id}`}
                className={`task-card${t.status === "done" ? " is-done" : ""}${t.is_important ? " is-important" : ""}${enteringId === t.id ? " is-entering" : ""}`}
              >
                <div className="task-card-main">
                  <div className="task-card-top">
                    {t.is_important ? (
                      <span className="task-important-pill" title="Важная задача">
                        <FlameIcon filled size={14} />
                        Важно
                      </span>
                    ) : null}
                    <span className={`task-status-pill ${STATUS_TONE[t.status]}`}>
                      {STATUS_LABEL[t.status]}
                    </span>
                    {t.is_working ? (
                      <span className="task-working-pill" title={t.working_by_name || undefined}>
                        Сейчас в работе
                      </span>
                    ) : null}
                    {isTaskOverdue(t.due_date, t.status) ? (
                      <span className="task-status-pill status-overdue">Опаздывает</span>
                    ) : null}
                    {typeof t.comments_count === "number" && t.comments_count > 0 && (
                      <span className="task-comments muted">{t.comments_count} комм.</span>
                    )}
                    {isAgency && t.can_delete ? (
                      <button
                        type="button"
                        className="btn btn-ghost task-card-delete"
                        disabled={deletingId === t.id}
                        title="Удалить черновую задачу"
                        onClick={(e) => void deleteTask(t, e)}
                      >
                        {deletingId === t.id ? "Удаляем…" : "Удалить"}
                      </button>
                    ) : null}
                  </div>
                  <strong
                    className={`task-card-title${t.status === "done" ? " is-struck" : ""}`}
                  >
                    {t.title}
                  </strong>
                  {t.description ? (
                    <span className="task-card-desc muted">{t.description}</span>
                  ) : null}
                </div>
                <div className={`task-due ${due.tone}`}>
                  <span className="task-due-icon" aria-hidden>
                    <CalendarGlyph />
                  </span>
                  <span className="task-due-body">
                    {due.detail ? (
                      <>
                        <span className="task-due-date">{due.detail}</span>
                        <span className="task-due-label">{due.label}</span>
                      </>
                    ) : (
                      <span className="task-due-date">{due.label}</span>
                    )}
                  </span>
                </div>
              </Link>
            );
          })
        )}

        {hasMore ? (
          <div ref={sentinelRef} className="task-list-sentinel muted">
            {loadingMore ? "Загрузка…" : ""}
          </div>
        ) : null}
      </div>
    </div>
  );
}
