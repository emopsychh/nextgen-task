import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  unwrapList,
  type Paginated,
  type Project,
  type SupportTicket,
  type SupportTicketMessage,
  type Task,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { formatDateTime } from "../../lib/format";
import {
  TICKET_BUCKETS,
  type TicketBucket,
  ticketDetailPath,
  ticketsApiQuery,
  ticketsListPath,
  ticketStatusLabel,
} from "./ticketHelpers";

export function SupportTickets() {
  const { portalId: routePortalId, ticketId: routeTicketId } = useParams();
  const { token, portal, user } = useAuth();
  const isAgency = portal?.role === "agency";
  const navigate = useNavigate();
  const toast = useFlashToast();

  const portalId = useMemo(() => {
    if (routePortalId) return Number(routePortalId);
    if (!isAgency && portal?.id) return portal.id;
    return null;
  }, [routePortalId, isAgency, portal?.id]);

  const selectedId = routeTicketId ? Number(routeTicketId) : null;

  const [bucket, setBucket] = useState<TicketBucket>("open");
  const [tickets, setTickets] = useState<SupportTicket[]>([]);
  const [detail, setDetail] = useState<SupportTicket | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [projectId, setProjectId] = useState<number | "">("");
  const [taskId, setTaskId] = useState<number | "">("");
  const [draft, setDraft] = useState("");
  const threadRef = useRef<HTMLDivElement>(null);

  const listPath = ticketsListPath(portalId, isAgency);

  const loadList = useCallback(async () => {
    if (!token || !portalId) return;
    const data = await api<SupportTicket[] | Paginated<SupportTicket>>(
      ticketsApiQuery(portalId, bucket),
      {},
      token
    );
    setTickets(unwrapList(data));
  }, [token, portalId, bucket]);

  const loadDetail = useCallback(async () => {
    if (!token || !selectedId) {
      setDetail(null);
      return;
    }
    const data = await api<SupportTicket>(`/api/tickets/${selectedId}/`, {}, token);
    setDetail(data);
  }, [token, selectedId]);

  const loadProjects = useCallback(async () => {
    if (!token || !portalId) return;
    const data = await api<Project[] | Paginated<Project>>(
      `/api/projects/?portal=${portalId}`,
      {},
      token
    );
    setProjects(unwrapList(data));
  }, [token, portalId]);

  useEffect(() => {
    if (!token || !portalId) return;
    void loadList().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [token, portalId, loadList]);

  useEffect(() => {
    if (!token) return;
    void loadDetail().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [token, loadDetail]);

  useEffect(() => {
    if (!token || !portalId || !projectId) {
      setTasks([]);
      return;
    }
    let cancelled = false;
    void api<Task[] | Paginated<Task>>(`/api/tasks/?project=${projectId}`, {}, token)
      .then((data) => {
        if (!cancelled) setTasks(unwrapList(data));
      })
      .catch(() => {
        if (!cancelled) setTasks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [token, portalId, projectId]);

  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [detail?.messages?.length, selectedId]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: !!portalId,
    onEvent: (payload) => {
      if (payload?.kind?.startsWith("ticket_") || !payload?.kind) {
        void loadList().catch(() => undefined);
        if (selectedId) void loadDetail().catch(() => undefined);
      }
    },
  });

  async function createTicket() {
    if (!token || !portalId || !subject.trim() || !body.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        portal: portalId,
        subject: subject.trim(),
        body: body.trim(),
      };
      if (projectId) payload.project = projectId;
      if (taskId) payload.task = taskId;
      const created = await api<SupportTicket>(
        "/api/tickets/",
        { method: "POST", body: JSON.stringify(payload) },
        token
      );
      toast.show("Тикет создан", "Мы ответим в этом диалоге");
      setShowCreate(false);
      setSubject("");
      setBody("");
      setProjectId("");
      setTaskId("");
      setBucket("open");
      navigate(ticketDetailPath(portalId, isAgency, created.id));
      void loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать тикет");
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage() {
    if (!token || !selectedId || !draft.trim() || detail?.status === "closed") return;
    setBusy(true);
    setError(null);
    try {
      const msg = await api<SupportTicketMessage>(
        `/api/tickets/${selectedId}/messages/`,
        { method: "POST", body: JSON.stringify({ text: draft.trim() }) },
        token
      );
      setDraft("");
      setDetail((prev) =>
        prev
          ? { ...prev, messages: [...(prev.messages || []), msg], updated_at: msg.created_at }
          : prev
      );
      void loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось отправить");
    } finally {
      setBusy(false);
    }
  }

  async function closeOrReopen(action: "close" | "reopen") {
    if (!token || !selectedId || !isAgency) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api<SupportTicket>(
        `/api/tickets/${selectedId}/${action}/`,
        { method: "POST", body: "{}" },
        token
      );
      setDetail(updated);
      toast.show(action === "close" ? "Тикет закрыт" : "Тикет снова открыт");
      if (action === "close" && bucket === "open") {
        navigate(listPath);
        setBucket("closed");
      } else if (action === "reopen" && bucket === "closed") {
        setBucket("open");
      }
      void loadList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Действие не выполнено");
    } finally {
      setBusy(false);
    }
  }

  function onComposerKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  }

  if (!portalId) {
    return (
      <div className="tasks-page">
        <p className="muted">Выберите клиента, чтобы открыть тикеты.</p>
      </div>
    );
  }

  const myAuthorId = user?.id ?? null;

  return (
    <div className="tasks-page tickets-hub">
      <div className="page-header">
        <div>
          <h1 className="page-title">{isAgency ? "Тикеты" : "Поддержка"}</h1>
          <p className="page-sub">
            {isAgency
              ? "Обращения клиента — ответы и закрытие тикетов"
              : "Сообщите о проблеме — агентство ответит в этом чате"}
          </p>
        </div>
        {!isAgency ? (
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => {
              setShowCreate(true);
              void loadProjects();
            }}
          >
            Новый тикет
          </button>
        ) : null}
      </div>

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      {showCreate && !isAgency ? (
        <div className="connect-panel stack ticket-create-panel">
          <div>
            <h2 className="section-title">Новый тикет</h2>
            <p className="muted">Кратко опишите проблему — можно привязать проект или задачу.</p>
          </div>
          <div className="field">
            <label>Тема</label>
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Например: не открывается отчёт"
              maxLength={500}
            />
          </div>
          <div className="field">
            <label>Описание</label>
            <textarea
              rows={4}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Что происходит и что вы уже пробовали"
            />
          </div>
          <div className="ticket-create-row">
            <div className="field">
              <label>Проект (необязательно)</label>
              <select
                value={projectId === "" ? "" : String(projectId)}
                onChange={(e) => {
                  const v = e.target.value;
                  setProjectId(v ? Number(v) : "");
                  setTaskId("");
                }}
              >
                <option value="">Без проекта</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Задача (необязательно)</label>
              <select
                value={taskId === "" ? "" : String(taskId)}
                onChange={(e) => {
                  const v = e.target.value;
                  setTaskId(v ? Number(v) : "");
                }}
                disabled={!projectId}
              >
                <option value="">Без задачи</option>
                {tasks.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.title}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="report-create-actions">
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || !subject.trim() || !body.trim()}
              onClick={() => void createTicket()}
            >
              Создать
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => setShowCreate(false)}
            >
              Отмена
            </button>
          </div>
        </div>
      ) : null}

      <div className="tickets-layout">
        <aside className="tickets-pane tickets-list-pane">
          <div className="task-filters report-filter-row tickets-tabs">
            {TICKET_BUCKETS.map((b) => (
              <button
                key={b.id}
                type="button"
                className={`task-filter-chip${bucket === b.id ? " active" : ""}`}
                onClick={() => {
                  setBucket(b.id);
                  if (selectedId) navigate(listPath);
                }}
              >
                {b.label}
              </button>
            ))}
          </div>
          {tickets.length === 0 ? (
            <p className="muted tickets-empty-list">
              {bucket === "open" ? "Нет открытых тикетов" : "Архив пуст"}
            </p>
          ) : (
            <ul className="tickets-list">
              {tickets.map((t) => {
                const active = t.id === selectedId;
                return (
                  <li key={t.id}>
                    <button
                      type="button"
                      className={`tickets-list-item${active ? " active" : ""}`}
                      onClick={() => navigate(ticketDetailPath(portalId, isAgency, t.id))}
                    >
                      <span className="tickets-list-subject">{t.subject}</span>
                      <span className="tickets-list-meta">
                        {ticketStatusLabel(t.status)} · {formatDateTime(t.updated_at)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <section className="tickets-pane tickets-detail-pane">
          {!selectedId || !detail ? (
            <div className="tickets-empty-detail">
              <p className="tickets-empty-title">Выберите тикет</p>
              <p className="muted">
                Среднее время ответа появится позже. Пока ответим в этом чате.
              </p>
            </div>
          ) : (
            <>
              <header className="tickets-detail-head">
                <div>
                  <h2 className="tickets-detail-title">{detail.subject}</h2>
                  <p className="tickets-detail-meta">
                    <span
                      className={`ticket-status-pill${detail.status === "closed" ? " is-closed" : ""}`}
                    >
                      {ticketStatusLabel(detail.status)}
                    </span>
                    <span className="muted">
                      {detail.created_by_name || "—"} · {formatDateTime(detail.created_at)}
                    </span>
                  </p>
                  {(detail.project_name || detail.task_title) && (
                    <p className="tickets-detail-links">
                      {detail.project ? (
                        <Link to={`/projects/${detail.project}`}>{detail.project_name}</Link>
                      ) : null}
                      {detail.project && detail.task ? " · " : null}
                      {detail.task ? (
                        <Link to={`/tasks/${detail.task}`}>{detail.task_title}</Link>
                      ) : null}
                    </p>
                  )}
                </div>
                {isAgency ? (
                  <div className="report-create-actions">
                    {detail.status === "open" ? (
                      <button
                        type="button"
                        className="btn"
                        disabled={busy}
                        onClick={() => void closeOrReopen("close")}
                      >
                        Закрыть
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={busy}
                        onClick={() => void closeOrReopen("reopen")}
                      >
                        Открыть снова
                      </button>
                    )}
                  </div>
                ) : null}
              </header>

              <div className="tickets-detail-body">
                <p className="tickets-body-text">{detail.body}</p>
              </div>

              <div className="tickets-thread" ref={threadRef}>
                {(detail.messages || []).length === 0 ? (
                  <p className="muted tickets-thread-empty">Пока нет сообщений в переписке</p>
                ) : (
                  (detail.messages || []).map((m) => {
                    const mine = myAuthorId != null && m.author === myAuthorId;
                    return (
                      <div
                        key={m.id}
                        className={`msg-row ticket-msg-row${mine ? " is-mine" : ""}`}
                      >
                        <div className="msg-bubble">
                          <div className="comment-top">
                            <strong>{m.author_name || "Участник"}</strong>
                            <span className="msg-time">{formatDateTime(m.created_at)}</span>
                          </div>
                          <div className="comment-text">{m.text}</div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>

              {detail.status === "closed" ? (
                <p className="muted tickets-composer-closed">
                  Тикет закрыт
                  {isAgency ? " — откройте снова, чтобы продолжить переписку" : ""}
                </p>
              ) : (
                <form
                  className="msg-composer messenger-composer ticket-composer"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void sendMessage();
                  }}
                >
                  <textarea
                    rows={2}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={onComposerKeyDown}
                    placeholder="Напишите сообщение…"
                    disabled={busy}
                  />
                  <button
                    type="submit"
                    className="btn btn-primary"
                    disabled={busy || !draft.trim()}
                  >
                    Отправить
                  </button>
                </form>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
