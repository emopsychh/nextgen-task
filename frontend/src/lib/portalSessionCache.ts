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

function boardKey(projectId: number, filter: string, query: string): string {
  return `nextgen:swr:board:${projectId}:${filter}:${query}`;
}

export type BoardTasksCache = {
  tasks: unknown[];
  hasMore: boolean;
};

export function readBoardTasksCache(
  projectId: number,
  filter: string,
  query: string
): BoardTasksCache | null {
  if (!projectId || typeof sessionStorage === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(boardKey(projectId, filter, query));
    if (!raw) return null;
    return JSON.parse(raw) as BoardTasksCache;
  } catch {
    return null;
  }
}

export function writeBoardTasksCache(
  projectId: number,
  filter: string,
  query: string,
  value: BoardTasksCache
): void {
  if (!projectId || typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(boardKey(projectId, filter, query), JSON.stringify(value));
  } catch {
    // ignore
  }
}

export const CACHE_PROJECTS = "projects";
export const CACHE_DEAL_HOURS = "deal-hours";
