import type { TaskStatus } from "../api/types";
import { isValidDate, parseDue } from "./dates";

/** Bitrix-aligned labels for local statuses */
export const STATUS_LABEL: Record<TaskStatus, string> = {
  todo: "Ждёт выполнения",
  in_progress: "Выполняется",
  done: "Завершена",
};

export const STATUS_TONE: Record<TaskStatus, string> = {
  todo: "status-todo",
  in_progress: "status-progress",
  done: "status-done",
};

export function isTaskOverdue(dueDate: string | null | undefined, status: TaskStatus): boolean {
  if (!dueDate || status === "done") return false;
  const d = parseDue(dueDate);
  if (!isValidDate(d)) return false;
  return d.getTime() < Date.now();
}
