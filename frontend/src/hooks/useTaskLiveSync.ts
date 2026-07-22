import { useEffect, useRef } from "react";
import { api, type Task } from "../api/types";

const POLL_MS = 2500;

function fingerprint(task: Task): string {
  const comments = task.comments || [];
  const files = task.attachments || [];
  const lastComment = comments.length ? comments[comments.length - 1]?.id : 0;
  const lastFile = files.length ? files[files.length - 1]?.id : 0;
  const timer = task.active_timer?.started_at || "";
  const tracked = task.total_tracked_seconds || 0;
  const paid = task.deal_paid_hours ?? "";
  const remaining = task.deal_remaining_hours ?? "";
  return [
    task.updated_at,
    task.status,
    task.title,
    task.description || "",
    task.due_date || "",
    comments.length,
    lastComment,
    files.length,
    lastFile,
    timer,
    tracked,
    paid,
    remaining,
  ].join("|");
}

type Options = {
  token: string | null;
  taskId: string | undefined;
  task: Task | null;
  enabled?: boolean;
  /** Current drafts — used so we don't overwrite in-progress edits. */
  draftTitle: string;
  draftDescription: string;
  onUpdate: (task: Task, drafts: { title?: string; description?: string }) => void;
};

/**
 * Soft realtime: poll the task while the tab is visible.
 * Enough for chat + status without WebSockets on the current stack.
 */
export function useTaskLiveSync({
  token,
  taskId,
  task,
  enabled = true,
  draftTitle,
  draftDescription,
  onUpdate,
}: Options) {
  const onUpdateRef = useRef(onUpdate);
  const draftRef = useRef({ title: draftTitle, description: draftDescription });
  const serverRef = useRef<{ title: string; description: string } | null>(null);
  const fpRef = useRef<string>("");

  onUpdateRef.current = onUpdate;
  draftRef.current = { title: draftTitle, description: draftDescription };

  useEffect(() => {
    if (!task) return;
    serverRef.current = {
      title: task.title,
      description: task.description || "",
    };
    fpRef.current = fingerprint(task);
  }, [task]);

  useEffect(() => {
    fpRef.current = "";
    serverRef.current = null;
  }, [taskId]);

  useEffect(() => {
    if (!token || !taskId || !enabled) return;

    let cancelled = false;
    let inFlight = false;
    let tickCount = 0;

    async function tick() {
      if (cancelled || inFlight) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      inFlight = true;
      tickCount += 1;
      try {
        // Every ~5th poll (~12s) also pull Bitrix comments/status so chat stays in sync
        // even if webhooks are slow; other ticks only read local DB.
        const pull = tickCount === 1 || tickCount % 5 === 0 ? "?pull=1" : "";
        const data = await api<Task>(`/api/tasks/${taskId}/${pull}`, {}, token);
        if (cancelled) return;
        const fp = fingerprint(data);
        if (fp === fpRef.current) return;

        const drafts: { title?: string; description?: string } = {};
        const prevServer = serverRef.current;
        const local = draftRef.current;
        if (prevServer && local.title === prevServer.title) {
          drafts.title = data.title;
        }
        if (prevServer && local.description === prevServer.description) {
          drafts.description = data.description || "";
        }
        onUpdateRef.current(data, drafts);
      } catch {
        // Ignore transient poll errors; next tick retries.
      } finally {
        inFlight = false;
      }
    }

    const onVisible = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    const id = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [token, taskId, enabled]);
}
