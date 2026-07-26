import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  isAbortError,
  unwrapList,
  type DealBinding,
  type Paginated,
  type Portal,
  type Project,
  type Task,
  type WorkReport,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { DealHoursCard } from "../../components/DealHoursCard";
import { FlashToast } from "../../components/FlashToast";
import { FlameIcon, DisputeIcon } from "../../components/icons";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { useWorkspaceDismissals } from "../../hooks/useWorkspaceDismissals";
import { isValidDate, parseDue, startOfDay } from "../../lib/dates";
import { formatDueFull } from "../../lib/format";
import {
  getPortalLabel,
  PORTAL_LABEL_EVENT,
  portalDisplayName,
  setPortalLabel,
} from "../../lib/portalLabelCache";
import {
  CACHE_DEAL_HOURS,
  CACHE_PROJECTS,
  clearPortalCache,
  readPortalCache,
  writePortalCache,
} from "../../lib/portalSessionCache";
import { isProjectInProgress, projectProgress } from "../../lib/projectProgress";
import { isTaskOverdue, STATUS_LABEL } from "../../lib/status";
import { reportDetailPath } from "../shared/reportHelpers";

const RECENT_DONE_MS = 7 * 24 * 60 * 60 * 1000;
const HOT_DUE_DAYS = 2;
const CACHE_OVERVIEW = "overview";

type OverviewSnapshot = {
  openTasks: Task[];
  recentDone: Task[];
  disputedReports: WorkReport[];
};

function taskDueLabel(task: Task): string | null {
  if (!task.due_date) return null;
  return formatDueFull(task.due_date);
}

/** Due today / tomorrow / within N calendar days (not yet overdue). */
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
  return 3;
}

