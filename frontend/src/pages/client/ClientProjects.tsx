import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  unwrapList,
  type DealBinding,
  type Paginated,
  type Portal,
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
import { isTaskOverdue, STATUS_LABEL } from "../../lib/status";
import {
  reportDetailPath,
  reportTitle,
  STATUS_LABEL_RU,
} from "../shared/reportHelpers";

const RECENT_DONE_MS = 7 * 24 * 60 * 60 * 1000;
const HOT_DUE_DAYS = 2;

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
  const [openTasks, setOpenTasks] = useState<Task[]>([]);
  const [recentDone, setRecentDone] = useState<Task[]>([]);
  const [pendingReports, setPendingReports] = useState<WorkReport[]>([]);
  const [disputedReports, setDisputedReports] = useState<WorkReport[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { dismiss, isDismissed } = useWorkspaceDismissals(
    Number.isFinite(portalId) && portalId > 0 ? portalId : null
  );

  const clientTasks = useMemo(
    () =>
      openTasks.filter(
        (t) =>
          t.created_by_role === "client" &&
          !isDismissed("task", t.id, t.updated_at)
      ),
    [openTasks, isDismissed]
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

  const visiblePendingReports = useMemo(
    () => pendingReports.filter((r) => !isDismissed("report", r.id, r.updated_at)),
    [pendingReports, isDismissed]
  );

  const visibleRecentDone = useMemo(
    () => recentDone.filter((t) => !isDismissed("task", t.id, t.updated_at)),
    [recentDone, isDismissed]
  );

  const visibleDisputedReports = useMemo(
    () => disputedReports.filter((r) => !isDismissed("report", r.id, r.updated_at)),
    [disputedReports, isDismissed]
  );
  const loadGenRef = useRef(0);
  const loadInFlightRef = useRef(false);

  async function load() {
    if (!token || !portalId) return;
    if (loadInFlightRef.current) return;
    const gen = loadGenRef.current;
    loadInFlightRef.current = true;
    try {
      const [openData, doneData, portalsData, hoursData, reportsData, disputedData] =
        await Promise.all([
        api<Task[] | Paginated<Task>>(
          `/api/tasks/?portal=${portalId}&open=1`,
          {},
          token
        ),
        !isAgency
          ? api<Task[] | Paginated<Task>>(
              `/api/tasks/?portal=${portalId}&status=done&ordering=-updated_at`,
              {},
              token
            )
          : Promise.resolve([] as Task[]),
        isAgency
          ? api<Portal[] | Paginated<Portal>>("/api/portals/", {}, token)
          : Promise.resolve([] as Portal[]),
        isAgency
          ? api<DealBinding[] | Paginated<DealBinding>>(
              `/api/deal-bindings/?client_portal=${portalId}&is_active=true`,
              {},
              token
            ).catch(() => [] as DealBinding[])
          : api<DealBinding>("/api/deal-bindings/mine/", {}, token).catch(() => null),
        !isAgency
          ? api<WorkReport[] | Paginated<WorkReport>>(
              `/api/reports/?portal=${portalId}&bucket=review`,
              {},
              token
            )
          : Promise.resolve([] as WorkReport[]),
        isAgency
          ? api<WorkReport[] | Paginated<WorkReport>>(
              `/api/reports/?portal=${portalId}&status=disputed`,
              {},
              token
            )
          : Promise.resolve([] as WorkReport[]),
      ]);
      if (gen !== loadGenRef.current) return;

      setOpenTasks(unwrapList(openData));

      if (!isAgency) {
        const cutoff = Date.now() - RECENT_DONE_MS;
        setRecentDone(
          unwrapList(doneData as Task[] | Paginated<Task>)
            .filter((t) => new Date(t.updated_at).getTime() >= cutoff)
            .slice(0, 6)
        );
        setPendingReports(unwrapList(reportsData as WorkReport[] | Paginated<WorkReport>));
        setDisputedReports([]);
        setDealHours(hoursData as DealBinding | null);
        setPortalInfo(portal);
      } else {
        const bindings = unwrapList(hoursData as DealBinding[] | Paginated<DealBinding>);
        setDealHours(bindings[0] || null);
        setRecentDone([]);
        setPendingReports([]);
        setDisputedReports(unwrapList(disputedData as WorkReport[] | Paginated<WorkReport>));
        const found = unwrapList(portalsData as Portal[] | Paginated<Portal>).find(
          (p) => p.id === portalId
        );
        setPortalInfo(found || null);
      }
    } finally {
      loadInFlightRef.current = false;
    }
  }

  useEffect(() => {
    loadGenRef.current += 1;
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

  useEffect(() => {
    if (!token || !portalId) return;
    const id = window.setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      reloadRef.current();
    }, 15000);
    const onVisible = () => {
      if (document.visibilityState === "visible") reloadRef.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [token, portalId]);

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !portalId || !isAgency) return;
    setBusy(true);
    setError(null);
    try {
      await api(
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
      toast.show("Он появился в панели слева", "Проект создан");
      await load();
      window.dispatchEvent(new Event("projects-updated"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать проект");
    } finally {
      setBusy(false);
    }
  }

  const titleName = portalInfo?.name || portalInfo?.domain || "Клиент";
  const agencyNeedsAttention =
    visibleDisputedReports.length > 0 ||
    clientTasks.length > 0 ||
    hotTasks.length > 0;

  return (
    <div className="workspace-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{isAgency ? titleName : "Рабочее пространство"}</h1>
          <p className="page-sub">
            {isAgency
              ? "Часы, споры по отчётам, задачи клиента и сроки"
              : "Часы и то, что ждёт вашего ответа"}
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

      {dealHours ? (
        <div className="client-hours-panel" data-tour="tour-deal-hours">
          <DealHoursCard binding={dealHours} audience={isAgency ? "agency" : "client"} />
        </div>
      ) : null}

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

      {!isAgency ? (
        <div className="workspace-focus" data-tour="tour-waiting-for-you">
          <div className="workspace-split-focus">
            <section className="workspace-focus-block">
              <div className="linked-head">
                <h2 className="section-title">Отчёты на согласовании</h2>
                <p className="muted">Нужно согласовать или оспорить</p>
              </div>
              {visiblePendingReports.length === 0 ? (
                <div className="empty-linked workspace-empty">
                  <p className="muted">Сейчас нет отчётов, ожидающих вашего ответа.</p>
                </div>
              ) : (
                <div className="workspace-attention-list">
                  {visiblePendingReports.map((r) => (
                    <Link
                      key={`report-${r.id}`}
                      to={reportDetailPath(portalId, false, r.id)}
                      className="workspace-attention-card is-report"
                      onClick={() => dismiss("report", r.id, r.updated_at)}
                    >
                      <div className="workspace-attention-top">
                        <span className={`report-status-pill status-${r.status}`}>
                          {STATUS_LABEL_RU[r.status]}
                        </span>
                        <span className="muted">Отчёт</span>
                      </div>
                      <strong>{reportTitle(r)}</strong>
                      <span className="muted">Открыть и ответить</span>
                    </Link>
                  ))}
                </div>
              )}
            </section>

            <section className="workspace-focus-block">
              <div className="linked-head">
                <h2 className="section-title">Недавно завершено</h2>
                <p className="muted">Можно посмотреть итог в задаче</p>
              </div>
              {visibleRecentDone.length === 0 ? (
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
          {!agencyNeedsAttention ? (
            <div className="empty-linked workspace-empty">
              <p className="muted">
                Нет споров, клиентских задач и горящих сроков. Проекты — в панели слева.
              </p>
            </div>
          ) : (
            <>
              {visibleDisputedReports.length > 0 ? (
                <section className="workspace-focus-block workspace-dispute-section">
                  <div className="linked-head">
                    <div className="workspace-dispute-title-row">
                      <h2 className="section-title workspace-dispute-title">
                        <span className="workspace-dispute-badge" aria-hidden>
                          <DisputeIcon size={15} />
                        </span>
                        <span>На споре</span>
                      </h2>
                    </div>
                    <p className="muted">Клиент оспорил отчёт — нужно разобрать</p>
                  </div>
                  <div className="workspace-attention-list">
                    {visibleDisputedReports.map((r) => (
                      <Link
                        key={`dispute-${r.id}`}
                        to={reportDetailPath(portalId, true, r.id)}
                        className="workspace-attention-card is-dispute"
                        onClick={() => dismiss("report", r.id, r.updated_at)}
                      >
                        <div className="workspace-attention-top">
                          <span className="workspace-dispute-pill">Оспорен</span>
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
                    <h2 className="section-title">От клиента</h2>
                    <p className="muted">Задачи, которые поставил клиент</p>
                  </div>
                  {clientTasks.length === 0 ? (
                    <div className="empty-linked workspace-empty">
                      <p className="muted">Пока нет открытых задач от клиента.</p>
                    </div>
                  ) : (
                    <div className="workspace-attention-list">
                      {clientTasks.slice(0, 12).map((t) => (
                        <Link
                          key={t.id}
                          to={`/tasks/${t.id}`}
                          className="workspace-attention-card"
                          onClick={() => dismiss("task", t.id, t.updated_at)}
                        >
                          <div className="workspace-attention-top">
                            <span className="workspace-chip tone-client">Клиент</span>
                            <span className="muted">{STATUS_LABEL[t.status]}</span>
                          </div>
                          <strong>{t.title}</strong>
                          <span className="muted">
                            {t.project_name}
                            {t.created_by_name ? ` · ${t.created_by_name}` : ""}
                          </span>
                        </Link>
                      ))}
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
