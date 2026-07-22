import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  type Comment,
  type Task,
  type TaskStatus,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { TaskGlyph } from "../../components/icons";
import { TaskComposer } from "../../components/task/TaskComposer";
import { TaskSummaryCard } from "../../components/task/TaskSummaryCard";
import { TaskThread, type ThreadItem, type ThreadRow } from "../../components/task/TaskThread";
import { useFlashToast } from "../../hooks/useFlashToast";
import { useTaskLiveSync } from "../../hooks/useTaskLiveSync";
import { dueMeta } from "../../lib/dates";
import { formatDayLabel, formatDueFull } from "../../lib/format";
import { isTaskOverdue, STATUS_LABEL, STATUS_TONE } from "../../lib/status";

type TaskPatch = Partial<{
  title: string;
  description: string;
  status: TaskStatus;
  due_date: string | null;
}>;

export function TaskDetail() {
  const { taskId } = useParams();
  const { token, portal, user } = useAuth();
  const canManage = Boolean(token);
  const canChangeStatus = portal?.role === "agency";
  const toast = useFlashToast(1800);
  const textInputRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const summaryRef = useRef<HTMLElement>(null);
  const threadEndRef = useRef<HTMLDivElement>(null);

  const [task, setTask] = useState<Task | null>(null);
  const [comment, setComment] = useState("");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sendBusy, setSendBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [compactTask, setCompactTask] = useState(false);

  const canEditDueDate =
    portal?.role === "agency" ||
    (portal?.role === "client" &&
      user != null &&
      task != null &&
      task.created_by != null &&
      task.created_by === user.id);

  async function load() {
    if (!token || !taskId) return;
    // Fast path: show task from DB immediately. Bitrix pull happens in live sync.
    const data = await api<Task>(`/api/tasks/${taskId}/`, {}, token);
    setTask(data);
    setDraftTitle(data.title);
    setDraftDescription(data.description || "");
  }

  useEffect(() => {
    setTask(null);
    setError(null);
    void load().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
  }, [token, taskId]);

  useTaskLiveSync({
    token,
    taskId,
    task,
    portalId: task?.portal_id ?? portal?.id ?? null,
    draftTitle,
    draftDescription,
    onUpdate: (data, drafts) => {
      setTask(data);
      if (drafts.title !== undefined) setDraftTitle(drafts.title);
      if (drafts.description !== undefined) setDraftDescription(drafts.description);
    },
  });

  const thread = useMemo(() => {
    if (!task) return [] as ThreadItem[];
    const items: ThreadItem[] = [];
    for (const c of task.comments || []) {
      items.push({ kind: "comment", at: c.created_at, comment: c });
    }
    for (const f of task.attachments || []) {
      if (f.comment) continue;
      items.push({ kind: "file", at: f.created_at, file: f });
    }
    items.sort((a, b) => a.at.localeCompare(b.at));
    return items;
  }, [task]);

  const threadWithDays = useMemo(() => {
    const rows: ThreadRow[] = [];
    let lastDay = "";
    for (const item of thread) {
      const day = item.at.slice(0, 10);
      if (day !== lastDay) {
        rows.push({ type: "day", label: formatDayLabel(item.at) });
        lastDay = day;
      }
      rows.push({ type: "item", item });
    }
    return rows;
  }, [thread]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [thread.length]);

  useEffect(() => {
    const el = textInputRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 34), 160)}px`;
  }, [comment]);

  useEffect(() => {
    const card = summaryRef.current;
    const root = threadRef.current;
    if (!card || !root) return;

    const io = new IntersectionObserver(
      ([entry]) => {
        setCompactTask(!entry.isIntersecting);
      },
      { root, threshold: 0.15, rootMargin: "0px 0px 0px 0px" }
    );
    io.observe(card);
    return () => io.disconnect();
  }, [task?.id]);

  function scrollToTaskCard() {
    summaryRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function patchTask(fields: TaskPatch, okMessage?: string) {
    if (!token || !task || !canManage) return;
    setSaveBusy(true);
    setError(null);
    try {
      const updated = await api<Task>(
        `/api/tasks/${task.id}/`,
        { method: "PATCH", body: JSON.stringify(fields) },
        token
      );
      setTask(updated);
      setDraftTitle(updated.title);
      setDraftDescription(updated.description || "");
      if (okMessage) toast.show(okMessage);
      window.dispatchEvent(new Event("projects-updated"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
      setDraftTitle(task.title);
      setDraftDescription(task.description || "");
    } finally {
      setSaveBusy(false);
    }
  }

  async function commitTitle() {
    if (!task || !canManage) return;
    const title = draftTitle.trim();
    if (!title) {
      setDraftTitle(task.title);
      setError("Название не может быть пустым");
      return;
    }
    if (title === task.title) return;
    await patchTask({ title }, "Название обновлено");
  }

  async function commitDescription() {
    if (!task || !canManage) return;
    const description = draftDescription;
    if (description === (task.description || "")) return;
    await patchTask({ description }, "Описание обновлено");
  }

  async function setStatus(status: TaskStatus) {
    if (!task || !canChangeStatus || task.status === status) return;
    await patchTask({ status });
  }

  async function setDueDate(iso: string) {
    if (!task || !canEditDueDate) return;
    const next = iso || null;
    if (next === task.due_date) return;
    await patchTask({ due_date: next });
  }

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const list = e.target.files;
    if (!list?.length) return;
    setPendingFiles((prev) => [...prev, ...Array.from(list)]);
    e.target.value = "";
  }

  function onAddFiles(files: File[]) {
    if (!files.length) return;
    setPendingFiles((prev) => [...prev, ...files]);
  }

  function removePending(index: number) {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  }

  async function uploadAttachment(file: File, opts: { taskId: number; commentId?: number }) {
    if (!token) return;
    const form = new FormData();
    form.append("task", String(opts.taskId));
    if (opts.commentId) form.append("comment", String(opts.commentId));
    form.append("file", file);
    await api("/api/attachments/", { method: "POST", body: form }, token);
  }

  async function sendMessage(e?: React.FormEvent) {
    e?.preventDefault();
    if (!token || !task) return;
    const text = comment.trim();
    const files = pendingFiles;
    if (!text && files.length === 0) return;

    setSendBusy(true);
    setError(null);
    try {
      // Always create a comment so files appear in the thread and sync to Bitrix chat.
      // Empty text is allowed for file-only messages.
      const created = await api<Comment>(
        "/api/comments/",
        { method: "POST", body: JSON.stringify({ task: task.id, text }) },
        token
      );

      for (const file of files) {
        await uploadAttachment(file, {
          taskId: task.id,
          commentId: created.id,
        });
      }

      setComment("");
      setPendingFiles([]);
      await load();
      toast.show(text ? "Сообщение отправлено" : "Файл отправлен");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить");
    } finally {
      setSendBusy(false);
    }
  }

  function onComposerKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  }

  if (!task) {
    return (
      <div className="task-detail-page">
        <div className="muted">Загрузка задачи…</div>
      </div>
    );
  }

  const due = dueMeta(task.due_date, task.status);
  const overdue = isTaskOverdue(task.due_date, task.status);
  const canSend = Boolean(comment.trim() || pendingFiles.length) && !sendBusy;
  const creator = task.created_by_name || "Команда";

  return (
    <div className="task-detail-page chat-mode">
      <div className="chat-topbar">
        <div className="chat-topbar-left">
          <Link to={`/projects/${task.project}`} className="task-back" title="К задачам">
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
            <span className="task-back-label">К задачам</span>
          </Link>
          <div className="chat-topbar-title">
            <strong>Чат задачи</strong>
            <span className="muted">{task.project_name}</span>
          </div>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <FlashToast message={toast.message} leaving={toast.leaving} />

      <section className="messenger">
        <button
          type="button"
          className={`task-sticky-bar${compactTask ? " visible" : ""}`}
          onClick={scrollToTaskCard}
          aria-hidden={!compactTask}
          tabIndex={compactTask ? 0 : -1}
        >
          <span className="task-sticky-icon" aria-hidden>
            <TaskGlyph />
          </span>
          <span className="task-sticky-main">
            <strong className="task-sticky-title">{task.title}</strong>
            <span className="task-sticky-meta">
              <span className={`task-status-pill ${STATUS_TONE[task.status]}`}>
                {STATUS_LABEL[task.status]}
              </span>
              {overdue ? (
                <span className="task-status-pill status-overdue">Опаздывает</span>
              ) : null}
              <span className={`task-due-inline ${due.tone}`}>
                {formatDueFull(task.due_date)}
              </span>
            </span>
          </span>
          <span className="task-sticky-hint muted">к карточке</span>
        </button>

        <div className="messenger-thread" ref={threadRef}>
          <div className="chat-day-pill">задача</div>

          <TaskSummaryCard
            ref={summaryRef}
            task={task}
            creator={creator}
            overdue={overdue}
            due={due}
            canManage={canManage}
            canChangeStatus={canChangeStatus}
            canEditDueDate={canEditDueDate}
            saveBusy={saveBusy}
            draftTitle={draftTitle}
            draftDescription={draftDescription}
            onDraftTitle={setDraftTitle}
            onDraftDescription={setDraftDescription}
            onCommitTitle={() => void commitTitle()}
            onCommitDescription={() => void commitDescription()}
            onSetStatus={(s) => void setStatus(s)}
            onSetDueDate={(iso) => void setDueDate(iso)}
          />

          <TaskThread ref={threadEndRef} rows={threadWithDays} />
        </div>

        <TaskComposer
          ref={textInputRef}
          comment={comment}
          pendingFiles={pendingFiles}
          canSend={canSend}
          onCommentChange={setComment}
          onPickFiles={onPickFiles}
          onAddFiles={onAddFiles}
          onRemovePending={removePending}
          onSend={(e) => void sendMessage(e)}
          onKeyDown={onComposerKeyDown}
        />
      </section>
    </div>
  );
}
