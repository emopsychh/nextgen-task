import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  API_BASE,
  api,
  unwrapList,
  type Paginated,
  type Project,
  type WorkReport,
  type WorkReportStatus,
  type WorkReportTaskRow,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { formatDateTime, formatDuration, formatPackageHours } from "../../lib/format";
import { STATUS_LABEL, STATUS_TONE } from "../../lib/status";

const STATUS_LABEL_RU: Record<WorkReportStatus, string> = {
  draft: "Черновик",
  pending_client: "На согласовании у клиента",
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

function ReportTaskCard({
  task,
  editable,
  token,
  reportId,
  onUpdated,
  onError,
}: {
  task: WorkReportTaskRow;
  editable: boolean;
  token: string;
  reportId: number;
  onUpdated: (report: WorkReport) => void;
  onError: (msg: string) => void;
}) {
  const [workDone, setWorkDone] = useState(task.work_done || "");
  const [expanded, setExpanded] = useState(
    Boolean(task.work_done) || (task.attachments && task.attachments.length > 0) || editable
  );
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const saveTimer = useRef<number | null>(null);

  useEffect(() => {
    setWorkDone(task.work_done || "");
  }, [task.work_done, task.id]);

  function scheduleSave(next: string) {
    if (!editable) return;
    if (saveTimer.current != null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void persist(next);
    }, 600);
  }

  async function persist(text: string) {
    setSaving(true);
    try {
      const updated = await api<WorkReport>(
        `/api/reports/${reportId}/lines/`,
        {
          method: "POST",
          body: JSON.stringify({ task_id: task.id, work_done: text }),
        },
        token
      );
      onUpdated(updated);
    } catch (e) {
      onError(e instanceof Error ? e.message : "Не удалось сохранить");
    } finally {
      setSaving(false);
    }
  }

  async function uploadFile(file: File) {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const updated = await api<WorkReport>(
        `/api/reports/${reportId}/lines/${task.id}/attachments/`,
        { method: "POST", body: form },
        token
      );
      onUpdated(updated);
    } catch (e) {
      onError(e instanceof Error ? e.message : "Не удалось загрузить файл");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function removeAttachment(attId: number) {
    try {
      const updated = await api<WorkReport>(
        `/api/reports/${reportId}/line-attachments/${attId}/`,
        { method: "DELETE" },
        token
      );
      onUpdated(updated);
    } catch (e) {
      onError(e instanceof Error ? e.message : "Не удалось удалить файл");
    }
  }

  const attachments = task.attachments || [];

  return (
    <article className={`report-task-card${expanded ? " is-open" : ""}`}>
      <button
        type="button"
        className="report-task-card-head"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="report-task-card-title">
          <Link to={`/tasks/${task.id}`} onClick={(e) => e.stopPropagation()}>
            {task.title}
          </Link>
          <span className={`task-status-pill ${STATUS_TONE[task.status]}`}>
            {STATUS_LABEL[task.status]}
          </span>
        </div>
        <div className="report-task-card-meta">
          <span>{formatDuration(task.tracked_seconds)}</span>
          {attachments.length > 0 ? (
            <span className="report-task-files-count">{attachments.length} файл.</span>
          ) : null}
          <span className="report-task-chevron" aria-hidden>
            {expanded ? "▾" : "▸"}
          </span>
        </div>
      </button>

      {expanded ? (
        <div className="report-task-card-body">
          {editable ? (
            <div className="field">
              <label>Что сделано</label>
              <textarea
                rows={3}
                value={workDone}
                placeholder="Кратко опишите результат по этой задаче…"
                onChange={(e) => {
                  const next = e.target.value;
                  setWorkDone(next);
                  scheduleSave(next);
                }}
                onBlur={() => {
                  if (saveTimer.current != null) window.clearTimeout(saveTimer.current);
                  if (workDone !== (task.work_done || "")) void persist(workDone);
                }}
              />
              <p className="report-save-hint muted">
                {saving ? "Сохраняем…" : "Сохраняется автоматически"}
              </p>
            </div>
          ) : workDone ? (
            <p className="report-work-done">{workDone}</p>
          ) : (
            <p className="muted">Описание работ не добавлено</p>
          )}

          {(attachments.length > 0 || editable) && (
            <div className="report-task-attachments">
              {attachments.map((att) => (
                <div key={att.id} className="report-attach-row">
                  {att.url ? (
                    <a href={`${API_BASE}${att.url}`} target="_blank" rel="noreferrer">
                      {att.original_name || "Файл"}
                    </a>
                  ) : (
                    <span>{att.original_name || "Файл"}</span>
                  )}
                  {editable ? (
                    <button
                      type="button"
                      className="btn-link-danger"
                      onClick={() => void removeAttachment(att.id)}
                    >
                      Удалить
                    </button>
                  ) : null}
                </div>
              ))}
              {editable ? (
                <div className="report-attach-actions">
                  <input
                    ref={fileRef}
                    type="file"
                    hidden
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void uploadFile(f);
                    }}
                  />
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={uploading}
                    onClick={() => fileRef.current?.click()}
                  >
                    {uploading ? "Загрузка…" : "Прикрепить файл"}
                  </button>
                </div>
              ) : null}
            </div>
          )}
        </div>
      ) : null}
    </article>
  );
}

