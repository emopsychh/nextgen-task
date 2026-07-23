import { isValidDate, parseDue } from "./dates";

export function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const startToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const startMsg = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round((startToday.getTime() - startMsg.getTime()) / 86400000);
  if (diffDays === 0) return "сегодня";
  if (diffDays === 1) return "вчера";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

export function formatDueFull(iso: string | null): string {
  if (!iso) return "Не задан";
  const d = parseDue(iso);
  if (!isValidDate(d)) return "Не задан";
  const date = d.toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
  const hasTime = /T|\d{2}:\d{2}/.test(iso);
  if (!hasTime) return date;
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return `${date}, ${time}`;
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** e.g. «2 ч 15 мин» */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  const parts: string[] = [];
  if (hours) parts.push(`${hours} ч`);
  if (minutes || (hours && !secs)) parts.push(`${minutes} мин`);
  else if (!hours) {
    if (secs && !minutes) parts.push(`${secs} сек`);
    else parts.push(`${minutes} мин`);
  }
  if (hours && secs && !minutes) parts.push(`${secs} сек`);
  return parts.length ? parts.join(" ") : "0 мин";
}

/** Live clock mm:ss or h:mm:ss while timer runs */
export function formatTimerClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  if (h > 0) return `${h}:${mm}:${ss}`;
  return `${mm}:${ss}`;
}

function pluralRu(n: number, one: string, few: string, many: string): string {
  const abs = Math.abs(Math.trunc(n)) % 100;
  const n1 = abs % 10;
  if (abs > 10 && abs < 20) return many;
  if (n1 === 1) return one;
  if (n1 >= 2 && n1 <= 4) return few;
  return many;
}

/** Decimal package hours → «15 часов и 3 минуты» */
export function formatPackageHours(value: number | string | null | undefined): string {
  if (value == null || value === "") return "—";
  const n = typeof value === "number" ? value : Number(String(value).replace(",", "."));
  if (!Number.isFinite(n)) return "—";
  const totalMinutes = Math.max(0, Math.round(n * 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0 && minutes === 0) {
    return `0 ${pluralRu(0, "минута", "минуты", "минут")}`;
  }
  const parts: string[] = [];
  if (hours > 0) {
    parts.push(`${hours} ${pluralRu(hours, "час", "часа", "часов")}`);
  }
  if (minutes > 0) {
    parts.push(`${minutes} ${pluralRu(minutes, "минута", "минуты", "минут")}`);
  }
  if (parts.length === 2) return `${parts[0]} и ${parts[1]}`;
  return parts[0];
}

export function asPackageHours(value: number | string | null | undefined): number | null {
  if (value == null || value === "") return null;
  const n = typeof value === "number" ? value : Number(String(value).replace(",", "."));
  return Number.isFinite(n) ? n : null;
}
