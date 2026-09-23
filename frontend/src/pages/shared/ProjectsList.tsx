import { useCallback, useEffect, useMemo, useState, type MouseEvent } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import {
  api,
  isAbortError,
  unwrapList,
  type Paginated,
  type Project,
  type ProjectCounts,
  type Task,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { BoardDoneSplit } from "../../components/BoardDoneSplit";
import { PaginationBar } from "../../components/PaginationBar";
import { ProjectsGantt } from "../../components/ProjectsGantt";
import { StatusFilterMenu } from "../../components/StatusFilterMenu";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { useSeenProjects } from "../../hooks/useSeenProjects";
import { dueMeta } from "../../lib/dates";
import { formatDuration } from "../../lib/format";
import { LIST_PAGE_SIZE, PICKER_PAGE_SIZE, pageTotal, withPage } from "../../lib/pagination";
import { displayTimeZone } from "../../lib/timezone";
import { portalDisplayName, setPortalLabel } from "../../lib/portalLabelCache";
import {
  CACHE_PROJECTS,
  readPortalCache,
  writePortalCache,
} from "../../lib/portalSessionCache";
import { isProjectComplete, projectProgress } from "../../lib/projectProgress";
import { linkStateFrom } from "../../lib/smartBack";
import { STATUS_LABEL, STATUS_TONE } from "../../lib/status";

type ProjectsView = "list" | "gantt";

const VIEW_STORAGE_KEY = "nextgen-projects-view";

function readProjectsView(): ProjectsView {
  try {
    const stored = window.localStorage.getItem(VIEW_STORAGE_KEY);
    return stored === "gantt" || stored === "board" ? "gantt" : "list";
  } catch {
    return "list";
  }
}

function writeProjectsView(view: ProjectsView) {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, view);
  } catch {
    /* ignore quota */
  }
}

function ListViewIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M8 7h12M8 12h12M8 17h12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <circle cx="4.5" cy="7" r="1.2" fill="currentColor" />
      <circle cx="4.5" cy="12" r="1.2" fill="currentColor" />
      <circle cx="4.5" cy="17" r="1.2" fill="currentColor" />
    </svg>
  );
}

function GanttViewIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" strokeWidth="1.5" opacity="0.35" />
      <rect x="7" y="4.3" width="8" height="3.4" rx="1.2" fill="currentColor" />
      <rect x="10" y="10.3" width="10" height="3.4" rx="1.2" fill="currentColor" />
      <rect x="5" y="16.3" width="7" height="3.4" rx="1.2" fill="currentColor" />
    </svg>
  );
}

