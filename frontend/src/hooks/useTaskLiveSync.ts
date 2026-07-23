import { useEffect, useRef } from "react";
import { api, type Task } from "../api/types";
import { usePortalLiveSync } from "./usePortalLiveSync";

const POLL_MS = 2000;
const PULL_EVERY = 6; // Bitrix pull ~ every 12s

function fingerprint(task: Task): string {
  // Chat history is no longer inlined on the task; we rely on lightweight
  // activity signals to detect new messages/files cheaply on each poll.
  const timer = task.active_timer?.started_at || "";
  const tracked = task.total_tracked_seconds || 0;
  const paid = task.deal_paid_hours ?? "";
  const remaining = task.deal_remaining_hours ?? "";
  return [
    task.updated_at,
    task.status,
    task.title,
    task.description || "",
    task.outcome || "",
    task.due_date || "",
    task.comments_count ?? 0,
    task.last_comment_id ?? 0,
    task.files_count ?? 0,
    task.last_file_id ?? 0,
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
  draftOutcome: string;
  onUpdate: (
    task: Task,
    drafts: { title?: string; description?: string; outcome?: string }
  ) => void;
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
  draftOutcome,
  onUpdate,
}: Options) {
  const onUpdateRef = useRef(onUpdate);
  const draftRef = useRef({
    title: draftTitle,
    description: draftDescription,
    outcome: draftOutcome,
  });
  const serverRef = useRef<{ title: string; description: string; outcome: string } | null>(
    null
  );
  const fpRef = useRef<string>("");
  const pullNowRef = useRef(false);
  // Ignore in-flight polls that raced a local PATCH (e.g. Complete → done
  // was overwritten by a slow ?pull=1 that still had in_progress).
  const updatedAtRef = useRef<string>("");

  onUpdateRef.current = onUpdate;
  draftRef.current = {
    title: draftTitle,
    description: draftDescription,
    outcome: draftOutcome,
  };

  useEffect(() => {
    if (!task) return;
    serverRef.current = {
      title: task.title,
      description: task.description || "",
      outcome: task.outcome || "",
    };
    fpRef.current = fingerprint(task);
    if (task.updated_at && (!updatedAtRef.current || task.updated_at >= updatedAtRef.current)) {
      updatedAtRef.current = task.updated_at;
    }
  }, [task]);

  useEffect(() => {
    fpRef.current = "";
    serverRef.current = null;
    updatedAtRef.current = "";
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
        // Drop stale responses that predate a local save (Complete/Pause/etc.).
        if (
          updatedAtRef.current &&
          data.updated_at &&
          data.updated_at < updatedAtRef.current
        ) {
          return;
        }
        const fp = fingerprint(data);
        if (fp === fpRef.current) return;

        const drafts: { title?: string; description?: string; outcome?: string } = {};
        const prevServer = serverRef.current;
        const local = draftRef.current;
        if (prevServer && local.title === prevServer.title) {
          drafts.title = data.title;
        }
        if (prevServer && local.description === prevServer.description) {
          drafts.description = data.description || "";
        }
        if (prevServer && local.outcome === prevServer.outcome) {
          drafts.outcome = data.outcome || "";
        }
        if (data.updated_at) updatedAtRef.current = data.updated_at;
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
