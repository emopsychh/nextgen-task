import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
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
import { dueMeta } from "../../lib/dates";
import { isTaskOverdue, STATUS_LABEL, STATUS_TONE } from "../../lib/status";
import { CalendarGlyph } from "../../components/icons";

export function ProjectTasks() {
  const { projectId } = useParams();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();

  const [project, setProject] = useState<Project | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [filter, setFilter] = useState<TaskStatus | "all">("all");
  const [query, setQuery] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [status, setStatus] = useState<TaskStatus>("todo");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [enteringId, setEnteringId] = useState<number | null>(null);
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [counts, setCounts] = useState<TaskCounts>({
    all: 0,
    todo: 0,
    in_progress: 0,
    done: 0,
  });
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
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

  function fetchPage(page: number, withPull: boolean) {
    return api<Paginated<Task>>(buildListUrl(page, withPull), {}, token!);
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

  async function loadCounts() {
    if (!token || !projectId) return;
    try {
      const c = await api<TaskCounts>(`/api/tasks/counts/?project=${projectId}`, {}, token);
      setCounts(c);
    } catch {
      // non-critical
    }
  }

  async function loadFirst() {
    if (!token || !projectId) return;
    const gen = genRef.current;
    const [projectData, taskData] = await Promise.all([
      api<Project>(`/api/projects/${projectId}/`, {}, token),
      fetchPage(1, true),
    ]);
    if (gen !== genRef.current) return;
    setProject(projectData);
    setTasks(taskData.results);
    setHasMore(Boolean(taskData.next));
    loadedPagesRef.current = 1;
    void loadCounts();
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

  // Reset & reload page 1 whenever project / filter / search change.
  useEffect(() => {
    if (!token || !projectId) return;
    genRef.current += 1;
    setInitialLoading(true);
    setTasks([]);
    setHasMore(false);
    loadedPagesRef.current = 1;
    void loadFirst()
      .catch((e) => setError(e instanceof Error ? e.message : "Ошибка"))
      .finally(() => setInitialLoading(false));
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
      pullNowRef.current = true;
    },
  });

  // Soft realtime: refresh only the pages already loaded (bounded by scroll);
  // Bitrix pull only on page 1 and only every ~12s (or on SSE).
  useEffect(() => {
    if (!token || !projectId) return;
    let cancelled = false;
    let inFlight = false;
    let tickCount = 0;

    async function tick() {
      if (cancelled || inFlight) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      inFlight = true;
      tickCount += 1;
      try {
        const wantPull = pullNowRef.current || tickCount % 5 === 0;
        pullNowRef.current = false;
        const pages = loadedPagesRef.current;
        const acc: Task[] = [];
        let lastNext: string | null = null;
        for (let p = 1; p <= pages; p++) {
          const data = await fetchPage(p, wantPull && p === 1);
          if (cancelled) return;
          acc.push(...data.results);
          if (p === pages) lastNext = data.next;
        }
        if (!cancelled) {
          setTasks(dedupeById(acc));
          setHasMore(Boolean(lastNext));
          void loadCounts();
        }
      } catch {
        // next tick retries
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
            status: isAgency ? status : "todo",
          }),
        },
        token
      );
      setTitle("");
      setDescription("");
      setDueDate("");
      setStatus("todo");
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
          </p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => setShowCreate((v) => !v)}
          data-tour="tour-new-task"
        >
          {showCreate ? "Закрыть" : "Новая задача"}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      {showCreate && (
        <form className="connect-panel create-task-panel stack" onSubmit={createTask}>
          <div>
            <h2 className="section-title">Новая задача</h2>
            <p className="muted">Название, статус и срок — остальное можно уточнить позже.</p>
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

          {isAgency && (
            <div className="field">
              <label>Статус</label>
              <div className="status-picker" role="group" aria-label="Статус задачи">
                {(["todo", "in_progress", "done"] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    className={`status-picker-btn ${STATUS_TONE[s]}${status === s ? " active" : ""}`}
                    onClick={() => setStatus(s)}
                  >
                    {STATUS_LABEL[s]}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="field">
            <label>Срок</label>
            <DueDatePicker value={dueDate} onChange={setDueDate} status={status} />
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
              onClick={() => setFilter(f.id)}
            >
              <span>{f.label}</span>
              <span className="task-filter-count">{f.count}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="task-list">
        {initialLoading && visible.length === 0 ? (
          <div className="empty-linked task-empty">
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
                      <span className="task-important-flag" title="Важная задача" aria-label="Важная задача">
                        <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden>
                          <path
                            d="M12 3.5l2.6 5.27 5.82.85-4.21 4.1.99 5.79L12 16.9l-5.2 2.73.99-5.79-4.21-4.1 5.82-.85L12 3.5z"
                            fill="currentColor"
                            stroke="currentColor"
                            strokeWidth="1.7"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </span>
                    ) : null}
                    <span className={`task-status-pill ${STATUS_TONE[t.status]}`}>
                      {STATUS_LABEL[t.status]}
                    </span>
                    {isTaskOverdue(t.due_date, t.status) ? (
                      <span className="task-status-pill status-overdue">Опаздывает</span>
                    ) : null}
                    {typeof t.comments_count === "number" && t.comments_count > 0 && (
                      <span className="task-comments muted">{t.comments_count} комм.</span>
                    )}
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
