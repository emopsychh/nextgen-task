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

export function isValidDate(d: Date): boolean {
  return d instanceof Date && !Number.isNaN(d.getTime());
}

/**
 * Parse due value as local Date.
 * Handles DRF output like 2026-08-07T23:59:59.123456Z and naive wall-clock strings.
 */
export function parseDue(iso: string | null | undefined): Date {
  if (iso == null) return new Date(NaN);
  const text = String(iso).trim();
  if (!text || text === "null" || text === "None" || text === "undefined") {
    return new Date(NaN);
  }

  // Date-only YYYY-MM-DD
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    const [y, m, d] = text.split("-").map(Number);
    return new Date(y, m - 1, d);
  }

  // Extract Y-M-D H:M:S from any ISO-ish string (ignore ms / timezone for wall clock)
  const parts = text.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/
  );
  if (parts) {
    const y = Number(parts[1]);
    const mo = Number(parts[2]);
    const d = Number(parts[3]);
    const h = Number(parts[4] ?? 0);
    const mi = Number(parts[5] ?? 0);
    const s = Number(parts[6] ?? 0);
    // If string has explicit Z / offset, prefer absolute instant via Date.parse
    if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(text) || /[+-]\d{2}:\d{2}/.test(text)) {
      // Normalize "+0000" → "+00:00"; drop excessive fractional digits for Safari
      const normalized = text
        .replace(/(\.\d{3})\d+/, "$1")
        .replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
      const abs = new Date(normalized);
      if (isValidDate(abs)) return abs;
    }
    const local = new Date(y, mo - 1, d, h, mi, s);
    if (isValidDate(local)) return local;
  }

  const fallback = new Date(text);
  return fallback;
}

/** @deprecated use parseDue — kept for calendar date grids */
export function parseISODate(iso: string): Date {
  const text = (iso || "").trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) {
    return parseDue(text.slice(0, 10));
  }
  return parseDue(text);
}

export function formatRuDate(iso: string): string {
  const d = parseDue(iso);
  if (!isValidDate(d)) return "";
  return d.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
  });
}

export function formatRuDateLong(iso: string): string {
  const d = parseDue(iso);
  if (!isValidDate(d)) return "";
  return d.toLocaleDateString("ru-RU", {
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
  due: string | null | undefined,
  status: "todo" | "in_progress" | "done" = "todo"
): { label: string; tone: DueTone; detail?: string } {
  if (status === "done") {
    const detail = due ? formatRuDate(due) : undefined;
    return {
      label: "Завершена",
      tone: "due-done",
      detail: detail || undefined,
    };
  }
  if (!due) return { label: "Без срока", tone: "due-none" };

  const target = parseDue(due);
  if (!isValidDate(target)) {
    return { label: "Без срока", tone: "due-none" };
  }

  const now = new Date();
  const today = startOfDay(now);
  const targetDay = startOfDay(target);
  const days = Math.round((targetDay.getTime() - today.getTime()) / 86400000);
  if (!Number.isFinite(days)) {
    return { label: "Без срока", tone: "due-none" };
  }

  const detail = formatRuDate(due) || undefined;

  if (target.getTime() < now.getTime()) {
    const n = Math.max(1, Math.abs(days));
    return {
      label: n === 1 ? "Просрочено на 1 день" : `Просрочено на ${n} дн.`,
      tone: "due-overdue",
      detail,
    };
  }
  if (days === 0) return { label: "Срок сегодня", tone: "due-today", detail };
  if (days === 1) return { label: "Завтра", tone: "due-soon", detail };
  if (days <= 7) return { label: `${days} дн. осталось`, tone: "due-soon", detail };
  return { label: `${days} дн. осталось`, tone: "due-ok", detail };
}

export function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

/** Monday-first weekday index 0..6 */
export function mondayIndex(d: Date): number {
  return (d.getDay() + 6) % 7;
}
