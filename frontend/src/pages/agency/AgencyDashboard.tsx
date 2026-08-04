import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  isAbortError,
  type Paginated,
  type Project,
  type Task,
  type TaskStatus,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { formatDueFull } from "../../lib/format";
import { isTaskOverdue, STATUS_LABEL } from "../../lib/status";

type StatusFilter = "all" | TaskStatus;

type ProjectBucket = {
  project: Project;
  tasks: Task[];
};

type ClientBucket = {
  portalId: number;
  portalName: string;
  projects: ProjectBucket[];
  openCount: number;
};

async function fetchAllPages<T>(
  path: string,
  token: string,
  signal?: AbortSignal
): Promise<T[]> {
  const out: T[] = [];
  for (let page = 1; page <= 30; page++) {
    const sep = path.includes("?") ? "&" : "?";
    const data = await api<Paginated<T> | T[]>(
      `${path}${sep}page=${page}`,
      { signal },
      token
    );
    if (Array.isArray(data)) {
      return data;
    }
    const batch = data.results || [];
    out.push(...batch);
    if (!data.next || batch.length === 0) break;
  }
  return out;
}

export function AgencyDashboard() {
  const { token } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [query, setQuery] = useState("");

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!token) return;
      setLoading(true);
      setError(null);
      try {
        const [projectList, taskList] = await Promise.all([
          fetchAllPages<Project>("/api/projects/?is_active=true", token, signal),
          fetchAllPages<Task>("/api/tasks/?open=1", token, signal),
        ]);
        if (signal?.aborted) return;
        setProjects(projectList);
        setTasks(taskList);
      } catch (e) {
        if (isAbortError(e)) return;
        setError(e instanceof Error ? e.message : "Не удалось загрузить дашборд");
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [token]
  );

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);
    return () => ac.abort();
  }, [load]);

  useEffect(() => {
    if (!token) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    const onUpdate = () => void load();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("projects-updated", onUpdate);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("projects-updated", onUpdate);
    };
  }, [token, load]);

  const filteredTasks = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tasks.filter((t) => {
      if (statusFilter !== "all" && t.status !== statusFilter) return false;
      if (!q) return true;
      return (
        t.title.toLowerCase().includes(q) ||
        (t.project_name || "").toLowerCase().includes(q) ||
        (t.created_by_name || "").toLowerCase().includes(q)
      );
    });
  }, [tasks, statusFilter, query]);

  const buckets = useMemo(() => {
    const projectById = new Map(projects.map((p) => [p.id, p]));
    const byPortal = new Map<number, ClientBucket>();

    for (const task of filteredTasks) {
      const project =
        projectById.get(task.project) ||
        ({
          id: task.project,
          portal: task.portal_id,
          portal_name: "",
          name: task.project_name || `Проект #${task.project}`,
          description: "",
          is_active: true,
          tasks_count: 0,
          done_count: 0,
        } satisfies Project);

      const portalId = task.portal_id || project.portal;
      let client = byPortal.get(portalId);
      if (!client) {
        client = {
          portalId,
          portalName: project.portal_name || `Клиент #${portalId}`,
          projects: [],
          openCount: 0,
        };
        byPortal.set(portalId, client);
      } else if (project.portal_name && !client.portalName.includes(project.portal_name)) {
        client.portalName = project.portal_name;
      }

      let bucket = client.projects.find((b) => b.project.id === project.id);
      if (!bucket) {
        bucket = { project, tasks: [] };
        client.projects.push(bucket);
      }
      bucket.tasks.push(task);
      client.openCount += 1;
    }

    // Projects with zero matching tasks still appear if no search/filter — skip empties.
    const list = Array.from(byPortal.values());
    for (const client of list) {
      client.projects.sort((a, b) => a.project.name.localeCompare(b.project.name, "ru"));
    }
    list.sort((a, b) => a.portalName.localeCompare(b.portalName, "ru"));
    return list;
  }, [filteredTasks, projects]);

  const totalOpen = filteredTasks.length;
  const clientCount = buckets.length;
  const projectCount = buckets.reduce((n, c) => n + c.projects.length, 0);

  const filters: { id: StatusFilter; label: string }[] = [
    { id: "all", label: "Все открытые" },
    { id: "todo", label: STATUS_LABEL.todo },
    { id: "in_progress", label: STATUS_LABEL.in_progress },
  ];

  return (
    <div className="workspace-page agency-dashboard">
      <div className="page-header">
        <div>
          <h1 className="page-title">Дашборд</h1>
          <p className="page-sub">
            Все проекты и открытые задачи по клиентам
            {!loading
              ? ` · ${clientCount} клиентов · ${projectCount} проектов · ${totalOpen} задач`
              : ""}
          </p>
        </div>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={loading}
          onClick={() => void load()}
        >
          Обновить
        </button>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      <div className="dashboard-toolbar">
        <div className="task-filters" role="tablist" aria-label="Фильтр по статусу">
          {filters.map((f) => (
            <button
              key={f.id}
              type="button"
              role="tab"
              aria-selected={statusFilter === f.id}
              className={`task-filter${statusFilter === f.id ? " active" : ""}`}
              onClick={() => setStatusFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <label className="task-search dashboard-search">
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
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Поиск по задаче, проекту, постановщику"
            aria-label="Поиск"
          />
        </label>
      </div>

      {loading && buckets.length === 0 ? (
        <div className="empty-linked workspace-empty data-loading-state">
          <span className="data-loading-spinner" aria-hidden />
          <p className="muted">Собираем задачи по клиентам…</p>
        </div>
      ) : buckets.length === 0 ? (
        <div className="empty-linked workspace-empty">
          <p className="muted">
            {tasks.length === 0
              ? "Открытых задач пока нет."
              : "Нет задач по выбранному фильтру."}
          </p>
        </div>
      ) : (
        <div className="dashboard-clients">
          {buckets.map((client) => (
            <section key={client.portalId} className="dashboard-client">
              <header className="dashboard-client-head">
                <div>
                  <h2 className="section-title">
                    <Link to={`/portals/${client.portalId}`} className="dashboard-client-link">
                      {client.portalName}
                    </Link>
                  </h2>
                  <p className="muted">
                    {client.projects.length}{" "}
                    {client.projects.length === 1 ? "проект" : "проектов"} · {client.openCount}{" "}
                    открытых
                  </p>
                </div>
                <Link to={`/portals/${client.portalId}/projects`} className="btn btn-ghost">
                  Все проекты
                </Link>
              </header>

              <div className="dashboard-projects">
                {client.projects.map(({ project, tasks: projectTasks }) => (
                  <div key={project.id} className="dashboard-project">
                    <div className="dashboard-project-head">
                      <Link to={`/projects/${project.id}`} className="dashboard-project-title">
                        {project.name}
                      </Link>
                      <span className="muted">
                        {projectTasks.length}{" "}
                        {projectTasks.length === 1 ? "задача" : "задач"}
                      </span>
                    </div>
                    <ul className="dashboard-task-list">
                      {projectTasks.map((task) => {
                        const overdue = isTaskOverdue(task.due_date, task.status);
                        return (
                          <li key={task.id}>
                            <Link to={`/tasks/${task.id}`} className="dashboard-task-row">
                              <span
                                className={`dashboard-task-status status-${task.status}${
                                  overdue ? " is-overdue" : ""
                                }`}
                              >
                                {overdue ? "Просрочена" : STATUS_LABEL[task.status]}
                              </span>
                              <span className="dashboard-task-title">
                                {task.is_important ? (
                                  <span className="dashboard-task-flame" title="Важная" aria-hidden>
                                    ★
                                  </span>
                                ) : null}
                                {task.title}
                              </span>
                              <span className="dashboard-task-meta muted">
                                {task.due_date ? formatDueFull(task.due_date) : "Без срока"}
                                {task.created_by_name
                                  ? ` · ${task.created_by_name}`
                                  : ""}
                              </span>
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
