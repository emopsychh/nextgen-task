import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import {
  api,
  apiBlob,
  isAbortError,
  type WorkReport,
  type WorkReportTaskRow,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { FileGlyph } from "../../components/icons";
import { SmartBackButton } from "../../components/SmartBackButton";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import {
  formatDateTime,
  formatDuration,
  formatPackageHours,
} from "../../lib/format";
import { linkStateFrom } from "../../lib/smartBack";
import { readPortalCache, writePortalCache } from "../../lib/portalSessionCache";
import { STATUS_LABEL } from "../../lib/status";
import {
  EVENT_LABEL,
  reportPackageFill,
  reportSheetTitle,
  reportsListPath,
  STATUS_LABEL_RU,
} from "./reportHelpers";

type FlatTask = WorkReportTaskRow & { projectId: number; projectName: string };

function tasksWord(n: number): string {
  const n10 = n % 10;
  const n100 = n % 100;
  if (n100 >= 11 && n100 <= 14) return "задач";
  if (n10 === 1) return "задача";
  if (n10 >= 2 && n10 <= 4) return "задачи";
  return "задач";
}

function taskStatusMeta(task: WorkReportTaskRow): { label: string; tone: string } {
  if (task.disputed || task.awaiting_client) {
    return { label: "На согласовании", tone: "review" };
  }
  if (task.status === "done") return { label: "Выполнено", tone: "done" };
  if (task.status === "in_progress") return { label: "В работе", tone: "progress" };
  return { label: STATUS_LABEL[task.status], tone: "todo" };
}

function reportStatusContext(status: WorkReport["status"], isAgency: boolean): string {
  if (status === "draft") {
    return "Завершённые задачи попадают в отчёт сами. Проверьте итоги и отправьте клиенту.";
  }
  if (status === "pending_client") {
    return isAgency ? "Отчёт отправлен клиенту и ожидает согласования." : "";
  }
  if (status === "disputed") {
    return isAgency
      ? "Клиент оставил замечания. Проверьте отмеченные задачи и верните отчёт на рассмотрение."
      : "Замечания отправлены менеджеру. История останется доступна в этом отчёте.";
  }
  if (status === "dismissed") return "Отчёт снят с контроля и сохранён в истории.";
  return "Работы по отчёту согласованы. Документ доступен для просмотра и скачивания.";
}

function MetricIcon({ kind }: { kind: "deal" | "pack" | "used" | "left" }) {
  if (kind === "deal") {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.8" />
        <path d="M14 3v6h6M9 13h6M9 17h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  }
  if (kind === "pack") {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
        <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.8" />
        <path d="M12 8v4.5L15 15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  }
  if (kind === "used") {
    return (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
        <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.8" />
        <circle cx="12" cy="12" r="1.4" fill="currentColor" />
        <path d="M12 7v5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 4a8 8 0 0 1 8 8H12V4Z" fill="currentColor" opacity="0.22" />
    </svg>
  );
}

export function ReportDetail() {
  const { portalId: routePortalId, reportId: routeReportId } = useParams();
  const location = useLocation();
  const fromState = linkStateFrom(location);
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();

  const reportId = routeReportId ? Number(routeReportId) : null;
  const [detail, setDetail] = useState<WorkReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const portalId = useMemo(() => {
    if (routePortalId) return Number(routePortalId);
    if (detail?.portal_id) return detail.portal_id;
    if (!isAgency && portal?.id) return portal.id;
    return null;
  }, [routePortalId, detail?.portal_id, isAgency, portal?.id]);

  const listPath = reportsListPath(portalId, isAgency);

  const loadDetail = useCallback(
    async (signal?: AbortSignal) => {
      if (!token || !reportId) return;
      const data = await api<WorkReport>(`/api/reports/${reportId}/`, { signal }, token);
      if (signal?.aborted) return;
      setDetail(data);
    },
    [token, reportId]
  );

  useEffect(() => {
    if (!reportId) return;
    const cached = readPortalCache<WorkReport>("report-detail", reportId);
    setDetail(cached);
    setError(null);
    const ac = new AbortController();
    void loadDetail(ac.signal).catch((e) => {
      if (!isAbortError(e)) setError(e instanceof Error ? e.message : "Ошибка");
    });
    return () => ac.abort();
  }, [reportId, loadDetail]);

  useEffect(() => {
    if (reportId && detail?.id === reportId) {
      writePortalCache("report-detail", reportId, detail);
    }
  }, [detail, reportId]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: !!portalId && !!reportId,
    onEvent: (payload) => {
      if (payload?.kind?.startsWith("report_") || payload?.kind === "task_update") {
        void loadDetail().catch(() => undefined);
      }
    },
  });

  const allTasks = useMemo<FlatTask[]>(
    () =>
      detail?.projects_detail?.flatMap((block) =>
        block.tasks.map((task) => ({
          ...task,
          projectId: block.id,
          projectName: block.name,
        }))
      ) || [],
    [detail?.projects_detail]
  );

  const hours = useMemo(() => {
    if (!detail) {
      return {
        deal: null,
        paid: null,
        leftover: null,
        reportSeconds: 0,
        taskSeconds: 0,
        used: 0,
        overage: 0,
        carried: 0,
        usedPct: null,
        isFull: false,
      };
    }
    const fill = reportPackageFill(detail);
    return {
      deal: detail.deal_hours || null,
      paid: fill.paid,
      leftover: fill.leftover,
      reportSeconds: detail.total_tracked_seconds || 0,
      taskSeconds: detail.task_tracked_seconds ?? detail.total_tracked_seconds ?? 0,
      used: fill.used,
      overage: fill.overage,
      carried: fill.carried,
      usedPct: fill.usedPct,
      isFull: fill.isFull,
    };
  }, [detail]);

  const sendBlockReason = useMemo(() => {
    if (!detail || detail.status !== "draft") return null;
    const selectedCount = detail.selected_task_ids?.length ?? detail.tasks_count ?? 0;
    if (selectedCount === 0) return "Закройте хотя бы одну задачу — она появится в отчёте сама.";
    if (hours.paid == null) return "В сделке не указан размер пакета.";
    if (!hours.isFull && hours.leftover != null) {
      return `В отчёте ещё не весь пакет. Закройте задачи на ${formatPackageHours(hours.leftover)}.`;
    }
    return null;
  }, [detail, hours.paid, hours.isFull, hours.leftover]);

  async function runAction(
    path: string,
    body?: Record<string, unknown>,
    okTitle?: string,
    okMsg?: string
  ) {
    if (!token || !reportId) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api<WorkReport>(
        `/api/reports/${reportId}/${path}/`,
        {
          method: "POST",
          body: body ? JSON.stringify(body) : "{}",
        },
        token
      );
      setDetail(updated);
      if (okTitle) toast.show(okMsg || "", okTitle);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Действие не выполнено");
    } finally {
      setBusy(false);
    }
  }

  async function downloadPdf() {
    if (!token || !reportId) return;
    setBusy(true);
    setError(null);
    try {
      const { blob, filename } = await apiBlob(`/api/reports/${reportId}/pdf/`, {}, token);
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename || `report-${reportId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
      toast.show("Файл сохранён", "PDF скачан");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось скачать PDF");
    } finally {
      setBusy(false);
    }
  }

  if (!reportId) {
    return (
      <div className="tasks-page report-detail-page">
        <p className="muted">Отчёт не найден.</p>
        <SmartBackButton fallback={listPath} className="task-back">
          <span className="task-back-label">Назад</span>
        </SmartBackButton>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="tasks-page report-detail-page">
        {error ? (
          <div className="stack" style={{ gap: 12 }}>
            <div className="error-banner">{error}</div>
            <SmartBackButton fallback={listPath} className="task-back">
              <span className="task-back-label">Назад</span>
            </SmartBackButton>
          </div>
        ) : (
          <p className="muted">Загрузка отчёта…</p>
        )}
      </div>
    );
  }

  const events = [...(detail.events || [])].reverse();
  const statusLead = reportStatusContext(detail.status, isAgency);
  const note =
    detail.client_comment?.trim() ||
    (hours.deal
      ? `Отчёт сформирован по пакету часов сделки «${detail.deal_title}» №${detail.deal_id}. Сюда сами попадают завершённые задачи.`
      : "Отчёт включает задачи выбранных проектов и зафиксированное по ним время.");

  return (
    <div className="tasks-page report-detail-page">
      <SmartBackButton fallback={listPath} className="task-back" title="Назад">
        <span className="task-back-icon" aria-hidden>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <path
              d="M15 6 9 12l6 6"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <span className="task-back-label">Назад</span>
      </SmartBackButton>

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <article className="report-sheet">
        <header className="report-sheet-head">
          <div className="report-sheet-head-text">
            <div className="report-detail-badges">
              <span className={`report-status-pill status-${detail.status}`}>
                {STATUS_LABEL_RU[detail.status]}
              </span>
              <span className="report-detail-date">Создан {formatDateTime(detail.created_at)}</span>
            </div>
            <h1 className="report-detail-title">{reportSheetTitle(detail)}</h1>
            {statusLead ? <p className="report-detail-lead">{statusLead}</p> : null}
          </div>
          <div className="report-actions">
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => void downloadPdf()}>
              Скачать PDF
            </button>
            {isAgency && detail.status === "draft" ? (
              <div className="report-send-control">
                <button
                  type="button"
                  className="btn btn-accent"
                  disabled={busy || Boolean(sendBlockReason)}
                  onClick={() => void runAction("send", undefined, "Отправлено", "Ждём ответа клиента")}
                >
                  Отправить клиенту
                </button>
                {sendBlockReason ? <span>{sendBlockReason}</span> : null}
              </div>
            ) : null}
            {isAgency && detail.status === "disputed" ? (
              <>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() =>
                    void runAction("reopen", undefined, "Снова на рассмотрении", "Можно отправить повторно")
                  }
                >
                  Вернуть на рассмотрение
                </button>
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={busy}
                  onClick={() => void runAction("dismiss", undefined, "Снято с контроля", "Можно создать новый отчёт")}
                >
                  Снять с контроля
                </button>
              </>
            ) : null}
            {!isAgency && detail.status === "pending_client" ? (
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy}
                onClick={() => void runAction("accept", undefined, "Согласовано", "Спасибо!")}
              >
                Согласовать
              </button>
            ) : null}
          </div>
        </header>

        <div className="report-sheet-grid">
          <div className="report-sheet-main">
            <div className="report-metric-grid">
              <div className="report-metric">
                <span className="report-metric-icon is-deal">
                  <MetricIcon kind="deal" />
                </span>
                <div>
                  <strong>{detail.deal_id ? `Сделка №${detail.deal_id}` : "Сделка"}</strong>
                  <span>{detail.deal_title || "Пакет часов по сделке"}</span>
                </div>
              </div>
              <div className="report-metric">
                <span className="report-metric-icon is-pack">
                  <MetricIcon kind="pack" />
                </span>
                <div>
                  <strong>{hours.paid != null ? formatPackageHours(hours.paid) : "—"}</strong>
                  <span>Всего по сделке</span>
                </div>
              </div>
              <div className="report-metric">
                <span className="report-metric-icon is-used">
                  <MetricIcon kind="used" />
                </span>
                <div>
                  <strong>{formatPackageHours(hours.used)}</strong>
                  <span>В отчёте</span>
                </div>
              </div>
              <div className="report-metric">
                <span className="report-metric-icon is-left">
                  <MetricIcon kind="left" />
                </span>
                <div>
                  <strong>
                    {hours.leftover != null ? formatPackageHours(hours.leftover) : "—"}
                  </strong>
                  <span>Осталось закрыть</span>
                </div>
              </div>
            </div>

            {hours.overage > 0 ? (
              <p className="report-overage-hint">
                Перерасход {formatPackageHours(hours.overage)} уйдёт в следующий пакет.
              </p>
            ) : null}
            {hours.carried > 0 ? (
              <p className="report-overage-hint">
                В отчёт уже вошло {formatPackageHours(hours.carried)} перерасхода с прошлой сделки.
              </p>
            ) : null}

            {hours.usedPct != null && hours.paid != null ? (
              <div className="report-pack-bar">
                <div className="report-pack-bar-copy">
                  <strong>{Math.round(hours.usedPct)}% пакета в отчёте</strong>
                  <span>
                    {hours.used != null ? formatPackageHours(hours.used) : "—"} из {formatPackageHours(hours.paid)}
                  </span>
                </div>
                <div
                  className="report-pack-track"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(hours.usedPct)}
                >
                  <span style={{ width: `${Math.max(hours.usedPct, hours.reportSeconds > 0 ? 2 : 0)}%` }} />
                </div>
              </div>
            ) : null}

            {detail.status === "disputed" && detail.client_comment ? (
              <div className="report-dispute-banner">
                <strong>Комментарий клиента:</strong> {detail.client_comment}
                {detail.dispute_items && detail.dispute_items.length > 0 ? (
                  <ul>
                    {detail.dispute_items.map((item) => (
                      <li key={item.id}>
                        {item.task_title}
                        {item.note ? ` — ${item.note}` : ""}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}

            <section className="report-table-card">
              <div className="report-section-head">
                <h2 className="report-section-title">
                  {detail.status === "disputed" ? "Задачи к обсуждению" : "Завершённые задачи"}
                </h2>
                <div className="report-table-tools">
                  {detail.status === "disputed" ? (
                    <p className="muted report-dispute-scope-hint">
                      Показаны только задачи, которые клиент отметил для обсуждения
                    </p>
                  ) : null}
                  <span className="report-table-count">
                    {allTasks.length} {tasksWord(allTasks.length)}
                  </span>
                </div>
              </div>

              {allTasks.length === 0 ? (
                <p className="muted report-tasks-empty">
                  {detail.status === "draft"
                    ? "Закройте задачу — она появится здесь сама."
                    : "В отчёте пока нет задач"}
                </p>
              ) : (
                <div className="report-task-list">
                    {allTasks.map((task) => {
                      const outcome = (task.outcome || "").trim();
                      const status = taskStatusMeta(task);
                      const files = task.files || [];
                      return (
                        <article
                          key={task.id}
                          className={`report-task-item${task.disputed || task.awaiting_client ? " is-review" : ""}`}
                        >
                          <header className="report-task-item-head">
                            <div className="report-task-item-title">
                              <Link to={`/tasks/${task.id}`} state={fromState}>
                                {task.title}
                              </Link>
                              <Link to={`/projects/${task.projectId}`} state={fromState}>
                                {task.projectName}
                              </Link>
                            </div>
                            <div className="report-task-item-meta">
                              <span className={`report-dot-status is-${status.tone}`}>
                                <i aria-hidden />
                                {status.label}
                              </span>
                              <strong>{formatDuration(task.tracked_seconds)}</strong>
                            </div>
                          </header>
                          <div className="report-task-item-body">
                            <section className={`report-task-result${outcome ? "" : " is-empty"}`}>
                              <span>Итог работы</span>
                              <p>{outcome || "Итог пока не указан"}</p>
                            </section>
                            <section className="report-task-files">
                              <span>Файлы к результату</span>
                              {files.length ? (
                                <ul className="report-result-files">
                                  {files.map((file) => (
                                    <li key={file.id}>
                                      <a
                                        href={file.url}
                                        className="report-file-link"
                                        target="_blank"
                                        rel="noreferrer"
                                      >
                                        <FileGlyph />
                                        <span>{file.name}</span>
                                      </a>
                                    </li>
                                  ))}
                                </ul>
                              ) : (
                                <p className="muted">Нет файлов</p>
                              )}
                            </section>
                          </div>
                        </article>
                      );
                    })}
                  <footer className="report-task-list-total">
                    <span>Итого по задачам</span>
                    <strong>{formatDuration(hours.taskSeconds)}</strong>
                  </footer>
                  {hours.carried > 0 ? (
                    <footer className="report-task-list-total">
                      <span>Перерасход с прошлой сделки</span>
                      <strong>{formatPackageHours(hours.carried)}</strong>
                    </footer>
                  ) : null}
                </div>
              )}
            </section>
          </div>

          <aside className="report-sheet-aside">
            {events.length > 0 ? (
              <section className="report-aside-card">
                <h3>История согласования</h3>
                <ol className="report-aside-timeline">
                  {events.map((event, idx) => (
                    <li key={event.id} className={idx === 0 ? "is-latest" : ""}>
                      <span className="report-aside-dot" aria-hidden />
                      <div>
                        <strong>{EVENT_LABEL[event.kind] || event.kind}</strong>
                        <span>
                          {event.actor_name || "система"} · {formatDateTime(event.created_at)}
                        </span>
                      </div>
                    </li>
                  ))}
                </ol>
              </section>
            ) : null}

            <section className="report-aside-card report-note-card">
              <h3>Комментарий к отчёту</h3>
              <p>{note}</p>
            </section>
          </aside>
        </div>
      </article>
    </div>
  );
}
