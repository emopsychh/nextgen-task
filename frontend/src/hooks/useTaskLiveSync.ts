import { useEffect, useRef } from "react";
import { api, type Task } from "../api/types";
import { usePortalLiveSync } from "./usePortalLiveSync";

const POLL_MS = 2000;
const PULL_EVERY = 6; // Bitrix pull ~ every 12s

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
  portalId?: number | null;
  enabled?: boolean;
  draftTitle: string;
  draftDescription: string;
  onUpdate: (task: Task, drafts: { title?: string; description?: string }) => void;
};

/**
 * Soft realtime: SSE for instant refresh + local poll; Bitrix pull sparingly.
 */
export function useTaskLiveSync({
  token,
  taskId,
  task,
  portalId,
  enabled = true,
  draftTitle,
  draftDescription,
  onUpdate,
}: Options) {
  const onUpdateRef = useRef(onUpdate);
  const draftRef = useRef({ title: draftTitle, description: draftDescription });
  const serverRef = useRef<{ title: string; description: string } | null>(null);
  const fpRef = useRef<string>("");
  const pullNowRef = useRef(false);

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

  usePortalLiveSync({
    token,
    portalId: portalId ?? task?.portal_id ?? null,
    enabled: enabled && !!taskId,
    onEvent: () => {
      pullNowRef.current = true;
    },
  });

  useEffect(() => {
    if (!token || !taskId || !enabled) return;

    let cancelled = false;
    let inFlight = false;
    let tickCount = 0;

    async function tick(forcePull = false) {
      if (cancelled || inFlight) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      inFlight = true;
      tickCount += 1;
      try {
        const wantPull =
          forcePull || pullNowRef.current || tickCount % PULL_EVERY === 0;
        pullNowRef.current = false;
        const pull = wantPull ? "?pull=1" : "";
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
      if (document.visibilityState === "visible") void tick(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    // Fast local poll first; Bitrix pull a few seconds later (does not block UI)
    void tick(false);
    const firstPull = window.setTimeout(() => void tick(true), 4000);
    const id = window.setInterval(() => void tick(false), POLL_MS);

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
      window.clearTimeout(firstPull);
      window.clearInterval(id);
    };
  }, [token, taskId, enabled]);
}
