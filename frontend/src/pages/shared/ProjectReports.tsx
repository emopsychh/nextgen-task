import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  unwrapList,
  type Paginated,
  type Project,
  type WorkReport,
  type WorkReportStatus,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { formatDateTime, formatDuration, formatPackageHours } from "../../lib/format";
import { STATUS_LABEL } from "../../lib/status";

type Bucket = "current" | "review" | "paid";

const STATUS_LABEL_RU: Record<WorkReportStatus, string> = {
  draft: "Черновик",
  pending_client: "На рассмотрении",
  disputed: "Оспорен",
  accepted: "Согласован",
  paid: "Оплачен",
};

const EVENT_LABEL: Record<string, string> = {
  created: "Создан",
  sent: "Отправлен клиенту",
  accepted: "Клиент согласился",
  disputed: "Клиент оспорил",
  paid: "Отмечен оплаченным",
  reopened: "Вернут в черновик",
};

const BUCKETS: { id: Bucket; label: string }[] = [
  { id: "current", label: "Актуальные" },
  { id: "review", label: "На рассмотрении" },
  { id: "paid", label: "Оплаченные" },
];

function reportTitle(r: Pick<WorkReport, "id" | "project_names" | "projects_count">): string {
  const names = r.project_names || [];
  if (names.length === 0) return `Отчёт №${r.id}`;
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} и ${names[1]}`;
  return `${names[0]} и ещё ${names.length - 1}`;
}

function reportSubtitle(r: Pick<WorkReport, "project_names" | "projects_count">): string {
  const n = r.projects_count || r.project_names?.length || 0;
  if (n <= 1) return "1 проект";
  if (n >= 2 && n <= 4) return `${n} проекта`;
  return `${n} проектов`;
}

export function ProjectReports() {
  const { portalId: routePortalId, reportId: routeReportId, projectId: routeProjectId } =
    useParams();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const navigate = useNavigate();
  const toast = useFlashToast();

  const [resolvedPortalId, setResolvedPortalId] = useState<number | null>(null);

  const portalId = useMemo(() => {
    if (routePortalId) return Number(routePortalId);
    if (resolvedPortalId) return resolvedPortalId;
    if (!isAgency && portal?.id) return portal.id;
    return null;
  }, [routePortalId, resolvedPortalId, isAgency, portal?.id]);

  useEffect(() => {
    if (!token || !routeProjectId || routePortalId) return;
    let cancelled = false;
    void api<Project>(`/api/projects/${routeProjectId}/`, {}, token)
      .then((p) => {
        if (!cancelled) setResolvedPortalId(p.portal);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token, routeProjectId, routePortalId]);

  const [bucket, setBucket] = useState<Bucket>("current");
  const [reports, setReports] = useState<WorkReport[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(
    routeReportId ? Number(routeReportId) : null
  );
  const [detail, setDetail] = useState<WorkReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [pickedProjects, setPickedProjects] = useState<Set<number>>(new Set());
  const [expandedProjects, setExpandedProjects] = useState<Set<number>>(new Set());
  const [expandedTasks, setExpandedTasks] = useState<Set<number>>(new Set());
  const [taskFilter, setTaskFilter] = useState<"all" | "with" | "without">("all");
  const [disputeComment, setDisputeComment] = useState("");
  const [selectedTasks, setSelectedTasks] = useState<Set<number>>(new Set());
  const [showDispute, setShowDispute] = useState(false);
  const [counts, setCounts] = useState<Record<Bucket, number>>({
    current: 0,
    review: 0,
    paid: 0,
  });

  const loadCounts = useCallback(async () => {
    if (!token || !portalId) return;
    const next: Record<Bucket, number> = { current: 0, review: 0, paid: 0 };
    await Promise.all(
      (["current", "review", "paid"] as Bucket[]).map(async (b) => {
        const data = await api<WorkReport[] | Paginated<WorkReport>>(
          `/api/reports/?portal=${portalId}&bucket=${b}`,
          {},
          token
        );
        next[b] = unwrapList(data).length;
      })
    );
    setCounts(next);
  }, [token, portalId]);

  const loadList = useCallback(async () => {
    if (!token || !portalId) return;
    const data = await api<WorkReport[] | Paginated<WorkReport>>(
      `/api/reports/?portal=${portalId}&bucket=${bucket}`,
      {},
      token
    );
    const list = unwrapList(data);
    setReports(list);
    setSelectedId((prev) => {
      if (routeReportId) {
        const id = Number(routeReportId);
        if (list.some((r) => r.id === id)) return id;
      }
      if (prev && list.some((r) => r.id === prev)) return prev;
      return list[0]?.id ?? null;
    });
    void loadCounts();
  }, [token, portalId, bucket, routeReportId, loadCounts]);

  const loadProjects = useCallback(async () => {
    if (!token || !portalId) return;
    const data = await api<Project[] | Paginated<Project>>(
      `/api/projects/?portal=${portalId}`,
      {},
      token
    );
    setProjects(unwrapList(data));
  }, [token, portalId]);

  const loadDetail = useCallback(
    async (id: number) => {
      if (!token) return;
      const data = await api<WorkReport>(`/api/reports/${id}/`, {}, token);
      setDetail(data);
      setExpandedTasks(new Set());
      setTaskFilter("all");
      // One project open is enough to start reading without a huge scroll.
      const firstId = data.projects_detail?.[0]?.id;
      setExpandedProjects(firstId ? new Set([firstId]) : new Set());
    },
    [token]
  );

  useEffect(() => {
    if (!token || !portalId) return;
    void loadList().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
    void loadProjects().catch(() => undefined);
  }, [token, portalId, loadList, loadProjects]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    void loadDetail(selectedId).catch((e) =>
      setError(e instanceof Error ? e.message : "Ошибка")
    );
  }, [selectedId, loadDetail]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: !!portalId,
    onEvent: (payload) => {
      if (payload?.kind?.startsWith("report_") || !payload?.kind) {
        void loadList().catch(() => undefined);
        if (selectedId) void loadDetail(selectedId).catch(() => undefined);
      }
    },
  });

  async function createReport() {
    if (!token || !portalId || pickedProjects.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api<WorkReport>(
        "/api/reports/",
        {
          method: "POST",
          body: JSON.stringify({
            portal: portalId,
            project_ids: Array.from(pickedProjects),
          }),
        },
        token
      );
      toast.show("Итоги задач подтянутся автоматически", "Отчёт создан");
      setShowCreate(false);
      setPickedProjects(new Set());
      setBucket("current");
      await loadList();
      setSelectedId(created.id);
      setDetail(created);
      if (isAgency && portalId) {
        navigate(`/portals/${portalId}/reports/${created.id}`, { replace: true });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать отчёт");
    } finally {
      setBusy(false);
    }
  }

  async function runAction(
    path: string,
    body?: Record<string, unknown>,
    okTitle?: string,
    okMsg?: string
  ) {
    if (!token || !selectedId) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api<WorkReport>(
        `/api/reports/${selectedId}/${path}/`,
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
      await loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Действие не выполнено");
    } finally {
      setBusy(false);
    }
  }

  function toggleProjectPick(id: number) {
    setPickedProjects((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
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

  const allDisputeTasks =
    detail?.projects_detail?.flatMap((p) =>
      p.tasks.map((t) => ({ ...t, projectName: p.name }))
    ) || [];

  if (!portalId) {
    return (
      <div className="tasks-page">
        <p className="muted">Выберите клиента, чтобы открыть отчёты.</p>
      </div>
    );
  }

  return (
    <div className="tasks-page report-hub">
      <div className="page-header">
        <div>
          <h1 className="page-title">Отчёты</h1>
          <p className="page-sub">
            Согласование выполненных работ по проектам клиента
          </p>
        </div>
        {isAgency ? (
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => {
              setShowCreate(true);
              void loadProjects();
            }}
          >
            Создать отчёт
          </button>
        ) : null}
      </div>

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <div className="task-filters report-filter-row">
        {BUCKETS.map((b) => (
          <button
            key={b.id}
            type="button"
            className={`task-filter-chip${bucket === b.id ? " active" : ""}`}
            onClick={() => setBucket(b.id)}
          >
            {b.label}
            <span className="task-filter-count">{counts[b.id]}</span>
          </button>
        ))}
      </div>

      {showCreate && isAgency ? (
        <div className="connect-panel stack report-create-panel">
          <div>
            <h2 className="section-title">Новый отчёт</h2>
            <p className="muted">
              Выберите один или несколько проектов. Итоги задач подтянутся из карточек
              завершённых задач.
            </p>
          </div>
          <ul className="report-project-pick">
            {projects.map((p) => (
              <li key={p.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={pickedProjects.has(p.id)}
                    onChange={() => toggleProjectPick(p.id)}
                  />
                  <span>{p.name}</span>
                  <span className="muted">
                    {p.done_count}/{p.tasks_count} задач
                  </span>
                </label>
              </li>
            ))}
            {projects.length === 0 ? (
              <li className="muted">Пока нет проектов у этого клиента</li>
            ) : null}
          </ul>
          <div className="report-create-actions">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setShowCreate(false);
                setPickedProjects(new Set());
              }}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn btn-accent"
              disabled={busy || pickedProjects.size === 0}
              onClick={() => void createReport()}
            >
              Создать
            </button>
          </div>
        </div>
      ) : null}

      <div className="report-layout">
        <aside className="report-list-panel">
          <div className="report-list-heading">
            <h2 className="report-panel-title">Отчёты</h2>
            <span className="report-list-count">{reports.length}</span>
          </div>
          {reports.length === 0 ? (
            <div className="report-list-empty">
              <p>В этой вкладке пока пусто.</p>
              {isAgency && bucket === "current" ? (
                <p className="muted">Нажмите «Создать отчёт», чтобы собрать проекты.</p>
              ) : null}
            </div>
          ) : (
            <ul className="report-list">
              {reports.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className={`report-list-item${selectedId === r.id ? " is-active" : ""}`}
                    onClick={() => {
                      setSelectedId(r.id);
                      if (isAgency && portalId) {
                        navigate(`/portals/${portalId}/reports/${r.id}`, { replace: true });
                      } else if (!isAgency) {
                        navigate(`/reports/${r.id}`, { replace: true });
                      }
                    }}
                  >
                    <div className="report-list-item-top">
                      <span className={`report-status-pill status-${r.status}`}>
                        {STATUS_LABEL_RU[r.status]}
                      </span>
                      <span className="report-list-hours">
                        {formatDuration(r.total_tracked_seconds)}
                      </span>
                    </div>
                    <strong className="report-list-title">{reportTitle(r)}</strong>
                    <span className="report-list-sub">
                      {reportSubtitle(r)} · {formatDateTime(r.created_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="report-detail-panel">
          {!detail ? (
            <div className="report-detail-empty">
              <h2 className="report-panel-title">Выберите отчёт</h2>
              <p className="muted">Слева список — справа содержимое и согласование.</p>
            </div>
          ) : (
            <>
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
                  <h2 className="report-detail-title">{reportTitle(detail)}</h2>
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
                          "Снова черновик",
                          "Можно отправить повторно"
                        )
                      }
                    >
                      Вернуть в черновик
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
                <div className="connect-panel stack report-dispute-form">
                  <h3 className="section-title">Оспорить отчёт</h3>
                  <p className="muted">Выберите задачи с вопросами и опишите претензию.</p>
                  <div className="field">
                    <label>Комментарий</label>
                    <textarea
                      rows={3}
                      value={disputeComment}
                      onChange={(e) => setDisputeComment(e.target.value)}
                      placeholder="Что не так?"
                    />
                  </div>
                  <ul className="report-task-checkboxes">
                    {allDisputeTasks.map((t) => (
                      <li key={t.id}>
                        <label>
                          <input
                            type="checkbox"
                            checked={selectedTasks.has(t.id)}
                            onChange={() => toggleTask(t.id)}
                          />
                          <span>
                            {t.title}
                            <span className="muted"> · {t.projectName}</span>
                          </span>
                          <span className="muted">{formatDuration(t.tracked_seconds)}</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={busy}
                    onClick={() => void submitDispute()}
                    style={{ alignSelf: "start" }}
                  >
                    Отправить спор
                  </button>
                </div>
              ) : null}

              <div className="report-section">
                <div className="report-section-head">
                  <h3 className="report-section-title">Проекты и итоги</h3>
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
                              <p className="muted report-tasks-empty">
                                Нет задач в этом фильтре
                              </p>
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
                  <h3 className="report-section-title">История согласования</h3>
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
            </>
          )}
        </section>
      </div>
    </div>
  );
}