export function ProjectsList() {
  const { portalId: routePortalId } = useParams();
  const location = useLocation();
  const fromState = linkStateFrom(location);
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const dueTz = displayTimeZone({
    role: portal?.role,
    portalTimezone: portal?.timezone,
  });
  const toast = useFlashToast();

  const portalId = useMemo(() => {
    if (routePortalId) return Number(routePortalId);
    if (!isAgency && portal?.id) return portal.id;
    return null;
  }, [routePortalId, isAgency, portal?.id]);

  const [projects, setProjects] = useState<Project[]>([]);
  const [counts, setCounts] = useState<ProjectCounts>({ all: 0, open: 0, done: 0 });
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [enteringId, setEnteringId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [filter, setFilter] = useState<"all" | "open" | "done">("all");
  const [view, setView] = useState<ProjectsView>(readProjectsView);
  const [expandedProjectId, setExpandedProjectId] = useState<number | null>(null);
  const [inlineTasks, setInlineTasks] = useState<Record<number, Task[]>>({});
  const [inlineLoadingId, setInlineLoadingId] = useState<number | null>(null);
  const [inlineErrors, setInlineErrors] = useState<Record<number, string>>({});
  const { isUnseen, seedIfEmpty } = useSeenProjects(portalId);
  const pageSize = view === "gantt" ? PICKER_PAGE_SIZE : LIST_PAGE_SIZE;
  const projectsListPath = isAgency ? `/portals/${portalId}/projects` : "/projects";
  const allTasksPath = isAgency ? `/portals/${portalId}/tasks` : "/tasks";

  useEffect(() => {
    const id = window.setTimeout(() => {
      setDebouncedQuery(query);
      setPage(1);
    }, 300);
    return () => window.clearTimeout(id);
  }, [query]);

  const loadCounts = useCallback(
    async (signal?: AbortSignal) => {
      if (!token || !portalId) return;
      let url = `/api/projects/counts/?portal=${portalId}`;
      const q = debouncedQuery.trim();
      if (q) url += `&search=${encodeURIComponent(q)}`;
      const data = await api<ProjectCounts>(url, { signal }, token);
      if (signal?.aborted) return;
      setCounts(data);
    },
    [token, portalId, debouncedQuery]
  );

  const load = useCallback(async (signal?: AbortSignal, pageNum = 1) => {
    if (!token || !portalId) return;
    let url = `/api/projects/?portal=${portalId}`;
    if (filter === "open") url += "&complete=open";
    if (filter === "done") url += "&complete=done";
    const q = debouncedQuery.trim();
    if (q) url += `&search=${encodeURIComponent(q)}`;
    const data = await api<Paginated<Project>>(
      withPage(url, pageNum, pageSize),
      { signal },
      token
    );
    if (signal?.aborted) return;
    const list = (data.results || []).filter((project) => project.portal === portalId);
    setProjects(list);
    setTotal(pageTotal(data));
    setPage(pageNum);
    writePortalCache(CACHE_PROJECTS, portalId, list);
    setLoaded(true);
    seedIfEmpty(list.map((p) => p.id));
    void loadCounts(signal);
  }, [token, portalId, seedIfEmpty, filter, debouncedQuery, loadCounts, pageSize]);

  useEffect(() => {
    if (!portalId || isAgency || !portal) return;
    const label = portalDisplayName(portal);
    if (label) setPortalLabel(portalId, label);
  }, [portalId, isAgency, portal]);

  useEffect(() => {
    setPage(1);
  }, [portalId]);

  useEffect(() => {
    if (!token || !portalId) return;
    const cached = readPortalCache<Project[]>(CACHE_PROJECTS, portalId);
    const scoped =
      cached?.filter((project) => project.portal === portalId) || [];
    setProjects(scoped);
    setLoaded(cached !== null);
    setLoading(true);
    setError(null);
    const ac = new AbortController();
    void load(ac.signal, page).catch((e) => {
      if (!isAbortError(e)) setError(e instanceof Error ? e.message : "Ошибка");
    }).finally(() => {
      if (!ac.signal.aborted) setLoading(false);
    });
    return () => ac.abort();
  }, [token, portalId, load, page]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: !!portalId,
    onEvent: () => {
      void load(undefined, page).catch(() => undefined);
    },
  });

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !portalId || !isAgency) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api<Project>(
        "/api/projects/",
        {
          method: "POST",
          body: JSON.stringify({
            portal: portalId,
            name,
            description,
          }),
        },
        token
      );
      setName("");
      setDescription("");
      setShowCreate(false);
      setEnteringId(created.id);
      toast.show("Откройте его, чтобы добавить задачи", "Проект создан");
      setPage(1);
      await load(undefined, 1);
      window.dispatchEvent(new Event("projects-updated"));
      window.setTimeout(() => setEnteringId(null), 900);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать");
    } finally {
      setBusy(false);
    }
  }

  async function deleteProject(project: Project, e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!token || !project.can_delete || deletingId) return;
    if (!window.confirm(`Удалить пустой проект «${project.name}»?`)) return;
    setDeletingId(project.id);
    setError(null);
    try {
      await api(`/api/projects/${project.id}/`, { method: "DELETE" }, token);
      setProjects((prev) => prev.filter((p) => p.id !== project.id));
      toast.show("Проект удалён");
      window.dispatchEvent(new Event("projects-updated"));
      if (projects.length <= 1 && page > 1) setPage(page - 1);
      else void load(undefined, page);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить проект");
    } finally {
      setDeletingId(null);
    }
  }

  async function toggleProjectTasks(project: Project) {
    if (expandedProjectId === project.id) {
      setExpandedProjectId(null);
      return;
    }
    setExpandedProjectId(project.id);
    if (inlineTasks[project.id] || !token) return;
    setInlineLoadingId(project.id);
    setInlineErrors((prev) => ({ ...prev, [project.id]: "" }));
    try {
      const data = await api<Paginated<Task> | Task[]>(
        `/api/tasks/?project=${project.id}&page=1&page_size=100`,
        {},
        token
      );
      setInlineTasks((prev) => ({ ...prev, [project.id]: unwrapList(data) }));
    } catch (err) {
      setInlineErrors((prev) => ({
        ...prev,
        [project.id]: err instanceof Error ? err.message : "Не удалось загрузить задачи",
      }));
    } finally {
      setInlineLoadingId((current) => (current === project.id ? null : current));
    }
  }

  const visibleProjects = projects;

  function changeView(next: ProjectsView) {
    if (next === view) return;
    setView(next);
    writeProjectsView(next);
    setPage(1);
  }

  if (!portalId) {
    return (
      <div className="tasks-page">
        <p className="muted">Выберите клиента, чтобы открыть проекты.</p>
      </div>
    );
  }

  return (
    <div className="tasks-page">
      {isAgency ? (
        <div className="page-header">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setShowCreate((v) => !v)}
            data-tour="tour-new-project"
          >
            {showCreate ? "Закрыть" : "Новый проект"}
          </button>
        </div>
      ) : null}

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <div className="work-kind-switch" role="tablist" aria-label="Раздел работы">
        <Link
          to={allTasksPath}
          role="tab"
          aria-selected={false}
          className="work-kind-switch-item"
        >
          Задачи
        </Link>
        <Link
          to={projectsListPath}
          role="tab"
          aria-selected
          className="work-kind-switch-item is-active"
        >
          Проекты
        </Link>
      </div>

      {isAgency && showCreate ? (
        <form className="connect-panel create-project-panel stack" onSubmit={createProject}>
          <div>
            <h2 className="section-title">Новый проект</h2>
            <p className="muted">
              В Bitrix это задача внутри проекта компании; внутри — подзадачи.
            </p>
          </div>
          <div className="field">
            <label>Название</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Например, Интеграция оплаты"
              required
              autoFocus
            />
          </div>
          <div className="field">
            <label>Описание</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Кратко, что входит в модуль"
            />
          </div>
          <button className="btn btn-accent" disabled={busy} style={{ alignSelf: "start" }}>
            {busy ? "Создаём…" : "Создать проект"}
          </button>
        </form>
      ) : null}

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
            aria-label="Поиск проектов"
          />
          {query ? (
            <button
              type="button"
              className="task-search-clear"
              onClick={() => setQuery("")}
              aria-label="Очистить поиск"
            >
              ×
            </button>
          ) : null}
        </label>
        <StatusFilterMenu
          label="Статус проектов"
          value={filter}
          options={[
            { id: "all", label: "Все проекты", count: counts.all },
            { id: "open", label: "Активные", count: counts.open, tone: "status-progress" },
            { id: "done", label: "Завершённые", count: counts.done, tone: "status-done" },
          ] as const}
          onChange={(next) => {
            if (next === filter) return;
            setPage(1);
            setFilter(next);
          }}
        />
        <div className="projects-view-toggle" role="group" aria-label="Вид проектов">
          <button
            type="button"
            className={`projects-view-btn${view === "list" ? " is-active" : ""}`}
            aria-pressed={view === "list"}
            title="Список"
            onClick={() => changeView("list")}
          >
            <ListViewIcon />
            <span>Список</span>
          </button>
          <button
            type="button"
            className={`projects-view-btn${view === "gantt" ? " is-active" : ""}`}
            aria-pressed={view === "gantt"}
            title="Диаграмма Ганта"
            onClick={() => changeView("gantt")}
          >
            <GanttViewIcon />
            <span>Гант</span>
          </button>
        </div>
      </div>

      {loading && !loaded ? (
        <div className="empty-linked workspace-empty data-loading-state">
          <span className="data-loading-spinner" aria-hidden />
          <p className="muted">Загружаем проекты…</p>
        </div>
      ) : !loaded && error ? null : projects.length === 0 ? (
        <div className="empty-linked workspace-empty">
          <p className="muted">
            {query.trim()
              ? "Ничего не найдено по запросу."
              : filter !== "all"
                ? "В этом статусе проектов нет."
                : isAgency
                  ? "Пока нет проектов. Создайте первый модуль."
                  : "Пока нет проектов у этого кабинета."}
          </p>
        </div>
      ) : view === "gantt" ? (
        <ProjectsGantt projects={visibleProjects} timeZone={dueTz} linkState={fromState} />
      ) : (
        <BoardDoneSplit
          items={visibleProjects}
          split={filter === "all"}
          isDone={isProjectComplete}
          doneLabel="Завершённые проекты"
          showDoneHeading={false}
          renderItem={(p) => {
            const { done, total: taskTotal } = projectProgress(p);
            const progress = taskTotal > 0 ? Math.round((done / taskTotal) * 100) : 0;
            const unseen = isUnseen(p.id);
            const complete = isProjectComplete(p);
            const projectStatus = complete
              ? { label: "Завершён", tone: "status-done" }
              : p.has_active_work
                ? { label: "В работе", tone: "status-progress" }
                : { label: "Запланирован", tone: "status-todo" };
            const due = dueMeta(p.due_date, complete ? "done" : "todo", dueTz);
            const tracked = p.total_tracked_seconds || 0;
            const expanded = expandedProjectId === p.id;
            const projectTasks = inlineTasks[p.id] || [];
            return (
              <li key={p.id} className={`board-list-item project-list-item${expanded ? " is-expanded" : ""}`}>
                  <div
                  className={`board-row project-expand-row${enteringId === p.id ? " is-entering" : ""}${unseen ? " is-new" : ""}${complete ? " is-done" : ""}`}
                  role="button"
                  tabIndex={0}
                  aria-expanded={expanded}
                  onClick={() => void toggleProjectTasks(p)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      void toggleProjectTasks(p);
                    }
                  }}
                  >
                    <span className="project-row-mark" aria-hidden>
                      <span>{progress}</span>
                    </span>
                    <div className="board-row-main">
                      <div className="project-compact-heading">
                        <strong className="board-row-title">{p.name}</strong>
                      </div>
                      <span className="board-row-note muted">
                        {done} из {taskTotal} задач · {formatDuration(tracked)}
                      </span>
                    </div>
                    <div className="project-row-progress" aria-label={`Выполнено ${progress}%`}>
                      <span className="project-row-progress-copy"><span>Прогресс</span><strong>{progress}%</strong></span>
                      <span className="project-row-progress-track" aria-hidden><span style={{ width: `${progress}%` }} /></span>
                    </div>
                    <div className="project-compact-side">
                      <span className={`task-status-pill ${projectStatus.tone}`}>
                        {projectStatus.label}
                      </span>
                      <span className={`board-meta-due ${due.tone}`}>
                        <strong>{due.detail || "Без срока"}</strong>
                      </span>
                      <span className={`project-expand-chevron${expanded ? " is-open" : ""}`} aria-hidden>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                          <path d="M9 6l6 6-6 6" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </span>
                    </div>
                  {isAgency && p.can_delete ? (
                    <button
                      type="button"
                      className="btn btn-ghost board-row-delete"
                      disabled={deletingId === p.id}
                      onClick={(ev) => void deleteProject(p, ev)}
                      title="Удалить пустой проект"
                    >
                      {deletingId === p.id ? "Удаляем…" : "Удалить"}
                    </button>
                  ) : null}
                </div>
                {expanded ? (
                  <div className="project-inline-tasks">
                    {inlineLoadingId === p.id ? (
                      <div className="project-inline-tasks-empty">
                        <span className="data-loading-spinner" aria-hidden />
                        <span className="muted">Загружаем задачи…</span>
                      </div>
                    ) : inlineErrors[p.id] ? (
                      <p className="project-inline-tasks-empty muted">{inlineErrors[p.id]}</p>
                    ) : projectTasks.length === 0 ? (
                      <p className="project-inline-tasks-empty muted">Задач пока нет.</p>
                    ) : (
                      <div className="project-inline-tasks-list">
                        {projectTasks.map((task) => (
                          <Link
                            key={task.id}
                            to={`/tasks/${task.id}`}
                            state={fromState}
                            className="project-inline-task"
                          >
                            <strong>{task.title}</strong>
                            {task.status !== "todo" ? (
                              <span className={`task-status-pill ${STATUS_TONE[task.status]}`}>
                                {STATUS_LABEL[task.status]}
                              </span>
                            ) : null}
                          </Link>
                        ))}
                      </div>
                    )}
                  </div>
                ) : null}
              </li>
            );
          }}
        />
      )}
      <PaginationBar
        page={page}
        total={total}
        pageSize={pageSize}
        disabled={loading}
        onChange={setPage}
      />
    </div>
  );
}