export function ClientProjects() {
  const { token, portal } = useAuth();
  const params = useParams();
  const portalId = Number(params.portalId || portal?.id);
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();

  const [portalInfo, setPortalInfo] = useState<Portal | null>(null);
  const [dealHours, setDealHours] = useState<DealBinding | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [openTasks, setOpenTasks] = useState<Task[]>([]);
  const [recentDone, setRecentDone] = useState<Task[]>([]);
  const [disputedReports, setDisputedReports] = useState<WorkReport[]>([]);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { dismiss, isDismissed } = useWorkspaceDismissals(
    Number.isFinite(portalId) && portalId > 0 ? portalId : null
  );

  const activeProjects = useMemo(
    () => projects.filter(isProjectInProgress).slice(0, 12),
    [projects]
  );

  const hotTasks = useMemo(() => {
    const seen = new Set<number>();
    const out: Task[] = [];
    for (const t of openTasks) {
      const overdue = isTaskOverdue(t.due_date, t.status);
      const soon = isDueSoon(t.due_date, t.status);
      const important = Boolean(t.is_important);
      if (!overdue && !soon && !important) continue;
      if (seen.has(t.id)) continue;
      seen.add(t.id);
      out.push(t);
    }
    out.sort((a, b) => hotPriority(a) - hotPriority(b));
    return out.slice(0, 12);
  }, [openTasks]);

  const visibleRecentDone = useMemo(
    () => recentDone.filter((t) => !isDismissed("task", t.id, t.updated_at)),
    [recentDone, isDismissed]
  );
  const loadGenRef = useRef(0);

  useEffect(() => {
    if (!portalId) {
      setPortalInfo(null);
      return;
    }
    // Route changes reuse this component. Never leave the previous client's
    // identity visible while the next client's data is loading.
    setPortalInfo(null);
    if (!isAgency && portal) {
      setPortalInfo(portal);
      const label = portalDisplayName(portal);
      if (label) setPortalLabel(portal.id, label);
      return;
    }
    const cached = getPortalLabel(portalId);
    if (cached) {
      setPortalInfo({
        id: portalId,
        name: cached,
        domain: "",
        role: "client",
        member_id: "",
        is_active: true,
      } as Portal);
    }
  }, [portalId, isAgency, portal]);

  useEffect(() => {
    if (!isAgency || !portalId) return;
    const onLabel = (event: Event) => {
      const detail = (event as CustomEvent<{ portalId: number; label: string }>).detail;
      if (!detail || detail.portalId !== portalId) return;
      setPortalInfo((prev) =>
        prev && prev.id === portalId
          ? { ...prev, name: detail.label }
          : ({
              id: portalId,
              name: detail.label,
              domain: "",
              role: "client",
              member_id: "",
              is_active: true,
            } as Portal)
      );
    };
    window.addEventListener(PORTAL_LABEL_EVENT, onLabel);
    return () => window.removeEventListener(PORTAL_LABEL_EVENT, onLabel);
  }, [isAgency, portalId]);

  async function refreshDealHoursInBackground(bindingId: number, signal?: AbortSignal) {
    if (!token || !portalId) return;
    const requestedPortalId = portalId;
    try {
      const updated = await api<DealBinding>(
        `/api/deal-bindings/${bindingId}/refresh-hours/`,
        { method: "POST", signal },
        token
      );
      if (signal?.aborted) return;
      if (updated.client_portal.id !== requestedPortalId || !updated.is_active) return;
      setDealHours(updated);
      writePortalCache(CACHE_DEAL_HOURS, requestedPortalId, updated);
    } catch (e) {
      if (!isAbortError(e)) undefined;
    }
  }

  useEffect(() => {
    // Clear first: an absent cache must not mean "keep the previous client".
    setDealHours(null);
    if (!portalId) {
      return;
    }
    const cached = readPortalCache<DealBinding>(CACHE_DEAL_HOURS, portalId);
    if (cached?.client_portal.id === portalId && cached.is_active) {
      setDealHours(cached);
    } else if (cached) {
      clearPortalCache(CACHE_DEAL_HOURS, portalId);
    }
  }, [portalId]);

  useEffect(() => {
    setProjects([]);
    const cachedOverview = portalId
      ? readPortalCache<OverviewSnapshot>(CACHE_OVERVIEW, portalId)
      : null;
    const scopedOpen =
      cachedOverview?.openTasks.filter((task) => task.portal_id === portalId) || [];
    const scopedDone =
      cachedOverview?.recentDone.filter((task) => task.portal_id === portalId) || [];
    const scopedDisputes =
      cachedOverview?.disputedReports.filter(
        (report) => report.portal_id === portalId
      ) || [];
    setOpenTasks(scopedOpen);
    setRecentDone(scopedDone);
    setDisputedReports(scopedDisputes);
    setOverviewLoading(cachedOverview === null);
    setError(null);
    if (!portalId) {
      return;
    }
    const cached = readPortalCache<Project[]>(CACHE_PROJECTS, portalId);
    const scoped = cached?.filter((project) => project.portal === portalId) || [];
    if (scoped.length) setProjects(scoped);
    if (cached && scoped.length !== cached.length) {
      writePortalCache(CACHE_PROJECTS, portalId, scoped);
    }
  }, [portalId]);

  async function load(signal?: AbortSignal) {
    if (!token || !portalId) return;
    const requestedPortalId = portalId;
    const gen = ++loadGenRef.current;
    try {
      const [openData, doneData, hoursData, disputedData, projectsData] = await Promise.all([
        api<Task[] | Paginated<Task>>(
          `/api/tasks/?portal=${requestedPortalId}&open=1`,
          { signal },
          token
        ),
        !isAgency
          ? api<Task[] | Paginated<Task>>(
              `/api/tasks/?portal=${requestedPortalId}&status=done&ordering=-updated_at`,
              { signal },
              token
            )
          : Promise.resolve([] as Task[]),
        isAgency
          ? api<DealBinding[] | Paginated<DealBinding>>(
              `/api/deal-bindings/?client_portal=${requestedPortalId}&is_active=true`,
              { signal },
              token
            ).catch((e) => {
              if (isAbortError(e)) throw e;
              return [] as DealBinding[];
            })
          : api<DealBinding>("/api/deal-bindings/mine/", { signal }, token).catch((e) => {
              if (isAbortError(e)) throw e;
              return null;
            }),
        isAgency
          ? api<WorkReport[] | Paginated<WorkReport>>(
              `/api/reports/?portal=${requestedPortalId}&status=disputed`,
              { signal },
              token
            )
          : Promise.resolve([] as WorkReport[]),
        api<Project[] | Paginated<Project>>(
          `/api/projects/?portal=${requestedPortalId}`,
          { signal },
          token
        ),
      ]);
      if (gen !== loadGenRef.current || signal?.aborted) return;

      const scopedOpen = unwrapList(openData).filter(
        (task) => task.portal_id === requestedPortalId
      );
      setOpenTasks(scopedOpen);
      const projectList = unwrapList(projectsData).filter(
        (project) => project.portal === requestedPortalId
      );
      setProjects(projectList);
      writePortalCache(CACHE_PROJECTS, requestedPortalId, projectList);

      if (!isAgency) {
        const cutoff = Date.now() - RECENT_DONE_MS;
        const scopedDone =
          unwrapList(doneData as Task[] | Paginated<Task>)
            .filter((task) => task.portal_id === requestedPortalId)
            .filter((t) => new Date(t.updated_at).getTime() >= cutoff)
            .slice(0, 6);
        setRecentDone(scopedDone);
        setDisputedReports([]);
        writePortalCache<OverviewSnapshot>(CACHE_OVERVIEW, requestedPortalId, {
          openTasks: scopedOpen,
          recentDone: scopedDone,
          disputedReports: [],
        });
        const mine = hoursData as DealBinding | null;
        const scopedMine =
          mine?.client_portal.id === requestedPortalId && mine.is_active ? mine : null;
        setDealHours(scopedMine);
        if (scopedMine) {
          writePortalCache(CACHE_DEAL_HOURS, requestedPortalId, scopedMine);
        } else {
          clearPortalCache(CACHE_DEAL_HOURS, requestedPortalId);
        }
        if (portal) setPortalInfo(portal);
        if (scopedMine?.id) void refreshDealHoursInBackground(scopedMine.id, signal);
      } else {
        const bindings = unwrapList(hoursData as DealBinding[] | Paginated<DealBinding>);
        const binding =
          bindings.find(
            (row) => row.client_portal.id === requestedPortalId && row.is_active
          ) || null;
        setDealHours(binding);
        if (binding) writePortalCache(CACHE_DEAL_HOURS, requestedPortalId, binding);
        else clearPortalCache(CACHE_DEAL_HOURS, requestedPortalId);
        const scopedDisputes = unwrapList(
          disputedData as WorkReport[] | Paginated<WorkReport>
        ).filter((report) => report.portal_id === requestedPortalId);
        setRecentDone([]);
        setDisputedReports(scopedDisputes);
        writePortalCache<OverviewSnapshot>(CACHE_OVERVIEW, requestedPortalId, {
          openTasks: scopedOpen,
          recentDone: [],
          disputedReports: scopedDisputes,
        });
        const fromBinding = binding?.client_portal;
        if (fromBinding) {
          const label = portalDisplayName(fromBinding);
          if (label) {
            setPortalLabel(requestedPortalId, label);
            setPortalInfo(fromBinding);
          }
        } else {
          const cached = getPortalLabel(requestedPortalId);
          if (cached) {
            setPortalInfo((prev) =>
              prev?.id === portalId
                ? prev
                : ({
                    id: portalId,
                    name: cached,
                    domain: "",
                    role: "client",
                    member_id: "",
                    is_active: true,
                  } as Portal)
            );
          }
        }
      }
    } finally {
      if (gen === loadGenRef.current && !signal?.aborted) {
        setOverviewLoading(false);
      }
    }
  }

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal).catch((e) => {
      if (!isAbortError(e)) setError(e instanceof Error ? e.message : "Ошибка");
    });
    return () => {
      loadGenRef.current += 1;
      ac.abort();
    };
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

  useEffect(() => {
    if (!token || !portalId) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") reloadRef.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [token, portalId]);

  const titleName = portalInfo?.name || portalInfo?.domain || "Клиент";
  const agencyNeedsAttention =
    disputedReports.length > 0 || activeProjects.length > 0 || hotTasks.length > 0;
  const projectsListPath = isAgency ? `/portals/${portalId}/projects` : "/projects";

  return (
    <div className="workspace-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{isAgency ? titleName : "Рабочее пространство"}</h1>
          <p className="page-sub">
            {isAgency
              ? "Часы, обращения по отчётам, активные проекты и горящие сроки"
              : "Часы, проекты в работе и недавно завершённое"}
          </p>
        </div>
        <Link to={projectsListPath} className="btn btn-primary" data-tour="tour-new-project">
          Все проекты
        </Link>
      </div>

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      {dealHours ? (
        <div className="client-hours-panel" data-tour="tour-deal-hours">
          <DealHoursCard binding={dealHours} audience={isAgency ? "agency" : "client"} />
        </div>
      ) : null}

      {!isAgency ? (
        <div className="workspace-focus" data-tour="tour-waiting-for-you">
          <div className="workspace-split-focus">
            <section className="workspace-focus-block">
              <div className="linked-head">
                <h2 className="section-title">Проекты в работе</h2>
                <p className="muted">Модули, которые ещё не закрыты на 100%</p>
              </div>
              {overviewLoading && activeProjects.length === 0 ? (
                <div className="empty-linked workspace-empty data-loading-state">
                  <span className="data-loading-spinner" aria-hidden />
                  <p className="muted">Загружаем проекты…</p>
                </div>
              ) : activeProjects.length === 0 ? (
                <div className="empty-linked workspace-empty">
                  <p className="muted">Сейчас нет проектов в работе.</p>
                </div>
              ) : (
                <div className="workspace-attention-list">
                  {activeProjects.map((p) => {
                    const { done, total, pct } = projectProgress(p);
                    return (
                      <Link
                        key={`project-${p.id}`}
                        to={`/projects/${p.id}`}
                        className="workspace-attention-card is-project"
                      >
                        <div className="workspace-attention-top">
                          <span className="workspace-chip tone-project">{pct}%</span>
                          <span className="muted">
                            {done}/{total} задач
                          </span>
                        </div>
                        <strong>{p.name}</strong>
                        <span className="muted">Открыть проект</span>
                      </Link>
                    );
                  })}
                </div>
              )}
            </section>

            <section className="workspace-focus-block">
              <div className="linked-head">
                <h2 className="section-title">Недавно завершено</h2>
                <p className="muted">Можно посмотреть итог в задаче</p>
              </div>
              {overviewLoading && visibleRecentDone.length === 0 ? (
                <div className="empty-linked workspace-empty data-loading-state">
                  <span className="data-loading-spinner" aria-hidden />
                  <p className="muted">Загружаем задачи…</p>
                </div>
              ) : visibleRecentDone.length === 0 ? (
                <div className="empty-linked workspace-empty">
                  <p className="muted">За последние дни завершённых задач нет.</p>
                </div>
              ) : (
                <div className="workspace-attention-list">
                  {visibleRecentDone.map((t) => (
                    <Link
                      key={`done-${t.id}`}
                      to={`/tasks/${t.id}`}
                      className="workspace-attention-card is-done"
                      onClick={() => dismiss("task", t.id, t.updated_at)}
                    >
                      <div className="workspace-attention-top">
                        <span className="workspace-chip tone-done">Завершена</span>
                        <span className="muted">{t.project_name}</span>
                      </div>
                      <strong>{t.title}</strong>
                      <span className="muted">Открыть задачу</span>
                    </Link>
                  ))}
                </div>
              )}
            </section>
          </div>
        </div>
      ) : (
        <div className="workspace-focus" data-tour="tour-agency-focus">
          {overviewLoading && !agencyNeedsAttention ? (
            <div className="empty-linked workspace-empty data-loading-state">
              <span className="data-loading-spinner" aria-hidden />
              <p className="muted">Загружаем обзор клиента…</p>
            </div>
          ) : !agencyNeedsAttention ? (
            <div className="empty-linked workspace-empty">
              <p className="muted">
                Нет обращений по отчётам, активных проектов и горящих сроков. Полный список — во
                вкладке «Проекты».
              </p>
            </div>
          ) : (
            <>
              {disputedReports.length > 0 ? (
                <section className="workspace-focus-block workspace-dispute-section">
                  <div className="linked-head">
                    <div className="workspace-dispute-title-row">
                      <h2 className="section-title workspace-dispute-title">
                        <span className="workspace-dispute-badge" aria-hidden>
                          <DisputeIcon size={15} />
                        </span>
                        <span>Связь с менеджером</span>
                      </h2>
                    </div>
                    <p className="muted">Клиент написал по отчёту — нужно разобрать</p>
                  </div>
                  <div className="workspace-attention-list">
                    {disputedReports.map((r) => (
                      <Link
                        key={`dispute-${r.id}`}
                        to={reportDetailPath(portalId, true, r.id)}
                        className="workspace-attention-card is-dispute"
                      >
                        <div className="workspace-attention-top">
                          <span className="workspace-dispute-pill">Обсуждение</span>
                          <span className="muted">Отчёт №{r.id}</span>
                        </div>
                        <span className="muted">Открыть и разобрать</span>
                      </Link>
                    ))}
                  </div>
                </section>
              ) : null}

              <div className="workspace-split-focus">
                <section className="workspace-focus-block">
                  <div className="linked-head">
                    <h2 className="section-title">Проекты в работе</h2>
                    <p className="muted">Модули клиента, которые ещё не на 100%</p>
                  </div>
                  {activeProjects.length === 0 ? (
                    <div className="empty-linked workspace-empty">
                      <p className="muted">Все проекты закрыты или ещё не созданы.</p>
                    </div>
                  ) : (
                    <div className="workspace-attention-list">
                      {activeProjects.map((p) => {
                        const { done, total, pct } = projectProgress(p);
                        return (
                          <Link
                            key={p.id}
                            to={`/projects/${p.id}`}
                            className="workspace-attention-card is-project"
                          >
                            <div className="workspace-attention-top">
                              <span className="workspace-chip tone-project">{pct}%</span>
                              <span className="muted">
                                {done}/{total} задач
                              </span>
                            </div>
                            <strong>{p.name}</strong>
                            <span className="muted">Открыть проект</span>
                          </Link>
                        );
                      })}
                    </div>
                  )}
                </section>

                <section className="workspace-focus-block">
                  <div className="linked-head">
                    <h2
                      className={`workspace-hot-heading${
                        hotTasks.length > 0 ? " is-shaking" : " is-calm"
                      }`}
                    >
                      <span className="workspace-hot-pill">
                        <FlameIcon filled size={14} />
                        <span className="workspace-hot-label">Горят</span>
                      </span>
                    </h2>
                    <p className="muted">Просроченные, срок 1–2 дня и важные</p>
                  </div>
                  {hotTasks.length === 0 ? (
                    <div className="empty-linked workspace-empty">
                      <p className="muted">Сроков и важных задач нет.</p>
                    </div>
                  ) : (
                    <div className="workspace-attention-list">
                      {hotTasks.map((t) => {
                        const overdue = isTaskOverdue(t.due_date, t.status);
                        const soon = isDueSoon(t.due_date, t.status);
                        const due = taskDueLabel(t);
                        return (
                          <Link
                            key={t.id}
                            to={`/tasks/${t.id}`}
                            className={`workspace-attention-card${
                              overdue ? " is-overdue" : soon ? " is-soon" : ""
                            }`}
                          >
                            <div className="workspace-attention-top">
                              {overdue ? (
                                <span className="workspace-chip tone-overdue">Просрочена</span>
                              ) : null}
                              {soon ? (
                                <span className="workspace-chip tone-soon">Скоро срок</span>
                              ) : null}
                              {t.is_important ? (
                                <span className="task-important-pill" title="Важная задача">
                                  <FlameIcon filled size={14} />
                                  Важно
                                </span>
                              ) : null}
                              <span className="muted">{t.project_name}</span>
                            </div>
                            <strong>{t.title}</strong>
                            <span className="muted">
                              {STATUS_LABEL[t.status]}
                              {due ? ` · до ${due}` : ""}
                            </span>
                          </Link>
                        );
                      })}
                    </div>
                  )}
                </section>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
