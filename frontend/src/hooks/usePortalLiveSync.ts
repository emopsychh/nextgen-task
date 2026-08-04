import { useEffect, useRef } from "react";
import { API_BASE, refreshAccessToken } from "../api/types";

type Options = {
  token: string | null;
  portalId: number | string | null | undefined;
  enabled?: boolean;
  /** Called when portal data may have changed (cursor bump). */
  onEvent: (payload?: {
    kind?: string;
    task_id?: number;
    project_id?: number;
    report_id?: number;
    v?: number;
  }) => void;
};

/**
 * Soft realtime for a portal via cursor polling (every 2s).
 *
 * We intentionally do NOT use EventSource/SSE on gunicorn gthread workers:
 * each open SSE holds a WSGI thread for up to an hour and starves API
 * requests — pages then sit on «Загружаем…» for a minute+.
 */
export function usePortalLiveSync({ token, portalId, enabled = true, onEvent }: Options) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const lastV = useRef(0);

  useEffect(() => {
    if (!token || !portalId || !enabled) return;

    // The cursor belongs to one portal only. Carrying it across route changes
    // can suppress updates for a quieter client or cause a false refresh.
    lastV.current = 0;
    let cancelled = false;
    let pollTimer: number | undefined;

    async function tick() {
      if (cancelled || !token) return;
      try {
        const res = await fetch(`${API_BASE}/api/sync/cursor/?portal=${portalId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (cancelled) return;
        if (res.status === 401) {
          void refreshAccessToken();
          return;
        }
        if (!res.ok) return;
        const data = (await res.json()) as { v?: number };
        if (cancelled) return;
        const v = Number(data.v || 0);
        if (lastV.current && v > lastV.current) {
          onEventRef.current({ v });
        }
        lastV.current = v;
      } catch {
        // ignore transient network errors
      }
    }

    void tick();
    pollTimer = window.setInterval(() => void tick(), 2000);

    return () => {
      cancelled = true;
      if (pollTimer != null) window.clearInterval(pollTimer);
    };
  }, [token, portalId, enabled]);
}
