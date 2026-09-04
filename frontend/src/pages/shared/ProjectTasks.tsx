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
import { dueMeta, formatRuDateTime, formatRuDateTimeOrDash } from "../../lib/dates";
import {
  readPortalCache,
  readBoardTasksCache,
  writePortalCache,
  writeBoardTasksCache,
} from "../../lib/portalSessionCache";
import { STATUS_LABEL, STATUS_TONE } from "../../lib/status";
import { BoardAvatar } from "../../components/BoardAvatar";
import { FlameIcon } from "../../components/icons";
import { formatDuration } from "../../lib/format";
import { BoardDoneSplit } from "../../components/BoardDoneSplit";
import { PaginationBar } from "../../components/PaginationBar";
import { LIST_PAGE_SIZE, pageTotal } from "../../lib/pagination";
import { displayTimeZone } from "../../lib/timezone";
import { SyncHint } from "../../components/SyncHint";

function dueHint(due: ReturnType<typeof dueMeta>): string {
  if (due.tone === "due-overdue") return "Срок истёк";
  return due.label;
}

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
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [initialLoading, setInitialLoading] = useState(true);
  const [bitrixSyncing, setBitrixSyncing] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const genRef = useRef(0);
  const pageRef = useRef(1);
  pageRef.current = page;

  const visible = tasks;

  function buildListUrl(pageNum: number, withPull: boolean): string {
    let url = `/api/tasks/?project=${projectId}&page=${pageNum}&page_size=${LIST_PAGE_SIZE}`;
    if (filter !== "all") url += `&status=${filter}`;
    const q = debouncedQuery.trim();
    if (q) url += `&search=${encodeURIComponent(q)}`;
    if (withPull) url += "&pull=1";
    return url;
  }

  function fetchPage(pageNum: number, withPull: boolean, signal?: AbortSignal) {
    return api<Paginated<Task>>(buildListUrl(pageNum, withPull), { signal }, token!);
  }

  function applyPage(data: Paginated<Task>, pageNum: number) {
    const list = data.results || [];
    const count = pageTotal(data);
    setTasks(list);
    setTotal(count);
    setPage(pageNum);
    const parts = cacheKeyParts();
    if (parts) {
      writeBoardTasksCache(parts[0], parts[1], parts[2], pageNum, {
        tasks: list,
        count,
        page: pageNum,
      });
    }
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

  async function loadPage(pageNum: number, withPull: boolean, signal?: AbortSignal) {
    if (!token || !projectId) return;
    const gen = genRef.current;
    const [projectData, taskData] = await Promise.all([
      api<Project>(`/api/projects/${projectId}/`, { signal }, token),
      fetchPage(pageNum, false, signal),
    ]);
    if (gen !== genRef.current || signal?.aborted) return;
    setProject(projectData);
    writePortalCache("project-meta", Number(projectId), projectData);
    applyPage(taskData, pageNum);
    void loadCounts();

    if (!withPull || pageNum !== 1) return;
    setBitrixSyncing(true);
    void fetchPage(1, true, signal)
      .then((pulled) => {
        if (gen !== genRef.current || signal?.aborted) return;
        applyPage(pulled, 1);
        void loadCounts();
      })
      .catch((e) => {
        if (!isAbortError(e)) undefined;
      })
      .finally(() => {
        if (gen === genRef.current) setBitrixSyncing(false);
      });
  }

  useEffect(() => {
    const id = window.setTimeout(() => {
      setDebouncedQuery(query);
      setPage(1);
    }, 300);
    return () => window.clearTimeout(id);
  }, [query]);

  useEffect(() => {
    const id = Number(projectId);
    if (!id || !project) return;
    markSeen(id);
  }, [projectId, project, markSeen]);

  useEffect(() => {
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
    setPage(1);
  }, [projectId]);

  useEffect(() => {
    if (!token || !projectId) return;
    genRef.current += 1;
    const gen = genRef.current;
    setBitrixSyncing(false);
    const ac = new AbortController();
    const parts = cacheKeyParts();
    const cached = parts
      ? readBoardTasksCache(parts[0], parts[1], parts[2], page)
      : null;
    if (cached) {
      setTasks(cached.tasks as Task[]);
      setTotal(cached.count || 0);
      setInitialLoading(false);
    } else {
      setInitialLoading(true);
      setTasks([]);
      setTotal(0);
    }
    void loadPage(page, page === 1, ac.signal)
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
  }, [token, projectId, filter, debouncedQuery, page]);

  const pullNowRef = useRef(false);
  usePortalLiveSync({
    token,
    portalId: project?.portal ?? null,
    enabled: !!projectId,
    onEvent: () => {
      pullNowRef.current = false;
    },
  });

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
        const currentPage = pageRef.current;
        const wantPull = currentPage === 1 && (pullNowRef.current || tickCount % 24 === 0);
        pullNowRef.current = false;
        const data = await fetchPage(currentPage, wantPull, signal);
        if (cancelled || signal.aborted) return;
        applyPage(data, currentPage);
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
      setPage(1);
      await loadPage(1, true);
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
      toast.show("Задача удалена");
      window.dispatchEvent(new Event("projects-updated"));
      if (tasks.length <= 1 && page > 1) setPage(page - 1);
      else await loadPage(page, false);
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
            {project?.due_date
              ? ` · срок ${formatRuDateTime(project.due_date, dueTz)}`
              : ""}
            {project && (project.total_tracked_seconds || 0) > 0
              ? ` · учёт ${formatDuration(project.total_tracked_seconds || 0)}`
              : ""}
            {bitrixSyncing ? <SyncHint>Обновляем статусы…</SyncHint> : null}
          </p>
        </div>
        <div className="report-header-actions">
          {isAgency ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setShowCreate((v) => !v)}
              data-tour="tour-new-task"
            >
              {showCreate ? "Закрыть" : "Новая задача"}
            </button>
          ) : (
            <Link to="/requests" className="btn btn-primary">
              На согласование
            </Link>
          )}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      {showCreate && isAgency && (
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
                setPage(1);
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
                ? isAgency
                  ? "Создайте первую задачу — кнопка «Новая задача» сверху."
                  : "Задачи появятся здесь, когда агентство примет заявку в работу."
                : debouncedQuery.trim()
                  ? "Ничего не найдено по запросу."
                  : "В этом статусе задач нет."}
            </p>
          </div>
        ) : (
          <BoardDoneSplit
            items={visible}
            split={filter === "all"}
            isDone={(t) => t.status === "done"}
            doneLabel="Завершённые задачи"
            as="div"
            className="task-list-group"
            renderItem={(t) => {
              const due = dueMeta(t.due_date, t.status, dueTz);
              const person = t.working_by_name || t.created_by_name || "";
              const personLabel = t.working_by_name ? "Исполнитель" : "Автор";
              const tracked = t.total_tracked_seconds || 0;
              return (
                <Link
                  key={t.id}
                  to={`/tasks/${t.id}`}
                  className={`board-row task-card${t.status === "done" ? " is-done" : ""}${t.is_important ? " is-important" : ""}${enteringId === t.id ? " is-entering" : ""}`}
                >
                  <div className="board-row-main">
                    <div className="board-row-chips">
                      <span className={`task-status-pill ${STATUS_TONE[t.status]}`}>
                        {STATUS_LABEL[t.status]}
                      </span>
                      {t.is_important ? (
                        <span className="task-important-pill" title="Важная задача">
                          <FlameIcon filled size={14} />
                          Важно
                        </span>
                      ) : null}
                      {t.is_working ? (
                        <span className="task-working-pill" title={t.working_by_name || undefined}>
                          Сейчас в работе
                        </span>
                      ) : null}
                      {t.awaiting_client ? (
                        <span className="task-awaiting-pill">Ожидает ответа</span>
                      ) : null}
                    </div>
                    <strong
                      className={`board-row-title task-card-title${t.status === "done" ? " is-struck" : ""}`}
                    >
                      {t.title}
                    </strong>
                    {t.description ? (
                      <span className="board-row-desc task-card-desc muted">{t.description}</span>
                    ) : null}
                    {typeof t.comments_count === "number" && t.comments_count > 0 ? (
                      <span className="board-row-note muted">{t.comments_count} комм.</span>
                    ) : null}
                  </div>
                  <div className="board-row-meta is-task">
                    <div className="board-meta">
                      <span className="board-meta-label">{personLabel}</span>
                      {person ? (
                        <span className="board-meta-person">
                          <BoardAvatar name={person} />
                          <span className="board-meta-text">{person}</span>
                        </span>
                      ) : (
                        <span className="board-meta-empty">—</span>
                      )}
                    </div>
                    <div className="board-meta">
                      <span className="board-meta-label">Срок</span>
                      <span className={`board-meta-due ${due.tone}`}>
                        <strong>{due.detail || "Без срока"}</strong>
                        {t.status !== "done" && due.detail ? <small>{dueHint(due)}</small> : null}
                      </span>
                    </div>
                    <div className="board-meta">
                      <span className="board-meta-label">Учёт</span>
                      <span className="board-meta-time">{formatDuration(tracked)}</span>
                    </div>
                    <div className="board-meta">
                      <span className="board-meta-label">Реализовали</span>
                      <span className="board-meta-due">
                        <strong>{formatRuDateTimeOrDash(t.completed_at, dueTz)}</strong>
                      </span>
                    </div>
                  </div>
                  {isAgency && t.can_delete ? (
                    <button
                      type="button"
                      className="btn btn-ghost board-row-delete"
                      disabled={deletingId === t.id}
                      title="Удалить черновую задачу"
                      onClick={(e) => void deleteTask(t, e)}
                    >
                      {deletingId === t.id ? "Удаляем…" : "Удалить"}
                    </button>
                  ) : null}
                </Link>
              );
            }}
          />
        )}
        <PaginationBar
          page={page}
          total={total}
          disabled={initialLoading}
          onChange={(next) => {
            setPage(next);
            window.scrollTo({ top: 0, behavior: "smooth" });
          }}
        />
      </div>
    </div>
  );
}
