import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  unwrapList,
  type ActivityEvent,
  type ActivityType,
  type Portal,
  type Project,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";

const TYPE_META: Record<
  ActivityType,
  { label: string; tone: string }
> = {
  project_created: { label: "Создание", tone: "tone-project" },
  task_created: { label: "Задача", tone: "tone-task" },
  task_updated: { label: "Статус", tone: "tone-update" },
  comment: { label: "Комментарий", tone: "tone-comment" },
  attachment: { label: "Файл", tone: "tone-file" },
};

function formatWhen(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diff = (now.getTime() - d.getTime()) / 1000;
  if (diff < 60) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  return d.toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ClientProjects() {
  const { token, portal } = useAuth();
  const params = useParams();
  const portalId = Number(params.portalId || portal?.id);
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();

  const [portalInfo, setPortalInfo] = useState<Portal | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [enteringProjectId, setEnteringProjectId] = useState<number | null>(null);

  const recentProjects = useMemo(() => {
    const byId = new Map(projects.map((p) => [p.id, p]));
    const ordered: Project[] = [];
    const seen = new Set<number>();
    for (const ev of activity) {
      if (!ev.project_id || seen.has(ev.project_id)) continue;
      const p = byId.get(ev.project_id);
      if (p) {
        ordered.push(p);
        seen.add(p.id);
      }
      if (ordered.length >= 4) break;
    }
    for (const p of projects) {
      if (!seen.has(p.id)) ordered.push(p);
    }
    return ordered.slice(0, 4);
  }, [activity, projects]);

  async function load() {
    if (!token || !portalId) return;
    const [projectData, activityData, portalsData] = await Promise.all([
      api<Project[] | { results: Project[] }>(
        `/api/projects/?portal=${portalId}&pull=1`,
        {},
        token
      ),
      api<ActivityEvent[]>(`/api/activity/?portal=${portalId}`, {}, token),
      isAgency
        ? api<Portal[] | { results: Portal[] }>("/api/portals/", {}, token)
        : Promise.resolve([] as Portal[]),
    ]);
    setProjects(unwrapList(projectData));
    setActivity(Array.isArray(activityData) ? activityData : []);
    if (isAgency) {
      const found = unwrapList(portalsData as Portal[] | { results: Portal[] }).find(
        (p) => p.id === portalId
      );
      setPortalInfo(found || null);
    } else {
      setPortalInfo(portal);
    }
  }

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [token, portalId]);

  const reloadRef = useRef<() => void>(() => undefined);
  reloadRef.current = () => {
    void load().catch(() => undefined);
  };

  usePortalLiveSync({
    token,
    portalId,
    onEvent: () => reloadRef.current(),
  });

  // Soft realtime for projects + activity (safety net)
  useEffect(() => {
    if (!token || !portalId) return;
    let cancelled = false;
    let tickCount = 0;
    let inFlight = false;

    async function tick(forcePull = false) {
      if (cancelled || inFlight) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      inFlight = true;
      tickCount += 1;
      try {
        const wantPull = forcePull || tickCount % 4 === 0;
        const qs = wantPull
          ? `/api/projects/?portal=${portalId}&pull=1`
          : `/api/projects/?portal=${portalId}`;
        const [projectData, activityData] = await Promise.all([
          api<Project[] | { results: Project[] }>(qs, {}, token!),
          api<ActivityEvent[]>(`/api/activity/?portal=${portalId}`, {}, token!),
        ]);
        if (!cancelled) {
          setProjects(unwrapList(projectData));
          setActivity(Array.isArray(activityData) ? activityData : []);
        }
      } catch {
        // next tick
      } finally {
        inFlight = false;
      }
    }

    const id = window.setInterval(() => void tick(false), 5000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void tick(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [token, portalId]);

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !portalId) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api<Project>(
        "/api/projects/",
        {
          method: "POST",
          body: JSON.stringify({ portal: portalId, name, description }),
        },
        token
      );
      setName("");
      setDescription("");
      setShowCreate(false);
      setEnteringProjectId(created.id);
      toast.show("Он появился в панели слева", "Проект создан");
      await load();
      window.dispatchEvent(new Event("projects-updated"));
      window.setTimeout(() => setEnteringProjectId(null), 900);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать проект");
    } finally {
      setBusy(false);
    }
  }

  function eventHref(ev: ActivityEvent): string | null {
    if (ev.task_id) return `/tasks/${ev.task_id}`;
    if (ev.project_id) return `/projects/${ev.project_id}`;
    return null;
  }

  const titleName = portalInfo?.name || portalInfo?.domain || "Клиент";

  return (
    <div className="workspace-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{isAgency ? titleName : "Рабочее пространство"}</h1>
          <p className="page-sub">
            Лента изменений по модулям и задачам
            {isAgency ? " этого клиента" : ""}
          </p>
        </div>
        {isAgency ? (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setShowCreate((v) => !v)}
            data-tour="tour-new-project"
          >
            {showCreate ? "Закрыть" : "Новый проект"}
          </button>
        ) : null}
      </div>

      {error && <div className="error-banner">{error}</div>}

      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      {isAgency && showCreate && (
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
      )}

      <div className="workspace-split">
        <section className="workspace-col" data-tour="tour-recent-projects">
          <div className="linked-head">
            <h2 className="section-title">Недавние проекты</h2>
            <p className="muted">С активностью или недавно созданные</p>
          </div>
          {recentProjects.length === 0 ? (
            <div className="empty-linked">
              <p className="muted">
                {isAgency
                  ? "Создайте первый проект — он появится в панели слева и как задача в Bitrix-проекте компании."
                  : "Пока нет проектов. Их создаёт агентство — здесь появятся задачи."}
              </p>
            </div>
          ) : (
            <div className="recent-projects-stack">
              {recentProjects.map((p) => {
                const total = p.tasks_count || 0;
                const done = p.done_count || 0;
                const pct = total ? Math.round((done / total) * 100) : 0;
                return (
                  <Link
                    key={p.id}
                    to={`/projects/${p.id}`}
                    className={`recent-project-card${enteringProjectId === p.id ? " is-entering" : ""}`}
                  >
                    <strong>{p.name}</strong>
                    <span className="muted">{p.description || "Без описания"}</span>
                    <div className="row" style={{ marginTop: 12, justifyContent: "space-between" }}>
                      <span className="muted">
                        {done}/{total} задач
                      </span>
                      <strong>{pct}%</strong>
                    </div>
                    <div className="progress">
                      <span style={{ width: `${pct}%` }} />
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
        </section>

        <section className="workspace-col activity-section" data-tour="tour-activity-feed">
          <div className="linked-head">
            <h2 className="section-title">Лента активности</h2>
            <p className="muted">Что изменилось: задачи, ответы, статусы</p>
          </div>
          <div className="activity-feed">
            {activity.length === 0 && (
              <div className="empty-linked">
                <p className="muted">Пока тихо — активность появится здесь.</p>
              </div>
            )}
            {activity.map((ev) => {
              const meta = TYPE_META[ev.type];
              const href = eventHref(ev);
              const inner = (
                <div className="activity-card-inner">
                  <div className="activity-top">
                    {ev.project_name ? (
                      <span className="activity-project">{ev.project_name}</span>
                    ) : (
                      <span />
                    )}
                    <time className="activity-time muted">{formatWhen(ev.at)}</time>
                  </div>
                  <div className="activity-headline">
                    <span className={`activity-badge ${meta.tone}`}>{meta.label}</span>
                    <strong>{ev.title}</strong>
                  </div>
                  {(ev.task_title || ev.subtitle) && (
                    <div className="activity-meta">
                      {ev.task_title && (
                        <span className="activity-task">задача «{ev.task_title}»</span>
                      )}
                      {ev.task_title && ev.subtitle ? (
                        <span className="activity-sep" aria-hidden>
                          ·
                        </span>
                      ) : null}
                      {ev.subtitle && <span className="activity-detail">{ev.subtitle}</span>}
                    </div>
                  )}
                </div>
              );
              return href ? (
                <Link key={ev.id} to={href} className={`activity-row ${meta.tone}`}>
                  {inner}
                </Link>
              ) : (
                <div key={ev.id} className={`activity-row ${meta.tone}`}>
                  {inner}
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}
