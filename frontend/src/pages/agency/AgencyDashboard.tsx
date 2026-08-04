import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  isAbortError,
  type Paginated,
  type Project,
  type Task,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlameIcon } from "../../components/icons";
import { isValidDate, parseDue, startOfDay } from "../../lib/dates";
import { formatDueFull } from "../../lib/format";
import { isTaskOverdue, STATUS_LABEL } from "../../lib/status";

const HOT_DUE_DAYS = 2;

type ClientFilter = "all" | number;

function isDueSoon(dueDate: string | null | undefined, status: Task["status"]): boolean {
  if (!dueDate || status === "done") return false;
  if (isTaskOverdue(dueDate, status)) return false;
  const target = parseDue(dueDate);
  if (!isValidDate(target)) return false;
  const today = startOfDay(new Date());
  const targetDay = startOfDay(target);
  const days = Math.round((targetDay.getTime() - today.getTime()) / 86400000);
  return days >= 0 && days <= HOT_DUE_DAYS;
}

function hotPriority(task: Task): number {
  if (isTaskOverdue(task.due_date, task.status)) return 0;
  if (isDueSoon(task.due_date, task.status)) return 1;
  if (task.is_important) return 2;
  if (task.status === "in_progress") return 3;
  return 4;
}

function portalLabel(task: Task, projects: Project[]): string {
  const project = projects.find((p) => p.id === task.project);
  return project?.portal_name || `Клиент #${task.portal_id}`;
}

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
    if (Array.isArray(data)) return data;
    const batch = data.results || [];
    out.push(...batch);
    if (!data.next || batch.length === 0) break;
  }
  return out;
}

function TaskCard({
  task,
  clientName,
}: {
  task: Task;
  clientName: string;
}) {
  const overdue = isTaskOverdue(task.due_date, task.status);
  const soon = isDueSoon(task.due_date, task.status);
  const due = task.due_date ? formatDueFull(task.due_date) : null;

  return (
    <Link
      to={`/tasks/${task.id}`}
      className={`workspace-attention-card${
        overdue ? " is-overdue" : soon ? " is-soon" : ""
      }`}
    >
      <div className="workspace-attention-top">
        <span className="workspace-chip tone-client">{clientName}</span>
        {overdue ? <span className="workspace-chip tone-overdue">Просрочена</span> : null}
        {soon ? <span className="workspace-chip tone-soon">Скоро срок</span> : null}
        {task.is_important ? (
          <span className="task-important-pill" title="Важная задача">
            <FlameIcon filled size={14} />
            Важно
          </span>
        ) : null}
      </div>
      <strong>{task.title}</strong>
      <span className="muted">
        {task.project_name}
        {" · "}
        {STATUS_LABEL[task.status]}
        {due ? ` · до ${due}` : " · без срока"}
      </span>
    </Link>
  );
}

