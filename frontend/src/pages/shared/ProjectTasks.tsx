import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrapList, type Project, type Task, type TaskStatus } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { DueDatePicker } from "../../components/DueDatePicker";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { dueMeta } from "../../lib/dates";
import { isTaskOverdue, STATUS_LABEL, STATUS_TONE } from "../../lib/status";

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

  const counts = useMemo(() => {
    const base = { all: tasks.length, todo: 0, in_progress: 0, done: 0 };
    for (const t of tasks) base[t.status] += 1;
    return base;
  }, [tasks]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = tasks.filter((t) => {
      if (filter !== "all" && t.status !== filter) return false;
      if (!q) return true;
      return (
        t.title.toLowerCase().includes(q) ||
        (t.description || "").toLowerCase().includes(q)
      );
    });
    list.sort((a, b) => {
      if (a.status === "done" && b.status !== "done") return 1;
      if (b.status === "done" && a.status !== "done") return -1;
      if (!a.due_date && !b.due_date) return 0;
      if (!a.due_date) return 1;
      if (!b.due_date) return -1;
      return a.due_date.localeCompare(b.due_date);
    });
    return list;
  }, [tasks, filter, query]);

  async function load() {
    if (!token || !projectId) return;
    const [projectData, taskData] = await Promise.all([
      api<Project>(`/api/projects/${projectId}/`, {}, token),
      api<Task[] | { results: Task[] }>(
        `/api/tasks/?project=${projectId}`,
        {},
        token
      ),
    ]);
    setProject(projectData);
    setTasks(unwrapList(taskData));
  }

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [token, projectId]);

  const pullNowRef = useRef(false);
  usePortalLiveSync({
    token,
    portalId: project?.portal ?? null,
    enabled: !!projectId,
    onEvent: () => {
      pullNowRef.current = true;
    },
  });

  // Soft realtime: cheap local poll; Bitrix pull only every ~15s (or on SSE)
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
        const pull = wantPull ? "&pull=1" : "";
        const taskData = await api<Task[] | { results: Task[] }>(
          `/api/tasks/?project=${projectId}${pull}`,
          {},
          token!
        );
        if (!cancelled) setTasks(unwrapList(taskData));
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
  }, [token, projectId]);

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
      await load();
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
        {visible.length === 0 ? (
          <div className="empty-linked task-empty">
            <p className="muted">
              {tasks.length === 0
                ? "Создайте первую задачу — кнопка «Новая задача» сверху."
                : query.trim()
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
                className={`task-card${t.status === "done" ? " is-done" : ""}${enteringId === t.id ? " is-entering" : ""}`}
              >
                <div className="task-card-main">
                  <div className="task-card-top">
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
                  <strong className="task-card-title">{t.title}</strong>
                  {t.description ? (
                    <span className="task-card-desc muted">{t.description}</span>
                  ) : null}
                </div>
                <div className={`task-due ${due.tone}`}>
                  <span className="task-due-label">{due.label}</span>
                  {due.detail && <span className="task-due-date">{due.detail}</span>}
                </div>
              </Link>
            );
          })
        )}
      </div>
    </div>
  );
}
