import { useCallback, useEffect, useRef, useState } from "react";
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
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { formatDateTime } from "../../lib/format";
import {
  TICKET_BUCKETS,
  type TicketBucket,
  ticketsApiQuery,
} from "../../pages/shared/ticketHelpers";
import { useSupportWidget } from "./SupportWidgetContext";

type View = "list" | "create" | "chat";

export function ClientSupportWidget() {
  const { token, portal, user } = useAuth();
  const { isOpen, close, initialTicketId } = useSupportWidget();
  const portalId = portal?.id ?? null;

  const [view, setView] = useState<View>("list");
  const [bucket, setBucket] = useState<TicketBucket>("open");
  const [tickets, setTickets] = useState<SupportTicket[]>([]);
  const [detail, setDetail] = useState<SupportTicket | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [projectId, setProjectId] = useState<number | "">("");
  const [taskId, setTaskId] = useState<number | "">("");
  const [draft, setDraft] = useState("");
  const threadRef = useRef<HTMLDivElement>(null);

  const loadList = useCallback(async () => {
    if (!token || !portalId) return;
    const data = await api<SupportTicket[] | Paginated<SupportTicket>>(
      ticketsApiQuery(portalId, bucket),
      {},
      token
    );
    setTickets(unwrapList(data));
  }, [token, portalId, bucket]);

  const loadDetail = useCallback(
    async (id: number) => {
      if (!token) return;
      const data = await api<SupportTicket>(`/api/tickets/${id}/`, {}, token);
      setDetail(data);
      setView("chat");
    },
    [token]
  );

  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    if (initialTicketId) {
      void loadDetail(initialTicketId).catch((e) =>
        setError(e instanceof Error ? e.message : "Ошибка")
      );
    } else {
      setView("list");
      setDetail(null);
    }
  }, [isOpen, initialTicketId, loadDetail]);

  useEffect(() => {
    if (!isOpen || !token || !portalId) return;
    void loadList().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [isOpen, token, portalId, loadList]);

  useEffect(() => {
    if (!isOpen || view !== "create" || !token || !portalId) return;
    let cancelled = false;
    void api<Project[] | Paginated<Project>>(`/api/projects/?portal=${portalId}`, {}, token)
      .then((data) => {
        if (!cancelled) setProjects(unwrapList(data));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [isOpen, view, token, portalId]);

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
  }, [detail?.messages?.length, view]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: isOpen && !!portalId,
    onEvent: (payload) => {
      if (payload?.kind?.startsWith("ticket_") || !payload?.kind) {
        void loadList().catch(() => undefined);
        if (detail?.id) void loadDetail(detail.id).catch(() => undefined);
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
      setSubject("");
      setBody("");
      setProjectId("");
      setTaskId("");
      setBucket("open");
      await loadList();
      await loadDetail(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать тикет");
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage() {
    if (!token || !detail || !draft.trim() || detail.status === "closed") return;
    setBusy(true);
    setError(null);
    try {
      const msg = await api<SupportTicketMessage>(
        `/api/tickets/${detail.id}/messages/`,
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

  function onComposerKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  }

  if (!isOpen) return null;

  const myAuthorId = user?.id ?? null;
  const showChat = view === "chat" && detail;
  const panelWide = view === "create" || showChat;

  return (
    <div className="support-widget-root" role="dialog" aria-label="Поддержка">
      <div className={`support-widget${panelWide ? " is-wide" : ""}`}>
        {view === "list" ? (
          <div className="support-widget-list-pane">
            <header className="support-widget-bar">
              <button
                type="button"
                className="support-widget-icon-btn"
                onClick={close}
                aria-label="Закрыть"
              >
                ×
              </button>
              <div className="support-widget-tabs">
                {TICKET_BUCKETS.map((b) => (
                  <button
                    key={b.id}
                    type="button"
                    className={`support-widget-tab${bucket === b.id ? " active" : ""}`}
                    onClick={() => setBucket(b.id)}
                  >
                    {b.label.toLowerCase()}
                  </button>
                ))}
              </div>
            </header>

            {error ? <div className="support-widget-error">{error}</div> : null}

            <div className="support-widget-scroll">
              {tickets.length === 0 ? (
                <p className="support-widget-empty muted">
                  {bucket === "open" ? "Нет открытых тикетов" : "Архив пуст"}
                </p>
              ) : (
                <ul className="support-widget-tickets">
                  {tickets.map((t) => (
                    <li key={t.id}>
                      <button
                        type="button"
                        className="support-widget-ticket"
                        onClick={() => void loadDetail(t.id)}
                      >
                        <span className="support-widget-ticket-title">{t.subject}</span>
                        <span className="support-widget-ticket-id">#{t.id}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <footer className="support-widget-footer">
              <button
                type="button"
                className="support-widget-new"
                onClick={() => {
                  setError(null);
                  setView("create");
                }}
              >
                <span aria-hidden>+</span> новый тикет
              </button>
            </footer>
          </div>
        ) : null}

        {view === "create" ? (
          <div className="support-widget-panel">
            <header className="support-widget-bar">
              <button
                type="button"
                className="support-widget-icon-btn"
                onClick={() => setView("list")}
                aria-label="Назад"
              >
                ‹
              </button>
              <strong className="support-widget-title">Создание тикета</strong>
              <button
                type="button"
                className="support-widget-icon-btn"
                onClick={close}
                aria-label="Закрыть"
              >
                ×
              </button>
            </header>

            {error ? <div className="support-widget-error">{error}</div> : null}

            <div className="support-widget-form">
              <label className="support-widget-field">
                <span>Тема тикета</span>
                <input
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  placeholder="Кратко, о чём вопрос"
                  maxLength={500}
                  autoFocus
                />
              </label>
              <label className="support-widget-field">
                <span className="sr-only">Описание</span>
                <textarea
                  rows={6}
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  placeholder="Подробно опишите вашу проблему…"
                />
              </label>
              <label className="support-widget-field">
                <span>Проект (необязательно)</span>
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
              </label>
              {projectId ? (
                <label className="support-widget-field">
                  <span>Задача (необязательно)</span>
                  <select
                    value={taskId === "" ? "" : String(taskId)}
                    onChange={(e) => {
                      const v = e.target.value;
                      setTaskId(v ? Number(v) : "");
                    }}
                  >
                    <option value="">Без задачи</option>
                    {tasks.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.title}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>

            <footer className="support-widget-footer support-widget-footer-create">
              <button
                type="button"
                className="btn btn-primary support-widget-create-btn"
                disabled={busy || !subject.trim() || !body.trim()}
                onClick={() => void createTicket()}
              >
                создать
              </button>
            </footer>
          </div>
        ) : null}

        {showChat && detail ? (
          <div className="support-widget-panel support-widget-chat">
            <header className="support-widget-bar support-widget-chat-head">
              <button
                type="button"
                className="support-widget-icon-btn"
                onClick={() => {
                  setDetail(null);
                  setView("list");
                  void loadList();
                }}
                aria-label="К списку"
              >
                ‹
              </button>
              <div className="support-widget-chat-meta">
                <strong>#{detail.id}</strong>
                <span className="support-widget-chat-subject">{detail.subject}</span>
                <span
                  className={`support-widget-status${detail.status === "closed" ? " is-closed" : ""}`}
                >
                  {detail.status === "closed" ? "закрыт" : "ожидает ответа"}
                </span>
              </div>
              <button
                type="button"
                className="support-widget-icon-btn"
                onClick={close}
                aria-label="Закрыть"
              >
                ×
              </button>
            </header>

            {error ? <div className="support-widget-error">{error}</div> : null}

            <div className="support-widget-chat-body" ref={threadRef}>
              {(detail.messages || []).length === 0 && detail.body ? (
                <div className="support-widget-msg is-mine">
                  <div className="support-widget-msg-bubble">
                    <div className="support-widget-msg-top">
                      <strong>{detail.created_by_name || "Вы"}</strong>
                      <span>{formatDateTime(detail.created_at)}</span>
                    </div>
                    <p>{detail.body}</p>
                  </div>
                </div>
              ) : null}
              {(detail.messages || []).map((m) => {
                const mine = myAuthorId != null && m.author === myAuthorId;
                return (
                  <div
                    key={m.id}
                    className={`support-widget-msg${mine ? " is-mine" : ""}`}
                  >
                    <div className="support-widget-msg-bubble">
                      <div className="support-widget-msg-top">
                        <strong>{m.author_name || (mine ? "Вы" : "Поддержка")}</strong>
                        <span>{formatDateTime(m.created_at)}</span>
                      </div>
                      <p>{m.text}</p>
                    </div>
                  </div>
                );
              })}
            </div>

            {detail.status === "closed" ? (
              <p className="muted support-widget-closed-note">Тикет закрыт</p>
            ) : (
              <form
                className="support-widget-composer"
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
                  placeholder="Введите сообщение…"
                  disabled={busy}
                />
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={busy || !draft.trim()}
                  aria-label="Отправить"
                >
                  →
                </button>
              </form>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
