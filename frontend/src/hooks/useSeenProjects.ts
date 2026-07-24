import { useCallback, useEffect, useState } from "react";

const SEEN_EVENT = "projects-seen-updated";

function storageKey(portalId: number | null): string | null {
  return portalId != null ? `nextgen.projects.seen.${portalId}` : null;
}

function loadIds(key: string | null): Set<number> {
  if (!key || typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return new Set();
    return new Set(
      parsed
        .map((x) => (typeof x === "number" ? x : Number(x)))
        .filter((n) => Number.isFinite(n) && n > 0)
    );
  } catch {
    return new Set();
  }
}

function saveIds(key: string | null, values: Set<number>) {
  if (!key || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(Array.from(values)));
  } catch {
    // quota / private mode
  }
}

function hasKey(key: string | null): boolean {
  if (!key || typeof window === "undefined") return false;
  return window.localStorage.getItem(key) != null;
}

function emitSeenUpdated(portalId: number | null) {
  if (portalId == null || typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(SEEN_EVENT, { detail: { portalId } })
  );
}

/** Per-portal "opened project" tracking for the nav badge. */
export function useSeenProjects(portalId: number | null) {
  const key = storageKey(portalId);
  const [seen, setSeen] = useState<Set<number>>(() => loadIds(key));

  useEffect(() => {
    setSeen(loadIds(key));
  }, [key]);

  useEffect(() => {
    if (portalId == null) return;
    const onSeen = (e: Event) => {
      const detail = (e as CustomEvent<{ portalId?: number }>).detail;
      if (detail?.portalId != null && detail.portalId !== portalId) return;
      setSeen(loadIds(key));
    };
    window.addEventListener(SEEN_EVENT, onSeen);
    return () => window.removeEventListener(SEEN_EVENT, onSeen);
  }, [portalId, key]);

  /** First visit: mark every current project as seen so the badge stays off. */
  const seedIfEmpty = useCallback(
    (projectIds: number[]) => {
      if (!key || portalId == null) return;
      if (hasKey(key)) return;
      const next = new Set(projectIds.filter((id) => Number.isFinite(id) && id > 0));
      saveIds(key, next);
      setSeen(next);
      emitSeenUpdated(portalId);
    },
    [key, portalId]
  );

  const markSeen = useCallback(
    (id: number) => {
      if (!key || portalId == null || !Number.isFinite(id) || id <= 0) return;
      setSeen((prev) => {
        if (prev.has(id)) return prev;
        const next = new Set(prev);
        next.add(id);
        saveIds(key, next);
        emitSeenUpdated(portalId);
        return next;
      });
    },
    [key, portalId]
  );

  const isUnseen = useCallback((id: number) => !seen.has(id), [seen]);

  const unseenCount = useCallback(
    (projects: { id: number }[]) => projects.reduce((n, p) => n + (seen.has(p.id) ? 0 : 1), 0),
    [seen]
  );

  return { markSeen, isUnseen, unseenCount, seedIfEmpty, seen };
}