export function ProjectReports() {
  const { projectId } = useParams();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();

  const [project, setProject] = useState<Project | null>(null);
  const [reports, setReports] = useState<WorkReport[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<WorkReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [disputeComment, setDisputeComment] = useState("");
  const [selectedTasks, setSelectedTasks] = useState<Set<number>>(new Set());
  const [showDispute, setShowDispute] = useState(false);

  const loadList = useCallback(async () => {
    if (!token || !projectId) return;
    const [projectData, reportData] = await Promise.all([
      api<Project>(`/api/projects/${projectId}/`, {}, token),
      api<WorkReport[] | Paginated<WorkReport>>(
        `/api/reports/?project=${projectId}`,
        {},
        token
      ),
    ]);
    setProject(projectData);
    const list = unwrapList(reportData);
    setReports(list);
    setSelectedId((prev) => {
      if (prev && list.some((r) => r.id === prev)) return prev;
      const active = list.find((r) => r.is_active);
      return active?.id ?? list[0]?.id ?? null;
    });
  }, [token, projectId]);

  const loadDetail = useCallback(
    async (id: number) => {
      if (!token) return;
      const data = await api<WorkReport>(`/api/reports/${id}/`, {}, token);
      setDetail(data);
    },
    [token]
  );

  useEffect(() => {
    if (!token || !projectId) return;
    void loadList().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [token, projectId, loadList]);

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
    portalId: project?.portal ?? null,
    enabled: !!project,
    onEvent: (payload) => {
      if (payload?.kind?.startsWith("report_") || !payload?.kind) {
        void loadList().catch(() => undefined);
        if (selectedId) void loadDetail(selectedId).catch(() => undefined);
      }
    },
  });

  const activeReport = useMemo(
    () => reports.find((r) => r.is_active) ?? null,
    [reports]
  );

  const hint =
    project &&
    project.tasks_count > 0 &&
    project.done_count >= project.tasks_count
      ? "Все задачи выполнены — можно отправить отчёт."
      : null;

  const linesEditable = Boolean(isAgency && detail?.status === "draft" && token);

  async function createReport() {
    if (!token || !projectId) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api<WorkReport>(
        "/api/reports/",
        { method: "POST", body: JSON.stringify({ project: Number(projectId) }) },
        token
      );
      toast.show("Добавьте описания работ и отправьте клиенту", "Отчёт создан");
      await loadList();
      setSelectedId(created.id);
      setDetail(created);
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

  return (
    <div className="tasks-page report-page">
      <div className="page-header">
        <div>
          <p className="muted" style={{ margin: "0 0 6px" }}>
            <Link to={`/projects/${projectId}`}>← {project?.name || "Проект"}</Link>
          </p>
          <h1 className="page-title">Отчёты</h1>
          <p className="page-sub">
            Что сделано по задачам модуля — на согласование клиенту
          </p>
        </div>
        <div className="report-header-actions">
          {isAgency && !activeReport ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => void createReport()}
            >
              Новый отчёт
            </button>
          ) : null}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {hint && isAgency && !activeReport ? (
        <div className="report-hint">{hint}</div>
      ) : null}

      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <div className="report-layout">
        <aside className="report-list-panel">
          <h2 className="section-title">История</h2>
          {reports.length === 0 ? (
            <p className="muted">Пока нет отчётов по этому проекту.</p>
          ) : (
            <ul className="report-list">
              {reports.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className={`report-list-item${selectedId === r.id ? " is-active" : ""}`}
                    onClick={() => setSelectedId(r.id)}
                  >
                    <span className={`report-status-pill status-${r.status}`}>
                      {STATUS_LABEL_RU[r.status]}
                    </span>
                    <span className="report-list-meta">
                      {formatDuration(r.total_tracked_seconds)} ·{" "}
                      {formatDateTime(r.created_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="report-detail-panel">
          {!detail ? (
            <p className="muted">Выберите отчёт слева или создайте новый.</p>
          ) : (
            <>
              <div className="report-detail-head">
                <div>
                  <h2 className="section-title">{detail.project_name}</h2>
                  <p className="muted">
                    <span className={`report-status-pill status-${detail.status}`}>
                      {STATUS_LABEL_RU[detail.status]}
                    </span>{" "}
                    · всего {formatDuration(detail.total_tracked_seconds)}
                  </p>
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
                          "Можно править описания и отправить ещё раз"
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

              {detail.deal_hours ? (
                <div className="report-deal-hours">
                  Часы по сделке: осталось{" "}
                  {formatPackageHours(detail.deal_hours.remaining_hours)} из{" "}
                  {formatPackageHours(detail.deal_hours.paid_hours)}
                </div>
              ) : null}

              {isAgency && detail.status === "draft" ? (
                <p className="report-edit-hint">
                  Раскройте задачу, опишите что сделано и при необходимости приложите файлы.
                </p>
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

              {showDispute && detail.tasks ? (
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
                      required
                    />
                  </div>
                  <ul className="report-task-checkboxes">
                    {detail.tasks.map((t) => (
                      <li key={t.id}>
                        <label>
                          <input
                            type="checkbox"
                            checked={selectedTasks.has(t.id)}
                            onChange={() => toggleTask(t.id)}
                          />
                          <span>{t.title}</span>
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

              <div className="report-task-cards">
                {(detail.tasks || []).map((t) => (
                  <ReportTaskCard
                    key={t.id}
                    task={t}
                    editable={linesEditable}
                    token={token!}
                    reportId={detail.id}
                    onUpdated={(report) => {
                      setDetail(report);
                      void loadList();
                    }}
                    onError={setError}
                  />
                ))}
              </div>

              {detail.events && detail.events.length > 0 ? (
                <div className="report-events">
                  <h3 className="section-title">История согласования</h3>
                  <ul>
                    {detail.events.map((ev) => (
                      <li key={ev.id}>
                        <strong>{EVENT_LABEL[ev.kind] || ev.kind}</strong>
                        {ev.actor_name ? ` — ${ev.actor_name}` : ""}
                        <span className="muted"> · {formatDateTime(ev.created_at)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
