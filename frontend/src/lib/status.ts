import type { TaskStatus } from "../api/types";
import { parseISODate, startOfDay } from "./dates";

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

export const SYNC_LABEL: Record<string, string> = {
  pending: "Ожидает синхронизации",
  synced: "Синхронизировано с Bitrix",
  error: "Ошибка синхронизации",
  skipped: "Без Bitrix",
};

export function isTaskOverdue(dueDate: string | null | undefined, status: TaskStatus): boolean {
  if (!dueDate || status === "done") return false;
  const today = startOfDay(new Date());
  const due = startOfDay(parseISODate(dueDate));
  return due.getTime() < today.getTime();
}
