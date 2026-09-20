import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
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
import { DealHoursCard, hasDealHoursPackage } from "../../components/DealHoursCard";
import { NowWorkingCard } from "../../components/NowWorkingCard";
import { FlashToast } from "../../components/FlashToast";
import {
  CheckCircleGlyph,
  DisputeIcon,
  FlameIcon,
  GridGlyph,
} from "../../components/icons";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { useWorkspaceDismissals } from "../../hooks/useWorkspaceDismissals";
import { formatRuDateTime, isValidDate, parseDue, startOfDay } from "../../lib/dates";
import { formatDayShort, formatDueFull, formatDuration } from "../../lib/format";
import { PICKER_PAGE_SIZE, withPage } from "../../lib/pagination";
import { linkStateFrom } from "../../lib/smartBack";
import { displayTimeZone } from "../../lib/timezone";
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
import { reportDetailPath, reportTitle, reportsApiQuery } from "../shared/reportHelpers";

const RECENT_DONE_MS = 7 * 24 * 60 * 60 * 1000;
const HOT_DUE_DAYS = 2;
const CACHE_OVERVIEW = "overview";

type OverviewSnapshot = {
  openTasks: Task[];
  recentDone: Task[];
  disputedReports: WorkReport[];
  pendingReports?: WorkReport[];
  attentionTasks?: Task[];
  workingTasks?: Task[];
};

type AttentionTone = "review" | "confirm" | "reply";

type AttentionItem = {
  key: string;
  tone: AttentionTone;
  chip: string;
  title: string;
  requestedAt: string | null;
  href: string;
  openLabel: string;
};

const ATTENTION_CAP = 8;

function earliestProjectDue(projectId: number, tasks: Task[]): string | null {
  const dues = tasks
    .filter((t) => t.project === projectId && t.status !== "done" && t.due_date)
    .map((t) => t.due_date as string)
    .sort();
  return dues[0] || null;
}

