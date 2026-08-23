/** Timezone helpers for due dates (no external date libs). */

export const AGENCY_DISPLAY_TZ = "Europe/Moscow";

/** IANA zone of the current browser — specialists and clients sit in different cities. */
export function viewerTimeZone(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (tz) return tz;
  } catch {
    /* ignore */
  }
  return AGENCY_DISPLAY_TZ;
}

export function displayTimeZone(_opts?: {
  role?: string | null;
  portalTimezone?: string | null;
  taskTimezone?: string | null;
}): string {
  return viewerTimeZone();
}

export function timeZoneShortName(timeZone: string, at: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat("ru-RU", {
      timeZone,
      timeZoneName: "shortOffset",
    }).formatToParts(at);
    const name = parts.find((part) => part.type === "timeZoneName")?.value;
    if (name) return name.replace("GMT", "UTC");
  } catch {
    /* shortOffset is missing in some engines */
  }
  try {
    const parts = new Intl.DateTimeFormat("ru-RU", {
      timeZone,
      timeZoneName: "short",
    }).formatToParts(at);
    return parts.find((part) => part.type === "timeZoneName")?.value || timeZone;
  } catch {
    return timeZone;
  }
}

type ZonedParts = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
};

export function getZonedParts(date: Date, timeZone: string): ZonedParts {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const map: Record<string, string> = {};
  for (const part of dtf.formatToParts(date)) {
    if (part.type !== "literal") map[part.type] = part.value;
  }
  return {
    year: Number(map.year),
    month: Number(map.month),
    day: Number(map.day),
    hour: Number(map.hour),
    minute: Number(map.minute),
    second: Number(map.second),
  };
}

/** Interpret wall-clock Y-M-D H:M:S in ``timeZone`` as an absolute Date. */
export function zonedWallToUtcDate(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
  timeZone: string
): Date {
  const utc = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  const asInZone = getZonedParts(utc, timeZone);
  const asUtcMs = Date.UTC(
    asInZone.year,
    asInZone.month - 1,
    asInZone.day,
    asInZone.hour,
    asInZone.minute,
    asInZone.second
  );
  const offset = asUtcMs - utc.getTime();
  return new Date(utc.getTime() - offset);
}

export function formatInTimeZone(
  date: Date,
  timeZone: string,
  options: Intl.DateTimeFormatOptions
): string {
  return new Intl.DateTimeFormat("ru-RU", { ...options, timeZone }).format(date);
}

/** Build `YYYY-MM-DDTHH:MM:SSZ` from wall clock in ``timeZone``. */
export function wallToUtcIso(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  second: number,
  timeZone: string
): string {
  const d = zonedWallToUtcDate(year, month, day, hour, minute, second, timeZone);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const h = String(d.getUTCHours()).padStart(2, "0");
  const mi = String(d.getUTCMinutes()).padStart(2, "0");
  const s = String(d.getUTCSeconds()).padStart(2, "0");
  return `${y}-${m}-${dd}T${h}:${mi}:${s}Z`;
}
