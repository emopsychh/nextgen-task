import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  isAbortError,
  type Comment,
  type Task,
  type TaskStatus,
  type ThreadItem,
  type ThreadPage,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { TaskCompleteModal } from "../../components/TaskCompleteModal";
import { TaskGlyph } from "../../components/icons";
import { TaskComposer } from "../../components/task/TaskComposer";
import { TaskSummaryCard } from "../../components/task/TaskSummaryCard";
import { TaskThread, type ThreadRow } from "../../components/task/TaskThread";
import { SyncHint } from "../../components/SyncHint";
import { useFlashToast } from "../../hooks/useFlashToast";
import { useTaskLiveSync } from "../../hooks/useTaskLiveSync";
import { dueMeta } from "../../lib/dates";
import { formatDayLabel, formatDueFull } from "../../lib/format";
import { isImageFile } from "../../lib/files";
import { isTaskOverdue, STATUS_LABEL, STATUS_TONE } from "../../lib/status";

/** Images first, then documents; drop exact duplicates (same name+size). */
function normalizePendingFiles(files: File[]): File[] {
  const seen = new Set<string>();
  const images: File[] = [];
  const docs: File[] = [];
  for (const f of files) {
    const key = `${f.name}::${f.size}::${f.lastModified}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (isImageFile(f)) images.push(f);
    else docs.push(f);
  }
  return [...images, ...docs];
}

type TaskPatch = Partial<{
  title: string;
  description: string;
  outcome: string;
  status: TaskStatus;
  due_date: string | null;
  is_important: boolean;
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
  const [items, setItems] = useState<ThreadItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const itemsRef = useRef<ThreadItem[]>([]);
  const activityRef = useRef<{ c: number; lc: number; f: number; lf: number } | null>(
    null
  );
  // Bumped whenever the task/token changes; async responses that resolve after
  // a switch are discarded so a slow request can't overwrite a newer task.
  const genRef = useRef(0);
  const [comment, setComment] = useState("");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sendBusy, setSendBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftOutcome, setDraftOutcome] = useState("");
  const [completeOpen, setCompleteOpen] = useState(false);
  const [compactTask, setCompactTask] = useState(false);
  const [threadSyncing, setThreadSyncing] = useState(false);

  const canEditDueDate =
    portal?.role === "agency" ||
    (portal?.role === "client" &&
      user != null &&
      task != null &&
      task.created_by != null &&
      task.created_by === user.id);

  async function load(signal?: AbortSignal) {
    if (!token || !taskId) return;
    const gen = genRef.current;
    // Fast path: show task from DB immediately. Bitrix pull happens in live sync.
    const data = await api<Task>(`/api/tasks/${taskId}/`, { signal }, token);
    if (gen !== genRef.current || signal?.aborted) return;
    setTask(data);
    setDraftTitle(data.title);
    setDraftDescription(data.description || "");
    setDraftOutcome(data.outcome || "");
  }

  // --- Chat thread: loaded lazily & paginated (never inlined on the task) ---

  function itemKey(it: ThreadItem): string {
    return it.kind === "comment" ? `c${it.comment.id}` : `f${it.file.id}`;
  }

  function isNearBottom(): boolean {
    const el = threadRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 160;
  }

  function scrollToBottom(smooth = false) {
    requestAnimationFrame(() =>
      threadEndRef.current?.scrollIntoView(smooth ? { behavior: "smooth" } : undefined)
    );
  }

  async function fetchThread(query: string, signal?: AbortSignal): Promise<ThreadPage | null> {
    if (!token || !taskId) return null;
    return api<ThreadPage>(`/api/tasks/${taskId}/thread/${query}`, { signal }, token);
  }

  function appendNew(newItems: ThreadItem[]) {
    if (!newItems.length) return;
    setItems((prev) => {
      const seen = new Set(prev.map(itemKey));
      const merged = prev.slice();
      for (const it of newItems) if (!seen.has(itemKey(it))) merged.push(it);
      merged.sort((a, b) => a.at.localeCompare(b.at));
      return merged;
    });
  }

  async function loadInitialThread(signal?: AbortSignal) {
    const gen = genRef.current;
    // Paint from DB immediately; Bitrix comments then files catch up in background.
    const page = await fetchThread("?limit=30", signal);
    if (!page || gen !== genRef.current || signal?.aborted) return;
    setItems(page.items);
    setHasMore(page.has_more);
    scrollToBottom(false);

    setThreadSyncing(true);
    try {
      const pulled = await fetchThread("?pull=1&limit=30", signal);
      if (!pulled || gen !== genRef.current || signal?.aborted) return;
      setItems(pulled.items);
      setHasMore(pulled.has_more);
    } catch {
      // comments pull is best-effort
    } finally {
      if (gen === genRef.current) setThreadSyncing(false);
    }

    // Files are slower (Bitrix disk download) — never block chat paint/sync hint.
    void fetchThread("?files=1&limit=30", signal)
      .then((withFiles) => {
        if (!withFiles || gen !== genRef.current || signal?.aborted) return;
        setItems(withFiles.items);
        setHasMore(withFiles.has_more);
        if (isNearBottom()) scrollToBottom(true);
      })
      .catch(() => undefined);
  }

  async function reloadLatestThread() {
    const gen = genRef.current;
    const page = await fetchThread("?limit=30");
    if (!page || gen !== genRef.current) return;
    setItems(page.items);
    setHasMore(page.has_more);
  }

  async function loadOlder() {
    if (loadingOlder || !hasMore) return;
    const cur = itemsRef.current;
    const oldest = cur.length ? cur[0].at : "";
    if (!oldest) return;
    const gen = genRef.current;
    setLoadingOlder(true);
    const el = threadRef.current;
    const prevH = el?.scrollHeight ?? 0;
    const prevTop = el?.scrollTop ?? 0;
    try {
      const page = await fetchThread(`?before=${encodeURIComponent(oldest)}&limit=30`);
      if (!page || gen !== genRef.current) return;
      setItems((prev) => {
        const seen = new Set(prev.map(itemKey));
        const older = page.items.filter((it) => !seen.has(itemKey(it)));
        return older.length ? [...older, ...prev] : prev;
      });
      setHasMore(page.has_more);
      // Preserve the viewport anchor after prepending older messages.
      requestAnimationFrame(() => {
        const node = threadRef.current;
        if (node) node.scrollTop = prevTop + (node.scrollHeight - prevH);
      });
    } finally {
      setLoadingOlder(false);
    }
  }

  // Detect new/removed activity from the lightweight live-poll signals and
  // fetch only the delta instead of the whole history.
  async function reconcileThread(data: Task) {
    const gen = genRef.current;
    const next = {
      c: data.comments_count ?? 0,
      lc: data.last_comment_id ?? 0,
      f: data.files_count ?? 0,
      lf: data.last_file_id ?? 0,
    };
    const prev = activityRef.current;
    activityRef.current = next;
    if (!prev) return; // baseline is established by loadInitialThread()
    if (next.c === prev.c && next.lc === prev.lc && next.f === prev.f && next.lf === prev.lf) {
      return;
    }
    const grew =
      next.lc > prev.lc || next.lf > prev.lf || next.c > prev.c || next.f > prev.f;
    if (!grew) {
      // Something was removed (e.g. a deleted comment) — resync from latest.
      await reloadLatestThread();
      return;
    }
    const stick = isNearBottom();
    const cur = itemsRef.current;
    const newestAt = cur.length ? cur[cur.length - 1].at : "";
    const page = await fetchThread(
      newestAt ? `?after=${encodeURIComponent(newestAt)}` : "?limit=30"
    );
    if (!page || gen !== genRef.current) return;
    if (newestAt) {
      appendNew(page.items);
    } else {
      setItems(page.items);
      setHasMore(page.has_more);
    }
    if (stick) scrollToBottom(true);
  }

  async function refreshAfterSend() {
    const gen = genRef.current;
    const cur = itemsRef.current;
    const newestAt = cur.length ? cur[cur.length - 1].at : "";
    const page = await fetchThread(
      newestAt ? `?after=${encodeURIComponent(newestAt)}` : "?limit=30"
    );
    if (page && gen === genRef.current) {
      if (newestAt) {
        appendNew(page.items);
      } else {
        setItems(page.items);
        setHasMore(page.has_more);
      }
    }
    scrollToBottom(true);
  }

  useEffect(() => {
    genRef.current += 1;
    setTask(null);
    setError(null);
    setItems([]);
    setHasMore(false);
    setThreadSyncing(false);
    itemsRef.current = [];
    activityRef.current = null;
    const ac = new AbortController();
    void load(ac.signal).catch((e) => {
      if (!isAbortError(e)) setError(e instanceof Error ? e.message : "Ошибка");
    });
    void loadInitialThread(ac.signal).catch(() => {});
    return () => ac.abort();
  }, [token, taskId]);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  // Soft catch-up for Bitrix task files while the detail page stays open.
  useEffect(() => {
    if (!token || !taskId || !task) return;
    let cancelled = false;
    const ac = new AbortController();

    async function softPullFiles() {
      if (cancelled || document.visibilityState === "hidden") return;
      const gen = genRef.current;
      try {
        const page = await fetchThread("?files=1&limit=30", ac.signal);
        if (!page || cancelled || gen !== genRef.current) return;
        setItems(page.items);
        setHasMore(page.has_more);
      } catch {
        // next interval retries
      }
    }

    const interval = window.setInterval(() => void softPullFiles(), 45000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void softPullFiles();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      ac.abort();
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, taskId, task?.id]);

  useTaskLiveSync({
    token,
    taskId,
    task,
    portalId: task?.portal_id ?? portal?.id ?? null,
    draftTitle,
    draftDescription,
    draftOutcome,
    onUpdate: (data, drafts) => {
      setTask(data);
      if (drafts.title !== undefined) setDraftTitle(drafts.title);
      if (drafts.description !== undefined) setDraftDescription(drafts.description);
      if (drafts.outcome !== undefined) setDraftOutcome(drafts.outcome);
      void reconcileThread(data);
    },
  });

  const threadWithDays = useMemo(() => {
    const rows: ThreadRow[] = [];
    let lastDay = "";
    for (const item of items) {
      const day = item.at.slice(0, 10);
      if (day !== lastDay) {
        rows.push({ type: "day", label: formatDayLabel(item.at) });
        lastDay = day;
      }
      rows.push({ type: "item", item });
    }
    return rows;
  }, [items]);

  // Infinite scroll upward: load older messages when near the top.
  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    const onScroll = () => {
      if (el.scrollTop < 140 && hasMore && !loadingOlder) void loadOlder();
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [hasMore, loadingOlder]);

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

  async function commitOutcome() {
    if (!task || !canManage) return;
    const outcome = draftOutcome;
    if (outcome === (task.outcome || "")) return;
    await patchTask({ outcome }, "Итог обновлён");
  }

  async function setStatus(status: TaskStatus) {
    if (!token || !task || !canChangeStatus || task.status === status) return;
    if (status === "done") {
      setCompleteOpen(true);
      return;
    }
    // Optimistic UI so a slow in-flight ?pull=1 cannot flash the old status
    // back over Complete/Pause before the PATCH response arrives.
    const prev = task;
    const optimisticAt = new Date().toISOString();
    setTask({ ...task, status, updated_at: optimisticAt });
    setSaveBusy(true);
    setError(null);
    try {
      const updated = await api<Task>(
        `/api/tasks/${task.id}/`,
        { method: "PATCH", body: JSON.stringify({ status }) },
        token
      );
      setTask(updated);
      setDraftTitle(updated.title);
      setDraftDescription(updated.description || "");
      setDraftOutcome(updated.outcome || "");
      window.dispatchEvent(new Event("projects-updated"));
    } catch (err) {
      setTask(prev);
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSaveBusy(false);
    }
  }

  async function completeWithOutcome(outcome: string) {
    if (!token || !task || !canChangeStatus) return;
    const trimmed = outcome.trim();
    if (!trimmed) {
      setError("Укажите итог работы перед завершением");
      return;
    }
    const prev = task;
    const optimisticAt = new Date().toISOString();
    setTask({ ...task, status: "done", outcome: trimmed, updated_at: optimisticAt });
    setDraftOutcome(trimmed);
    setSaveBusy(true);
    setError(null);
    try {
      const updated = await api<Task>(
        `/api/tasks/${task.id}/`,
        { method: "PATCH", body: JSON.stringify({ status: "done", outcome: trimmed }) },
        token
      );
      setTask(updated);
      setDraftTitle(updated.title);
      setDraftDescription(updated.description || "");
      setDraftOutcome(updated.outcome || "");
      setCompleteOpen(false);
      window.dispatchEvent(new Event("projects-updated"));
      toast.show("Итог сохранён в задаче и попадёт в отчёт", "Задача завершена");
    } catch (err) {
      setTask(prev);
      setDraftOutcome(prev.outcome || "");
      setError(err instanceof Error ? err.message : "Не удалось завершить");
    } finally {
      setSaveBusy(false);
    }
  }

  async function setDueDate(iso: string) {
    if (!task || !canEditDueDate) return;
    const next = iso || null;
    if (next === task.due_date) return;
    await patchTask({ due_date: next });
  }

  async function toggleImportant() {
    if (!task || !canManage) return;
    const next = !task.is_important;
    await patchTask(
      { is_important: next },
      next ? "Задача отмечена как важная" : "Отметка «Важная» снята"
    );
  }

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const list = e.target.files;
    if (!list?.length) {
      console.info("[nextgen-attach] onPickFiles: empty FileList");
      return;
    }
    const files = Array.from(list);
    console.info("[nextgen-attach] onPickFiles → pending", {
      added: files.map((f) => ({ name: f.name, size: f.size, type: f.type })),
    });
    setPendingFiles((prev) => {
      const next = normalizePendingFiles([...prev, ...files]);
      console.info("[nextgen-attach] pendingFiles now", next.length);
      return next;
    });
    e.target.value = "";
  }

  function onAddFiles(files: File[]) {
    if (!files.length) return;
    console.info("[nextgen-attach] onAddFiles", files.map((f) => f.name));
    setPendingFiles((prev) => normalizePendingFiles([...prev, ...files]));
  }

  function removePending(index: number) {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
  }

  async function uploadAttachment(file: File, opts: { taskId: number; commentId?: number }) {
    if (!token) {
      console.warn("[nextgen-attach] upload skipped: no token");
      return;
    }
    const form = new FormData();
    form.append("task", String(opts.taskId));
    if (opts.commentId) form.append("comment", String(opts.commentId));
    form.append("file", file);
    console.info("[nextgen-attach] POST /api/attachments/", {
      name: file.name,
      size: file.size,
      taskId: opts.taskId,
      commentId: opts.commentId,
    });
    try {
      const res = await api("/api/attachments/", { method: "POST", body: form }, token);
      console.info("[nextgen-attach] upload ok", res);
      return res;
    } catch (err) {
      console.error("[nextgen-attach] upload failed", err);
      throw err;
    }
  }

  async function sendMessage(e?: React.FormEvent) {
    e?.preventDefault();
    if (!token || !task) {
      console.warn("[nextgen-attach] sendMessage blocked", { hasToken: !!token, hasTask: !!task });
      return;
    }
    const text = comment.trim();
    const files = pendingFiles;
    if (!text && files.length === 0) return;

    console.info("[nextgen-attach] sendMessage", {
      textLen: text.length,
      files: files.map((f) => f.name),
      taskId: task.id,
    });

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
      console.info("[nextgen-attach] comment created", created.id);

      for (const file of files) {
        await uploadAttachment(file, {
          taskId: task.id,
          commentId: created.id,
        });
      }

      setComment("");
      setPendingFiles([]);
      await refreshAfterSend();
      toast.show(text ? "Сообщение отправлено" : "Файл отправлен");
    } catch (err) {
      console.error("[nextgen-attach] sendMessage failed", err);
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
        {error ? (
          <div className="stack" style={{ gap: 12 }}>
            <div className="error-banner">{error}</div>
            <Link to="/" className="task-back" title="Назад">
              <span className="task-back-label">Вернуться назад</span>
            </Link>
          </div>
        ) : (
          <div className="muted">Загрузка задачи…</div>
        )}
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
          {threadSyncing ? <SyncHint>Обновляем чат…</SyncHint> : null}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <FlashToast message={toast.message} leaving={toast.leaving} />

      <TaskCompleteModal
        open={completeOpen}
        taskTitle={task.title}
        initialOutcome={task.outcome || ""}
        busy={saveBusy}
        onCancel={() => !saveBusy && setCompleteOpen(false)}
        onConfirm={(outcome) => void completeWithOutcome(outcome)}
      />

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
            onRequestComplete={() => setCompleteOpen(true)}
            onSetDueDate={(iso) => void setDueDate(iso)}
            onToggleImportant={() => void toggleImportant()}
            draftOutcome={draftOutcome}
            onDraftOutcome={setDraftOutcome}
            onCommitOutcome={() => void commitOutcome()}
          />

          {hasMore ? (
            <div className="chat-load-older muted">
              {loadingOlder ? "Загрузка истории…" : "Прокрутите вверх для истории"}
            </div>
          ) : null}

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