function taskDueLabel(task: Task, timeZone: string): string | null {
  if (!task.due_date) return null;
  return formatDueFull(task.due_date, timeZone);
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
  const location = useLocation();
  const fromState = linkStateFrom(location);
  const portalId = Number(params.portalId || portal?.id);
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();

  const [portalInfo, setPortalInfo] = useState<Portal | null>(null);
  const [dealHours, setDealHours] = useState<DealBinding | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [openTasks, setOpenTasks] = useState<Task[]>([]);
  const [workingTasks, setWorkingTasks] = useState<Task[]>([]);
  const [recentDone, setRecentDone] = useState<Task[]>([]);
  const [disputedReports, setDisputedReports] = useState<WorkReport[]>([]);
  const [pendingReports, setPendingReports] = useState<WorkReport[]>([]);
  const [attentionTasks, setAttentionTasks] = useState<Task[]>([]);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const dueTz = displayTimeZone({
    role: portal?.role,
    portalTimezone: portalInfo?.timezone || portal?.timezone,
  });
  const { dismiss, isDismissed } = useWorkspaceDismissals(
    Number.isFinite(portalId) && portalId > 0 ? portalId : null
  );

  const activeProjects = useMemo(
    () =>
      projects
        .filter(isProjectInProgress)
        .slice()
        .sort((a, b) => {
          const byCreated = String(a.created_at || "").localeCompare(String(b.created_at || ""));
          return byCreated || a.id - b.id;
        })
        .slice(0, 3),
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
    () => recentDone.filter((t) => !isDismissed("task", t.id, t.updated_at)).slice(0, 3),
    [recentDone, isDismissed]
  );

  const attentionItems = useMemo(() => {
    const items: AttentionItem[] = [];

    for (const report of pendingReports) {
      items.push({
        key: `report-${report.id}`,
        tone: "review",
        chip: "На согласовании",
        title: reportTitle(report),
        requestedAt: report.sent_at || report.updated_at,
        href: reportDetailPath(portalId, false, report.id),
        openLabel: "Открыть отчёт",
      });
    }

    for (const task of attentionTasks) {
      const awaiting = Boolean(task.awaiting_client);
      items.push({
        key: `task-${task.id}`,
        tone: awaiting ? "reply" : "confirm",
        chip: awaiting ? "Ожидает ответа" : "На подтверждении",
        title: task.title,
        requestedAt: awaiting
          ? task.awaiting_client_at || task.updated_at
          : task.completed_at || task.updated_at,
        href: `/tasks/${task.id}`,
        openLabel: "Открыть задачу",
      });
    }

    return items.slice(0, ATTENTION_CAP);
  }, [pendingReports, attentionTasks, portalId]);
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
    setWorkingTasks(
      (cachedOverview?.workingTasks || scopedOpen).filter(
        (task) => task.portal_id === portalId && task.is_working
      )
    );
    setRecentDone(scopedDone);
    setDisputedReports(scopedDisputes);
    setPendingReports(
      (cachedOverview?.pendingReports || []).filter((report) => report.portal_id === portalId)
    );
    setAttentionTasks(
      (cachedOverview?.attentionTasks || []).filter((task) => task.portal_id === portalId)
    );
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
      const [openData, doneData, hoursData, disputedData, reviewData, attentionData, projectsData, workingData] =
        await Promise.all([
        api<Task[] | Paginated<Task>>(
          withPage(`/api/tasks/?portal=${requestedPortalId}&open=1`, 1, PICKER_PAGE_SIZE),
          { signal },
          token
        ),
        !isAgency
          ? api<Task[] | Paginated<Task>>(
              withPage(
                `/api/tasks/?portal=${requestedPortalId}&status=done&ordering=-updated_at`,
                1,
                PICKER_PAGE_SIZE
              ),
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
        !isAgency
          ? api<WorkReport[] | Paginated<WorkReport>>(
              reportsApiQuery(requestedPortalId, "review"),
              { signal },
              token
            ).catch((e) => {
              if (isAbortError(e)) throw e;
              return [] as WorkReport[];
            })
          : Promise.resolve([] as WorkReport[]),
        !isAgency
          ? api<Task[] | Paginated<Task>>(
              withPage(
                `/api/tasks/?portal=${requestedPortalId}&attention=1`,
                1,
                PICKER_PAGE_SIZE
              ),
              { signal },
              token
            ).catch((e) => {
              if (isAbortError(e)) throw e;
              return [] as Task[];
            })
          : Promise.resolve([] as Task[]),
        api<Project[] | Paginated<Project>>(
          withPage(`/api/projects/?portal=${requestedPortalId}`, 1, PICKER_PAGE_SIZE),
          { signal },
          token
        ),
        api<Task[] | Paginated<Task>>(
          withPage(`/api/tasks/?portal=${requestedPortalId}&working=1`, 1, PICKER_PAGE_SIZE),
          { signal },
          token
        ),
      ]);
      if (gen !== loadGenRef.current || signal?.aborted) return;

      const scopedOpen = unwrapList(openData).filter(
        (task) => task.portal_id === requestedPortalId
      );
      setOpenTasks(scopedOpen);
      const scopedWorking = unwrapList(workingData).filter(
        (task) => task.portal_id === requestedPortalId && Boolean(task.is_working)
      );
      setWorkingTasks(scopedWorking);
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
        const scopedReview = unwrapList(reviewData as WorkReport[] | Paginated<WorkReport>).filter(
          (report) => report.portal_id === requestedPortalId
        );
        const scopedAttention = unwrapList(attentionData as Task[] | Paginated<Task>).filter(
          (task) => task.portal_id === requestedPortalId
        );
        setPendingReports(scopedReview);
        setAttentionTasks(scopedAttention);
        writePortalCache<OverviewSnapshot>(CACHE_OVERVIEW, requestedPortalId, {
          openTasks: scopedOpen,
          recentDone: scopedDone,
          disputedReports: [],
          pendingReports: scopedReview,
          attentionTasks: scopedAttention,
          workingTasks: scopedWorking,
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
        setPendingReports([]);
        setAttentionTasks([]);
        writePortalCache<OverviewSnapshot>(CACHE_OVERVIEW, requestedPortalId, {
          openTasks: scopedOpen,
          recentDone: [],
          disputedReports: scopedDisputes,
          workingTasks: scopedWorking,
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
        </div>
        <Link to={projectsListPath} className="btn btn-primary btn-with-icon" data-tour="tour-new-project">
          <GridGlyph />
          Все проекты
        </Link>
      </div>

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <div className="overview-hours-split" data-tour="tour-deal-hours">
        <div className="overview-hours-col">
          {hasDealHoursPackage(dealHours) && dealHours ? (
            <DealHoursCard binding={dealHours} audience={isAgency ? "agency" : "client"} />
          ) : (
            <section className="deal-hours-card is-client-pack is-empty-pack" aria-label="Пакет часов">
              <div className="deal-hours-card-head">
                <h2 className="section-title">Пакет часов</h2>
              </div>
              <p className="muted">
                {overviewLoading
                  ? "Загружаем пакет…"
                  : "Пакет часов по этому кабинету пока не подключён."}
              </p>
            </section>
          )}
        </div>
        <NowWorkingCard tasks={workingTasks} loading={overviewLoading} />
      </div>

      {!isAgency ? (
        <div className="workspace-focus overview-layout" data-tour="tour-waiting-for-you">
          <div className="overview-split">
            <section className="overview-card overview-projects-card">
              <div className="overview-card-head">
                <h2 className="section-title">Проекты в работе</h2>
                <Link to={projectsListPath} className="overview-text-link">
                  Все проекты
                </Link>
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
                <div className="overview-project-list">
                  {activeProjects.map((p) => {
                    const { done, total, pct } = projectProgress(p);
                    const due = p.due_date || earliestProjectDue(p.id, openTasks);
                    const dueLabel = due ? formatRuDateTime(due, dueTz) : "";
                    const tracked = p.total_tracked_seconds || 0;
                    const blurb =
                      (p.description || "").trim() ||
                      (p.has_active_work ? "Сейчас в работе у команды" : "Открытые задачи в модуле");
                    return (
                      <Link
                        key={`project-${p.id}`}
                        to={`/projects/${p.id}`}
                        state={fromState}
                        className="overview-project-row"
                      >
                        <div className="overview-project-copy">
                          <strong>{p.name}</strong>
                          <p>{blurb}</p>
                        </div>
                        <div className="overview-project-progress">
                          <span className="overview-project-pct">{pct}%</span>
                          <span className="overview-mini-track" aria-hidden>
                            <span style={{ width: `${pct}%` }} />
                          </span>
                          <span className="muted">
                            {done}/{total} задач выполнено
                            {tracked > 0 ? ` · ${formatDuration(tracked)}` : ""}
                          </span>
                        </div>
                        <div className="overview-project-meta">
                          {dueLabel ? (
                            <span className="muted">Дедлайн {dueLabel}</span>
                          ) : (
                            <span className="muted">Без дедлайна</span>
                          )}
                        </div>
                      </Link>
                    );
                  })}
                </div>
              )}
            </section>

            <section className="overview-card overview-attention-card">
              <div className="overview-card-head">
                <h2 className="section-title">Требует вашего внимания</h2>
              </div>
              {overviewLoading && attentionItems.length === 0 ? (
                <div className="empty-linked workspace-empty data-loading-state">
                  <span className="data-loading-spinner" aria-hidden />
                  <p className="muted">Загружаем задачи…</p>
                </div>
              ) : attentionItems.length === 0 ? (
                <div className="empty-linked workspace-empty">
                  <p className="muted">Сейчас ничего не ждёт вашего ответа.</p>
                </div>
              ) : (
                <div className="overview-attention-list">
                  {attentionItems.map((item) => {
                    const requested = item.requestedAt
                      ? formatDayShort(item.requestedAt, dueTz)
                      : "";
                    const body = (
                      <>
                        <span className={`overview-chip tone-${item.tone}`}>{item.chip}</span>
                        <strong>{item.title}</strong>
                        <span className="overview-attention-foot">
                          {requested ? <span className="muted">Запрошено {requested}</span> : <span />}
                          <span className="overview-text-link">{item.openLabel}</span>
                        </span>
                      </>
                    );
                    return (
                      <Link
                        key={item.key}
                        to={item.href}
                        state={fromState}
                        className="overview-attention-row"
                      >
                        {body}
                      </Link>
                    );
                  })}
                </div>
              )}
            </section>

            <section className="overview-card overview-done-card">
              <div className="overview-card-head">
                <h2 className="section-title">Недавно завершено</h2>
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
                <div className="overview-done-list">
                  {visibleRecentDone.map((t) => (
                    <Link
                      key={`done-${t.id}`}
                      to={`/tasks/${t.id}`}
                      state={fromState}
                      className="overview-done-row"
                      onClick={() => dismiss("task", t.id, t.updated_at)}
                    >
                      <span className="overview-done-check" aria-hidden>
                        <CheckCircleGlyph />
                      </span>
                      <strong>{t.title}</strong>
                      <span className="workspace-chip tone-done">Завершена</span>
                      <span className="muted">
                        {formatDayShort(t.completed_at || t.updated_at, dueTz)}
                      </span>
                    </Link>
                  ))}
                  <Link to={projectsListPath} className="overview-text-link overview-done-more">
                    Смотреть все завершённые
                  </Link>
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
                        state={fromState}
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
                            state={fromState}
                            className="workspace-attention-card is-project"
                          >
                            <div className="workspace-attention-top">
                              <span className="workspace-chip tone-project">{pct}%</span>
                              {p.has_active_work ? (
                                <span className="task-working-pill">Сейчас в работе</span>
                              ) : null}
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
                        const due = taskDueLabel(t, dueTz);
                        return (
                          <Link
                            key={t.id}
                            to={`/tasks/${t.id}`}
                            state={fromState}
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