export function AgencyDashboard() {
  const { token } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [clientFilter, setClientFilter] = useState<ClientFilter>("all");

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

  const clients = useMemo(() => {
    const map = new Map<number, string>();
    for (const p of projects) {
      if (!map.has(p.portal)) {
        map.set(p.portal, p.portal_name || `Клиент #${p.portal}`);
      }
    }
    for (const t of tasks) {
      if (!map.has(t.portal_id)) {
        map.set(t.portal_id, portalLabel(t, projects));
      }
    }
    return Array.from(map.entries())
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name, "ru"));
  }, [projects, tasks]);

  const scopedTasks = useMemo(() => {
    if (clientFilter === "all") return tasks;
    return tasks.filter((t) => t.portal_id === clientFilter);
  }, [tasks, clientFilter]);

  const overdue = useMemo(
    () =>
      scopedTasks
        .filter((t) => isTaskOverdue(t.due_date, t.status))
        .sort((a, b) => hotPriority(a) - hotPriority(b)),
    [scopedTasks]
  );

  const dueSoon = useMemo(
    () =>
      scopedTasks
        .filter((t) => isDueSoon(t.due_date, t.status))
        .sort((a, b) => {
          const da = a.due_date ? parseDue(a.due_date).getTime() : 0;
          const db = b.due_date ? parseDue(b.due_date).getTime() : 0;
          return da - db;
        }),
    [scopedTasks]
  );

  const important = useMemo(
    () =>
      scopedTasks
        .filter(
          (t) =>
            t.is_important &&
            !isTaskOverdue(t.due_date, t.status) &&
            !isDueSoon(t.due_date, t.status)
        )
        .sort((a, b) => a.title.localeCompare(b.title, "ru")),
    [scopedTasks]
  );

  const inProgress = useMemo(
    () =>
      scopedTasks
        .filter(
          (t) =>
            t.status === "in_progress" &&
            !isTaskOverdue(t.due_date, t.status) &&
            !isDueSoon(t.due_date, t.status) &&
            !t.is_important
        )
        .sort((a, b) => a.title.localeCompare(b.title, "ru")),
    [scopedTasks]
  );

  const hotCount = overdue.length + dueSoon.length + important.length;
  const calm = !loading && hotCount === 0 && inProgress.length === 0;

  return (
    <div className="workspace-page agency-dashboard" data-tour="tour-agency-dashboard">
      <div className="page-header">
        <div>
          <h1 className="page-title">Рабочее пространство</h1>
          <p className="page-sub">
            Горящие задачи со всех клиентов — без переключения по порталам
            {!loading
              ? ` · ${overdue.length} просрочено · ${dueSoon.length} скоро · ${important.length} важных`
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

      {clients.length > 1 ? (
        <div className="dashboard-toolbar">
          <div className="task-filters" role="tablist" aria-label="Фильтр по клиенту">
            <button
              type="button"
              role="tab"
              aria-selected={clientFilter === "all"}
              className={`task-filter${clientFilter === "all" ? " active" : ""}`}
              onClick={() => setClientFilter("all")}
            >
              Все клиенты
            </button>
            {clients.map((c) => (
              <button
                key={c.id}
                type="button"
                role="tab"
                aria-selected={clientFilter === c.id}
                className={`task-filter${clientFilter === c.id ? " active" : ""}`}
                onClick={() => setClientFilter(c.id)}
              >
                {c.name}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {loading && tasks.length === 0 ? (
        <div className="empty-linked workspace-empty data-loading-state">
          <span className="data-loading-spinner" aria-hidden />
          <p className="muted">Собираем задачи по клиентам…</p>
        </div>
      ) : calm ? (
        <div className="empty-linked workspace-empty">
          <p className="muted">
            Сейчас нет просроченных, срочных и важных задач
            {clientFilter === "all" ? " по клиентам" : ""}. Можно спокойно открыть нужный
            портал слева.
          </p>
        </div>
      ) : (
        <div className="workspace-focus dashboard-focus">
          {overdue.length > 0 ? (
            <section className="workspace-focus-block">
              <div className="linked-head">
                <h2 className="section-title dashboard-section-title is-overdue">
                  Просрочены
                  <span className="dashboard-section-count">{overdue.length}</span>
                </h2>
                <p className="muted">Срок уже прошёл — взять в работу в первую очередь</p>
              </div>
              <div className="workspace-attention-list dashboard-attention-grid">
                {overdue.map((t) => (
                  <TaskCard key={t.id} task={t} clientName={portalLabel(t, projects)} />
                ))}
              </div>
            </section>
          ) : null}

          {dueSoon.length > 0 ? (
            <section className="workspace-focus-block">
              <div className="linked-head">
                <h2
                  className={`workspace-hot-heading${
                    dueSoon.length > 0 ? " is-shaking" : " is-calm"
                  }`}
                >
                  <span className="workspace-hot-pill">
                    <FlameIcon filled size={14} />
                    <span className="workspace-hot-label">Скоро срок</span>
                  </span>
                  <span className="dashboard-section-count">{dueSoon.length}</span>
                </h2>
                <p className="muted">Срок сегодня или в ближайшие 1–2 дня</p>
              </div>
              <div className="workspace-attention-list dashboard-attention-grid">
                {dueSoon.map((t) => (
                  <TaskCard key={t.id} task={t} clientName={portalLabel(t, projects)} />
                ))}
              </div>
            </section>
          ) : null}

          {important.length > 0 ? (
            <section className="workspace-focus-block">
              <div className="linked-head">
                <h2 className="section-title dashboard-section-title">
                  Важные
                  <span className="dashboard-section-count">{important.length}</span>
                </h2>
                <p className="muted">Помечены важными, без ближайшего дедлайна</p>
              </div>
              <div className="workspace-attention-list dashboard-attention-grid">
                {important.map((t) => (
                  <TaskCard key={t.id} task={t} clientName={portalLabel(t, projects)} />
                ))}
              </div>
            </section>
          ) : null}

          {inProgress.length > 0 ? (
            <section className="workspace-focus-block">
              <div className="linked-head">
                <h2 className="section-title dashboard-section-title">
                  Уже в работе
                  <span className="dashboard-section-count">{inProgress.length}</span>
                </h2>
                <p className="muted">Открыты в статусе «Выполняется» по всем клиентам</p>
              </div>
              <div className="workspace-attention-list dashboard-attention-grid">
                {inProgress.map((t) => (
                  <TaskCard key={t.id} task={t} clientName={portalLabel(t, projects)} />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      )}
    </div>
  );
}
