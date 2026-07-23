import { useEffect, useRef } from "react";
import { API_BASE, refreshAccessToken } from "../api/types";

type Options = {
  token: string | null;
  portalId: number | string | null | undefined;
  enabled?: boolean;
  /** Called when portal data may have changed (SSE or cursor bump). */
  onEvent: (payload?: {
    kind?: string;
    task_id?: number;
    project_id?: number;
    report_id?: number;
    v?: number;
  }) => void;
};

/**
 * Soft realtime for a portal: prefer SSE, fall back to cursor polling every 2s.
 */
export function usePortalLiveSync({ token, portalId, enabled = true, onEvent }: Options) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const lastV = useRef(0);

  useEffect(() => {
    if (!token || !portalId || !enabled) return;

    let cancelled = false;
    let es: EventSource | null = null;
    let pollTimer: number | undefined;

    function handlePayload(raw: string) {
      try {
        const data = JSON.parse(raw) as {
          v?: number;
          kind?: string;
          task_id?: number;
          hello?: boolean;
        };
        if (typeof data.v === "number") lastV.current = data.v;
        if (data.hello) return;
        onEventRef.current(data);
      } catch {
        onEventRef.current({});
      }
    }

    // The app JWT is NEVER put in a URL. EventSource (which can't send headers)
    // is authorised by a short-lived signed stream token minted over a normal
    // authenticated fetch; the cursor poll uses the Authorization header.
    async function connect() {
      try {
        const res = await fetch(`${API_BASE}/api/stream/token/?portal=${portalId}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (cancelled) return;
        if (res.status === 401) {
          // Access token expired — refresh; the token change re-runs this effect.
          void refreshAccessToken();
          return;
        }
        if (!res.ok) throw new Error("stream token failed");
        const { t } = (await res.json()) as { t: string };
        if (cancelled || !t) throw new Error("stream token missing");
        es = new EventSource(`${API_BASE}/api/stream/?portal=${portalId}&t=${encodeURIComponent(t)}`);
        es.onmessage = (ev) => {
          if (!cancelled) handlePayload(ev.data);
        };
        // Fall back to cursor poll if SSE dies (close to stop reconnect storm)
        es.onerror = () => {
          try {
            es?.close();
          } catch {
            // ignore
          }
          es = null;
          if (!cancelled && pollTimer == null) startPoll();
        };
      } catch {
        if (!cancelled) startPoll();
      }
    }

    function startPoll() {
      if (pollTimer != null) return;
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
          // ignore
        }
      }
      void tick();
      pollTimer = window.setInterval(() => void tick(), 2000);
    }

    void connect();

    return () => {
      cancelled = true;
      es?.close();
      if (pollTimer != null) window.clearInterval(pollTimer);
    };
  }, [token, portalId, enabled]);
}
