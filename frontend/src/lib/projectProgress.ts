import type { Project } from "../api/types";

export function projectProgress(p: Pick<Project, "tasks_count" | "done_count">): {
  total: number;
  done: number;
  pct: number;
} {
  const total = p.tasks_count || 0;
  const done = p.done_count || 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  return { total, done, pct };
}

/** Projects still in progress (not fully completed). */
export function isProjectInProgress(p: Pick<Project, "tasks_count" | "done_count">): boolean {
  return !isProjectComplete(p);
}

/** Fully completed projects (all tasks done). Empty projects stay open. */
export function isProjectComplete(p: Pick<Project, "tasks_count" | "done_count">): boolean {
  const { pct, total } = projectProgress(p);
  return pct >= 100 && total > 0;
}
