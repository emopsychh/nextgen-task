export function toISODate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

export function addDays(d: Date, n: number): Date {
  const next = new Date(d);
  next.setDate(next.getDate() + n);
  return next;
}

export function parseISODate(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function formatRuDate(iso: string): string {
  return parseISODate(iso).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
  });
}

export function formatRuDateLong(iso: string): string {
  return parseISODate(iso).toLocaleDateString("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

export type DueTone = "due-none" | "due-ok" | "due-soon" | "due-today" | "due-overdue" | "due-done";

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

  const today = startOfDay(new Date());
  const target = startOfDay(parseISODate(due));
  const days = Math.round((target.getTime() - today.getTime()) / 86400000);

  if (days < 0) {
    const n = Math.abs(days);
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
