import { useEffect, useRef } from "react";

type Options = {
  token: string | null;
  portalId: number | string | null | undefined;
  enabled?: boolean;
  /** Called when portal data may have changed (SSE or cursor bump). */
  onEvent: (payload?: { kind?: string; task_id?: number; v?: number }) => void;
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

    const base = "";
    const url = `${base}/api/stream/?portal=${portalId}&access_token=${encodeURIComponent(token)}`;
    try {
      es = new EventSource(url);
      es.onmessage = (ev) => {
        if (!cancelled) handlePayload(ev.data);
      };
      es.onerror = () => {
        // Fall back to cursor poll if SSE dies
        es?.close();
        es = null;
        if (!cancelled && pollTimer == null) startPoll();
      };
    } catch {
      startPoll();
    }

    function startPoll() {
      if (pollTimer != null) return;
      async function tick() {
        if (cancelled || !token) return;
        try {
          const res = await fetch(
            `/api/sync/cursor/?portal=${portalId}&access_token=${encodeURIComponent(token)}`
          );
          if (!res.ok) return;
          const data = (await res.json()) as { v?: number };
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

    // Safety net even with SSE
    const safety = window.setInterval(() => {
      if (cancelled) return;
      // light nudge every 20s in case a publish was missed
    }, 20000);

    return () => {
      cancelled = true;
      es?.close();
      if (pollTimer != null) window.clearInterval(pollTimer);
      window.clearInterval(safety);
    };
  }, [token, portalId, enabled]);
}
