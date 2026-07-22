/** Due date / datetime helpers. Values are ISO strings (date or datetime). */

export function toISODate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Local wall-clock datetime without timezone suffix (matches Bitrix writes). */
export function toISODateTime(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  const s = String(d.getSeconds()).padStart(2, "0");
  return `${y}-${m}-${day}T${h}:${min}:${s}`;
}

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

export function addDays(d: Date, n: number): Date {
  const next = new Date(d);
  next.setDate(next.getDate() + n);
  return next;
}

/** Parse due value as local Date (date-only or datetime, with/without Z). */
export function parseDue(iso: string): Date {
  const text = (iso || "").trim();
  if (!text) return new Date(NaN);
  // Date-only YYYY-MM-DD
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    const [y, m, d] = text.split("-").map(Number);
    return new Date(y, m - 1, d);
  }
  // Naive datetime → treat as local wall clock
  const naive = text.match(
    /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/
  );
  if (naive && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(text)) {
    return new Date(
      Number(naive[1]),
      Number(naive[2]) - 1,
      Number(naive[3]),
      Number(naive[4]),
      Number(naive[5]),
      Number(naive[6] || 0)
    );
  }
  return new Date(text);
}

/** @deprecated use parseDue — kept for calendar date grids */
export function parseISODate(iso: string): Date {
  return parseDue(iso.length >= 10 ? iso.slice(0, 10) : iso);
}

export function formatRuDate(iso: string): string {
  return parseDue(iso).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
  });
}

export function formatRuDateLong(iso: string): string {
  return parseDue(iso).toLocaleDateString("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

export type DueTone =
  | "due-none"
  | "due-ok"
  | "due-soon"
  | "due-today"
  | "due-overdue"
  | "due-done";

export function dueMeta(
  due: string | null,
  status: "todo" | "in_progress" | "done" = "todo"
): { label: string; tone: DueTone; detail?: string } {
  if (status === "done") {
    return {
      label: "Завершена",
      tone: "due-done",
      detail: due ? formatRuDate(due) : undefined,
    };
  }
  if (!due) return { label: "Без срока", tone: "due-none" };

  const now = new Date();
  const target = parseDue(due);
  const today = startOfDay(now);
  const targetDay = startOfDay(target);
  const days = Math.round((targetDay.getTime() - today.getTime()) / 86400000);

  if (target.getTime() < now.getTime()) {
    const n = Math.abs(days) || 1;
    return {
      label: n === 1 ? "Просрочено на 1 день" : `Просрочено на ${n} дн.`,
      tone: "due-overdue",
      detail: formatRuDate(due),
    };
  }
  if (days === 0) return { label: "Срок сегодня", tone: "due-today", detail: formatRuDate(due) };
  if (days === 1) return { label: "Завтра", tone: "due-soon", detail: formatRuDate(due) };
  if (days <= 7) return { label: `${days} дн. осталось`, tone: "due-soon", detail: formatRuDate(due) };
  return { label: `${days} дн. осталось`, tone: "due-ok", detail: formatRuDate(due) };
}

export function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

/** Monday-first weekday index 0..6 */
export function mondayIndex(d: Date): number {
  return (d.getDay() + 6) % 7;
}
