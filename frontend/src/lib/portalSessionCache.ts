/** Stale-while-revalidate snapshots in sessionStorage (per browser tab). */

function key(kind: string, portalId: number): string {
  return `nextgen:swr:${kind}:${portalId}`;
}

export function readPortalCache<T>(kind: string, portalId: number): T | null {
  if (!portalId || typeof sessionStorage === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(key(kind, portalId));
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function writePortalCache<T>(kind: string, portalId: number, value: T): void {
  if (!portalId || typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(key(kind, portalId), JSON.stringify(value));
  } catch {
    // quota / private mode — ignore
  }
}

export function clearPortalCache(kind: string, portalId: number): void {
  if (!portalId || typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.removeItem(key(kind, portalId));
  } catch {
    // ignore
  }
}

export const CACHE_PROJECTS = "projects";
export const CACHE_DEAL_HOURS = "deal-hours";
