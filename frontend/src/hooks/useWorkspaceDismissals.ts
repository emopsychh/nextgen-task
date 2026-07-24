import { useCallback, useEffect, useState } from "react";

function loadSet(storageKey: string | null): Set<string> {
  if (!storageKey || typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((x): x is string => typeof x === "string"));
  } catch {
    return new Set();
  }
}

function saveSet(storageKey: string | null, values: Set<string>) {
  if (!storageKey || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(Array.from(values)));
  } catch {
    // quota / private mode
  }
}

function itemKey(kind: string, id: number, stamp?: string | null): string {
  return `${kind}:${id}:${stamp || ""}`;
}

/** Persist "opened from workspace" so cards don't pile up forever. */
export function useWorkspaceDismissals(portalId: number | null) {
  const storageKey = portalId ? `nextgen.ws.dismissed.${portalId}` : null;
  const [dismissed, setDismissed] = useState<Set<string>>(() => loadSet(storageKey));

  useEffect(() => {
    setDismissed(loadSet(storageKey));
  }, [storageKey]);

  const isDismissed = useCallback(
    (kind: string, id: number, stamp?: string | null) =>
      dismissed.has(itemKey(kind, id, stamp)),
    [dismissed]
  );

  const dismiss = useCallback(
    (kind: string, id: number, stamp?: string | null) => {
      const key = itemKey(kind, id, stamp);
      setDismissed((prev) => {
        if (prev.has(key)) return prev;
        const next = new Set(prev);
        next.add(key);
        saveSet(storageKey, next);
        return next;
      });
    },
    [storageKey]
  );

  return { dismiss, isDismissed };
}
