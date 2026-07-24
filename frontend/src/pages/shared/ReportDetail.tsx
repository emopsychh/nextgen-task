import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type WorkReport } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { DisputeIcon } from "../../components/icons";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { formatDateTime, formatDuration, formatPackageHours } from "../../lib/format";
import { STATUS_LABEL } from "../../lib/status";
import {
  EVENT_LABEL,
  reportTitle,
  reportsListPath,
  STATUS_LABEL_RU,
} from "./reportHelpers";

export function ReportDetail() {
  const { portalId: routePortalId, reportId: routeReportId } = useParams();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();

  const reportId = routeReportId ? Number(routeReportId) : null;
  const [detail, setDetail] = useState<WorkReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [expandedProjects, setExpandedProjects] = useState<Set<number>>(new Set());
  const [expandedTasks, setExpandedTasks] = useState<Set<number>>(new Set());
  const [taskFilter, setTaskFilter] = useState<"all" | "with" | "without">("all");
  const [disputeComment, setDisputeComment] = useState("");
  const [selectedTasks, setSelectedTasks] = useState<Set<number>>(new Set());
  const [showDispute, setShowDispute] = useState(false);

  const portalId = useMemo(() => {
    if (routePortalId) return Number(routePortalId);
    if (detail?.portal_id) return detail.portal_id;
    if (!isAgency && portal?.id) return portal.id;
    return null;
  }, [routePortalId, detail?.portal_id, isAgency, portal?.id]);

  const listPath = reportsListPath(portalId, isAgency);

  const loadDetail = useCallback(async () => {
    if (!token || !reportId) return;
    const data = await api<WorkReport>(`/api/reports/${reportId}/`, {}, token);
    setDetail(data);
    setExpandedTasks(new Set());
    setTaskFilter("all");
    setShowDispute(false);
    setDisputeComment("");
    setSelectedTasks(new Set());
    const firstId = data.projects_detail?.[0]?.id;
    setExpandedProjects(firstId ? new Set([firstId]) : new Set());
  }, [token, reportId]);

  useEffect(() => {
    if (!reportId) return;
    void loadDetail().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [reportId, loadDetail]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: !!portalId && !!reportId,
    onEvent: (payload) => {
      if (payload?.kind?.startsWith("report_") || !payload?.kind) {
        void loadDetail().catch(() => undefined);
      }
    },
  });

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
      setShowDispute(false);
      setDisputeComment("");
      setSelectedTasks(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Действие не выполнено");
    } finally {
      setBusy(false);
    }
  }

  function toggleExpand(id: number) {
    setExpandedProjects((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleTaskExpand(id: number) {
    setExpandedTasks((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleTask(id: number) {
    setSelectedTasks((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const allDisputeTasks = useMemo(
    () =>
      detail?.projects_detail?.flatMap((p) =>
        p.tasks.map((t) => ({ ...t, projectName: p.name }))
      ) || [],
    [detail?.projects_detail]
  );

  function toggleAllDisputeTasks() {
    setSelectedTasks((prev) => {
      if (allDisputeTasks.length > 0 && prev.size === allDisputeTasks.length) {
        return new Set();
      }
      return new Set(allDisputeTasks.map((t) => t.id));
    });
  }

  async function submitDispute() {
    if (!disputeComment.trim() || selectedTasks.size === 0) {
      setError("Выберите задачи и напишите комментарий");
      return;
    }
    await runAction(
      "dispute",
      {
        client_comment: disputeComment.trim(),
        task_ids: Array.from(selectedTasks),
      },
      "Отчёт оспорен",
      "Агентство получит список вопросов"
    );
  }

  if (!reportId) {
    return (
      <div className="tasks-page">
        <p className="muted">Отчёт не найден.</p>
        <Link to={listPath} className="task-back">
          <span className="task-back-label">К отчётам</span>
        </Link>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="tasks-page report-detail-page">
        {error ? (
          <div className="stack" style={{ gap: 12 }}>
            <div className="error-banner">{error}</div>
            <Link to={listPath} className="task-back">
              <span className="task-back-label">К отчётам</span>
            </Link>
          </div>
        ) : (
          <p className="muted">Загрузка отчёта…</p>
        )}
      </div>
    );
  }

  return (
    <div className="tasks-page report-detail-page">
      <Link to={listPath} className="task-back" title="К отчётам">
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
        <span className="task-back-label">К отчётам</span>
      </Link>

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <div className="report-detail-panel is-page">
        <div className="report-detail-head">
          <div className="report-detail-head-text">
            <div className="report-detail-badges">
              <span className={`report-status-pill status-${detail.status}`}>
                {STATUS_LABEL_RU[detail.status]}
              </span>
              <span className="report-detail-date">
                Создан {formatDateTime(detail.created_at)}
              </span>
            </div>
            <h1 className="report-detail-title">{reportTitle(detail)}</h1>
            {(detail.project_names || []).length > 1 ? (
              <p className="report-detail-projects">
                {(detail.project_names || []).join(" · ")}
              </p>
            ) : null}
          </div>
          <div className="report-actions">
            {isAgency && detail.status === "draft" ? (
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy}
                onClick={() =>
                  void runAction("send", undefined, "Отправлено", "Ждём ответа клиента")
                }
              >
                Отправить клиенту
              </button>
            ) : null}
            {isAgency && detail.status === "disputed" ? (
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    "reopen",
                    undefined,
                    "Снова на рассмотрении",
                    "Можно отправить повторно"
                  )
                }
              >
                Вернуть на рассмотрение
              </button>
            ) : null}
            {isAgency && detail.status === "accepted" ? (
              <button
                type="button"
                className="btn btn-accent"
                disabled={busy}
                onClick={() =>
                  void runAction("mark_paid", undefined, "Оплачен", "Отчёт в архиве")
                }
              >
                Отметить оплаченным
              </button>
            ) : null}
            {!isAgency && detail.status === "pending_client" ? (
              <>
                <button
                  type="button"
                  className="btn btn-accent"
                  disabled={busy}
                  onClick={() =>
                    void runAction("accept", undefined, "Согласовано", "Спасибо!")
                  }
                >
                  Согласен
                </button>
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={busy}
                  onClick={() => setShowDispute((v) => !v)}
                >
                  {showDispute ? "Отмена" : "Оспорить"}
                </button>
              </>
            ) : null}
          </div>
        </div>

        <div className="report-stat-row">
          <div className="report-stat-card is-hours">
            <span className="report-stat-label">Затрачено</span>
            <strong className="report-stat-value">
              {formatDuration(detail.total_tracked_seconds)}
            </strong>
          </div>
          <div className="report-stat-card">
            <span className="report-stat-label">Проекты</span>
            <strong className="report-stat-value">
              {detail.projects_count || detail.project_names?.length || 0}
            </strong>
          </div>
          <div className="report-stat-card">
            <span className="report-stat-label">Задачи</span>
            <strong className="report-stat-value">
              {(detail.projects_detail || []).reduce((n, p) => n + p.tasks.length, 0)}
            </strong>
          </div>
          {detail.deal_hours ? (
            <div className="report-stat-card is-deal">
              <span className="report-stat-label">Остаток по сделке</span>
              <strong className="report-stat-value report-stat-value-sm">
                {formatPackageHours(detail.deal_hours.remaining_hours)}
              </strong>
              <span className="report-stat-hint">
                из {formatPackageHours(detail.deal_hours.paid_hours)}
              </span>
            </div>
          ) : null}
        </div>

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

        {showDispute ? (
          <div className="report-dispute-form">
            <div className="report-dispute-form-head">
              <span className="report-dispute-form-badge" aria-hidden>
                <DisputeIcon size={15} />
              </span>
              <div>
                <h3 className="report-dispute-form-title">Оспорить отчёт</h3>
                <p className="muted report-dispute-form-sub">
                  Отметьте задачи с вопросами и коротко опишите претензию
                </p>
              </div>
            </div>

            <div className="report-dispute-tasks-head">
              <span className="report-dispute-tasks-label">
                Задачи
                {selectedTasks.size > 0 ? (
                  <span className="report-dispute-count"> · выбрано {selectedTasks.size}</span>
                ) : null}
              </span>
              {allDisputeTasks.length > 0 ? (
                <button
                  type="button"
                  className="report-dispute-select-all"
                  onClick={toggleAllDisputeTasks}
                >
                  {selectedTasks.size === allDisputeTasks.length
                    ? "Снять все"
                    : "Выбрать все"}
                </button>
              ) : null}
            </div>

            {allDisputeTasks.length === 0 ? (
              <p className="muted report-dispute-empty">В отчёте пока нет задач.</p>
            ) : (
              <ul className="report-dispute-task-list">
                {allDisputeTasks.map((t) => {
                  const checked = selectedTasks.has(t.id);
                  return (
                    <li key={t.id}>
                      <label
                        className={`report-dispute-task${checked ? " is-checked" : ""}`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleTask(t.id)}
                        />
                        <span className="report-dispute-task-body">
                          <strong>{t.title}</strong>
                          <span className="muted">{t.projectName}</span>
                        </span>
                        <span className="report-dispute-task-time">
                          {formatDuration(t.tracked_seconds)}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}

            <div className="field report-dispute-comment">
              <label htmlFor="dispute-comment">Комментарий</label>
              <textarea
                id="dispute-comment"
                rows={4}
                value={disputeComment}
                onChange={(e) => setDisputeComment(e.target.value)}
                placeholder="Что не так? Что нужно переделать?"
              />
            </div>

            <div className="report-dispute-actions">
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => {
                  setShowDispute(false);
                  setDisputeComment("");
                  setSelectedTasks(new Set());
                }}
              >
                Отмена
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={
                  busy || selectedTasks.size === 0 || !disputeComment.trim()
                }
                onClick={() => void submitDispute()}
              >
                Отправить спор
              </button>
            </div>
          </div>
        ) : null}

        <div className="report-section">
          <div className="report-section-head">
            <h2 className="report-section-title">Проекты и итоги</h2>
            <div className="report-task-filters" role="group" aria-label="Фильтр задач">
              {(
                [
                  { id: "all", label: "Все" },
                  { id: "with", label: "С итогом" },
                  { id: "without", label: "Без итога" },
                ] as const
              ).map((f) => (
                <button
                  key={f.id}
                  type="button"
                  className={`report-mini-chip${taskFilter === f.id ? " active" : ""}`}
                  onClick={() => setTaskFilter(f.id)}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>
          <div className="report-project-blocks">
            {(detail.projects_detail || []).map((block) => {
              const open = expandedProjects.has(block.id);
              const withOutcome = block.tasks.filter((t) => t.outcome?.trim()).length;
              const visibleTasks = block.tasks.filter((t) => {
                const has = Boolean(t.outcome?.trim());
                if (taskFilter === "with") return has;
                if (taskFilter === "without") return !has;
                return true;
              });
              return (
                <article
                  key={block.id}
                  className={`report-project-block${open ? " is-open" : ""}`}
                >
                  <button
                    type="button"
                    className="report-project-block-head"
                    onClick={() => toggleExpand(block.id)}
                  >
                    <span className="report-project-block-title">{block.name}</span>
                    <span className="report-project-block-meta">
                      <span className="report-project-hours">
                        {formatDuration(block.total_tracked_seconds)}
                      </span>
                      <span>
                        {withOutcome}/{block.tasks.length} с итогом
                      </span>
                      <span className="report-task-chevron">{open ? "▾" : "▸"}</span>
                    </span>
                  </button>
                  {open ? (
                    <div className="report-project-block-body">
                      {visibleTasks.length === 0 ? (
                        <p className="muted report-tasks-empty">Нет задач в этом фильтре</p>
                      ) : (
                        <div className="report-task-table" role="table">
                          <div className="report-task-table-head" role="row">
                            <span>Задача</span>
                            <span>Итог</span>
                            <span>Статус</span>
                            <span>Время</span>
                          </div>
                          {visibleTasks.map((t) => {
                            const taskOpen = expandedTasks.has(t.id);
                            const outcome = (t.outcome || "").trim();
                            return (
                              <div
                                key={t.id}
                                className={`report-task-row${taskOpen ? " is-open" : ""}${
                                  outcome ? " has-outcome" : ""
                                }`}
                                role="row"
                              >
                                <button
                                  type="button"
                                  className="report-task-row-main"
                                  onClick={() => toggleTaskExpand(t.id)}
                                >
                                  <span className="report-task-name">
                                    <Link
                                      to={`/tasks/${t.id}`}
                                      onClick={(e) => e.stopPropagation()}
                                    >
                                      {t.title}
                                    </Link>
                                  </span>
                                  <span
                                    className={`report-task-outcome-preview${
                                      outcome ? "" : " is-empty"
                                    }`}
                                  >
                                    {outcome
                                      ? outcome.length > 90
                                        ? `${outcome.slice(0, 90)}…`
                                        : outcome
                                      : "Итог не указан"}
                                  </span>
                                  <span className="report-task-status">
                                    {STATUS_LABEL[t.status]}
                                  </span>
                                  <span className="report-task-time">
                                    {formatDuration(t.tracked_seconds)}
                                  </span>
                                </button>
                                {taskOpen ? (
                                  <div className="report-task-row-detail">
                                    {outcome ? (
                                      <p className="report-outcome-text">{outcome}</p>
                                    ) : (
                                      <p className="muted report-outcome-empty">
                                        Итог не указан
                                        {t.status !== "done"
                                          ? " — задача ещё не завершена"
                                          : " — можно дописать в карточке задачи"}
                                      </p>
                                    )}
                                    <Link
                                      className="report-task-open-link"
                                      to={`/tasks/${t.id}`}
                                    >
                                      Открыть задачу →
                                    </Link>
                                  </div>
                                ) : null}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        </div>

        {detail.events && detail.events.length > 0 ? (
          <div className="report-section">
            <h2 className="report-section-title">История согласования</h2>
            <ol className="report-timeline">
              {detail.events.map((ev, idx) => (
                <li key={ev.id} className="report-timeline-item">
                  <span className="report-timeline-dot" aria-hidden />
                  {idx < detail.events!.length - 1 ? (
                    <span className="report-timeline-line" aria-hidden />
                  ) : null}
                  <div className="report-timeline-body">
                    <strong className="report-timeline-kind">
                      {EVENT_LABEL[ev.kind] || ev.kind}
                    </strong>
                    <span className="report-timeline-meta">
                      {ev.actor_name || "Участник"} · {formatDateTime(ev.created_at)}
                    </span>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </div>
    </div>
  );
}
