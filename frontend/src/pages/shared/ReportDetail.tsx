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
import {
  reportPackageFill,
  reportTitle,
  reportsListPath,
  STATUS_LABEL_RU,
} from "./reportHelpers";

type FlatTask = WorkReportTaskRow & { projectId: number; projectName: string };

function reportStatusTone(status: WorkReport["status"]): string {
  if (status === "accepted" || status === "paid" || status === "dismissed") return "status-done";
  if (status === "pending_client") return "status-progress";
  if (status === "disputed") return "status-overdue";
  return "status-todo";
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

  const hoursLabel =
    hours.paid != null
      ? `${formatPackageHours(hours.used)} из ${formatPackageHours(hours.paid)}`
      : formatPackageHours(hours.used);

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

      <header className="report-simple-head">
        <div>
          <div className="task-compact-heading">
            <h1>{reportTitle(detail)}</h1>
            <span className={`task-status-pill ${reportStatusTone(detail.status)}`}>
              {STATUS_LABEL_RU[detail.status]}
            </span>
          </div>
          <p className="muted">
            Сделка №{detail.deal_id} · {hoursLabel}
            {hours.leftover != null && hours.leftover > 0
              ? ` · осталось ${formatPackageHours(hours.leftover)}`
              : ""}
            {" · "}
            {formatDateTime(detail.created_at)}
          </p>
          {hours.overage > 0 ? (
            <p className="muted">Перерасход {formatPackageHours(hours.overage)} уйдёт в следующий пакет.</p>
          ) : null}
        </div>
        <div className="report-simple-actions">
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

      {detail.client_comment?.trim() || detail.dispute_items?.length ? (
        <p className="report-simple-note">
          {detail.client_comment?.trim()}
          {detail.dispute_items?.length
            ? `${detail.client_comment?.trim() ? " · " : ""}${detail.dispute_items
                .map((item) => item.task_title)
                .join(", ")}`
            : ""}
        </p>
      ) : null}

      {allTasks.length === 0 ? (
        <p className="muted">
          {detail.status === "draft"
            ? "Закройте задачу — она появится здесь сама."
            : "В отчёте пока нет задач"}
        </p>
      ) : (
        <div className="task-list-group">
          {allTasks.map((task) => {
            const outcome = (task.outcome || "").trim();
            const files = task.files || [];
            return (
              <article key={task.id} className="board-row task-card">
                <div className="board-row-main">
                  <div className="task-compact-heading">
                    <Link to={`/tasks/${task.id}`} state={fromState} className="board-row-title task-card-title">
                      {task.title}
                    </Link>
                    {task.status === "done" ? (
                      <span className="task-status-pill status-done">Готово</span>
                    ) : task.status === "in_progress" ? (
                      <span className="task-status-pill status-progress">В работе</span>
                    ) : null}
                  </div>
                  <span className="board-row-note muted">
                    {task.projectName}
                    {outcome ? ` · ${outcome}` : ""}
                  </span>
                  {files.length ? (
                    <span className="report-file-line">
                      {files.map((file) => (
                        <a key={file.id} href={file.url} target="_blank" rel="noreferrer">
                          {file.name}
                        </a>
                      ))}
                    </span>
                  ) : null}
                </div>
                <div className="task-compact-side">
                  <span className="board-meta-due">
                    <strong>{formatDuration(task.tracked_seconds)}</strong>
                  </span>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
