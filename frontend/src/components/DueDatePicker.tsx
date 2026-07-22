import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  addDays,
  daysInMonth,
  dueMeta,
  mondayIndex,
  parseISODate,
  toISODate,
} from "../lib/dates";

type Props = {
  value: string;
  onChange: (iso: string) => void;
  status?: "todo" | "in_progress" | "done";
  /** button — create forms; inline — clickable date in task card */
  variant?: "button" | "inline";
};

type View = "date" | "time";

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const HOURS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0"));
const MINUTES = Array.from({ length: 12 }, (_, i) => String(i * 5).padStart(2, "0"));

function monthLabel(year: number, month: number): string {
  const raw = new Date(year, month, 1).toLocaleDateString("ru-RU", {
    month: "long",
    year: "numeric",
  });
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

function formatShortDate(d: Date): string {
  return d.toLocaleDateString("ru-RU", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

function formatDotDate(iso: string): string {
  const d = parseISODate(iso);
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  return `${dd}.${mm}.${d.getFullYear()}`;
}

function endOfWeek(from: Date): Date {
  const d = startDay(from);
  const day = (d.getDay() + 6) % 7; // Mon=0
  const toFri = 4 - day;
  if (toFri >= 0) return addDays(d, toFri);
  return addDays(d, toFri + 7);
}

function endOfMonth(from: Date): Date {
  return new Date(from.getFullYear(), from.getMonth() + 1, 0);
}

function startDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function DueDatePicker({
  value,
  onChange,
  status = "todo",
  variant = "button",
}: Props) {
  const todayIso = toISODate(new Date());
  const selected = value ? parseISODate(value) : null;
  const initial = selected || new Date();

  const [open, setOpen] = useState(false);
  const [view, setView] = useState<View>("date");
  const [viewYear, setViewYear] = useState(initial.getFullYear());
  const [viewMonth, setViewMonth] = useState(initial.getMonth());
  const [hour, setHour] = useState("09");
  const [minute, setMinute] = useState("00");
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  function placePopover() {
    const anchor = triggerRef.current || rootRef.current;
    if (!anchor) return;
    const r = anchor.getBoundingClientRect();
    const pop = popoverRef.current;
    const popH = pop?.offsetHeight || (view === "time" ? 320 : 340);
    const popW = Math.min(520, window.innerWidth - 24);
    let top = r.bottom + 8;
    if (top + popH > window.innerHeight - 12) {
      top = Math.max(12, r.top - popH - 8);
    }
    let left = r.left;
    if (left + popW > window.innerWidth - 12) {
      left = Math.max(12, window.innerWidth - popW - 12);
    }
    setPos({ top, left });
  }

  useEffect(() => {
    if (!value) return;
    const d = parseISODate(value);
    setViewYear(d.getFullYear());
    setViewMonth(d.getMonth());
  }, [value]);

  useEffect(() => {
    if (!open) {
      setView("date");
      setPos(null);
      return;
    }
    placePopover();
    const id = window.requestAnimationFrame(() => placePopover());
    function onDoc(e: MouseEvent) {
      const target = e.target as Node;
      if (rootRef.current?.contains(target)) return;
      if (popoverRef.current?.contains(target)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function onReposition() {
      placePopover();
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onReposition);
    window.addEventListener("scroll", onReposition, true);
    return () => {
      window.cancelAnimationFrame(id);
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onReposition);
      window.removeEventListener("scroll", onReposition, true);
    };
  }, [open, view]);

  const meta = dueMeta(value || null, status);

  const presets = useMemo(() => {
    const now = startDay(new Date());
    const items = [
      { id: "today", label: "Сегодня", date: now },
      { id: "tomorrow", label: "Завтра", date: addDays(now, 1) },
      { id: "week_end", label: "В конце недели", date: endOfWeek(now) },
      { id: "week", label: "Через неделю", date: addDays(now, 7) },
      { id: "month_end", label: "В конце месяца", date: endOfMonth(now) },
    ];
    return items.map((item) => ({
      ...item,
      iso: toISODate(item.date),
      subtitle: capitalize(formatShortDate(item.date)),
    }));
  }, [open, value]);

  const cells = useMemo(() => {
    const first = new Date(viewYear, viewMonth, 1);
    const lead = mondayIndex(first);
    const total = daysInMonth(viewYear, viewMonth);
    const prevMonth = viewMonth === 0 ? 11 : viewMonth - 1;
    const prevYear = viewMonth === 0 ? viewYear - 1 : viewYear;
    const prevTotal = daysInMonth(prevYear, prevMonth);
    const items: { key: string; iso: string; day: number; inMonth: boolean; weekend: boolean }[] =
      [];

    for (let i = 0; i < lead; i += 1) {
      const day = prevTotal - lead + i + 1;
      const d = new Date(viewYear, viewMonth - 1, day);
      const wd = mondayIndex(d);
      items.push({
        key: `p-${day}`,
        iso: toISODate(d),
        day,
        inMonth: false,
        weekend: wd >= 5,
      });
    }
    for (let day = 1; day <= total; day += 1) {
      const d = new Date(viewYear, viewMonth, day);
      const wd = mondayIndex(d);
      items.push({
        key: `c-${day}`,
        iso: toISODate(d),
        day,
        inMonth: true,
        weekend: wd >= 5,
      });
    }
    let next = 1;
    while (items.length % 7 !== 0) {
      const d = new Date(viewYear, viewMonth + 1, next);
      const wd = mondayIndex(d);
      items.push({
        key: `n-${next}`,
        iso: toISODate(d),
        day: next,
        inMonth: false,
        weekend: wd >= 5,
      });
      next += 1;
    }
    return items;
  }, [viewYear, viewMonth]);

  function shiftMonth(delta: number) {
    const d = new Date(viewYear, viewMonth + delta, 1);
    setViewYear(d.getFullYear());
    setViewMonth(d.getMonth());
  }

  function pickDate(iso: string) {
    onChange(iso);
  }

  function pickPreset(iso: string) {
    onChange(iso);
    const d = parseISODate(iso);
    setViewYear(d.getFullYear());
    setViewMonth(d.getMonth());
  }

  const timeLabel = `${hour}:${minute}`;
  const triggerLabel = value
    ? `${formatDotDate(value)} ${timeLabel}`
    : "Выбрать срок";
  const inlineLabel = value
    ? `${formatDotDate(value)}${meta.label ? ` · ${meta.label}` : ""}`
    : "Указать срок";

  return (
    <div className={`due-picker${variant === "inline" ? " due-picker-inline" : ""}`} ref={rootRef}>
      {variant === "inline" ? (
        <button
          ref={triggerRef}
          type="button"
          className={`due-inline-link${open ? " open" : ""}${value ? ` ${meta.tone}` : " is-empty"}`}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-haspopup="dialog"
        >
          {inlineLabel}
        </button>
      ) : (
        <div className="due-trigger-row">
          <button
            ref={triggerRef}
            type="button"
            className={`due-trigger${open ? " open" : ""}${value ? ` ${meta.tone}` : ""}`}
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-haspopup="dialog"
          >
            <span className="due-trigger-icon" aria-hidden>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <rect
                  x="3"
                  y="5"
                  width="18"
                  height="16"
                  rx="3"
                  stroke="currentColor"
                  strokeWidth="2"
                />
                <path
                  d="M3 10h18M8 3v4M16 3v4"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            </span>
            <span className="due-trigger-text">
              {value ? (
                <>
                  <strong>{triggerLabel}</strong>
                  <span className="due-trigger-badge">{meta.label}</span>
                </>
              ) : (
                <strong className="due-trigger-placeholder">Выбрать срок</strong>
              )}
            </span>
          </button>

          {value && (
            <button
              type="button"
              className="due-clear-btn"
              onClick={() => onChange("")}
              aria-label="Сбросить срок"
            >
              Сбросить
            </button>
          )}
        </div>
      )}

      {open &&
        createPortal(
          <div
            ref={popoverRef}
            className="bx-due-popover"
            role="dialog"
            aria-label="Выбор срока"
            style={pos ? { top: pos.top, left: pos.left } : { visibility: "hidden" }}
          >
          {view === "time" && (
            <div className="bx-due-time-head">
              <button
                type="button"
                className="bx-due-back"
                onClick={() => setView("date")}
                aria-label="Назад к календарю"
              >
                ‹
              </button>
              <div className="bx-due-time-title">
                {value ? `${formatDotDate(value)} ${timeLabel}` : timeLabel}
              </div>
            </div>
          )}

          <div className="bx-due-body">
            {view === "date" ? (
              <div className="bx-due-cal">
                <div className="bx-due-cal-head">
                  <button
                    type="button"
                    className="bx-due-nav"
                    onClick={() => shiftMonth(-1)}
                    aria-label="Предыдущий месяц"
                  >
                    ‹
                  </button>
                  <div className="bx-due-month">{monthLabel(viewYear, viewMonth)}</div>
                  <button
                    type="button"
                    className="bx-due-nav"
                    onClick={() => shiftMonth(1)}
                    aria-label="Следующий месяц"
                  >
                    ›
                  </button>
                </div>

                <div className="bx-due-weekdays">
                  {WEEKDAYS.map((w, i) => (
                    <span key={w} className={i >= 5 ? "weekend" : undefined}>
                      {w}
                    </span>
                  ))}
                </div>

                <div className="bx-due-grid">
                  {cells.map((cell) => {
                    const isSelected = value === cell.iso;
                    const isToday = cell.iso === todayIso;
                    return (
                      <button
                        key={cell.key}
                        type="button"
                        className={[
                          "bx-due-day",
                          cell.inMonth ? "" : "out",
                          cell.weekend ? "weekend" : "",
                          isSelected ? "selected" : "",
                          isToday ? "today" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                        onClick={() => pickDate(cell.iso)}
                      >
                        {cell.day}
                      </button>
                    );
                  })}
                </div>

                <button
                  type="button"
                  className="bx-due-time-btn"
                  onClick={() => {
                    if (!value) onChange(todayIso);
                    setView("time");
                  }}
                >
                  <span className="bx-due-time-icon" aria-hidden>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                      <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="2" />
                      <path
                        d="M12 8v5l3 2"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </span>
                  {timeLabel}
                </button>
              </div>
            ) : (
              <div className="bx-due-time">
                <div className="bx-due-time-cols">
                  <div className="bx-due-time-col">
                    <div className="bx-due-time-label">Часы</div>
                    <div className="bx-due-hours">
                      {HOURS.map((h) => (
                        <button
                          key={h}
                          type="button"
                          className={`bx-due-slot${hour === h ? " selected" : ""}`}
                          onClick={() => setHour(h)}
                        >
                          {h}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="bx-due-time-divider" aria-hidden />
                  <div className="bx-due-time-col minutes">
                    <div className="bx-due-time-label">Минуты</div>
                    <div className="bx-due-minutes">
                      {MINUTES.map((m) => (
                        <button
                          key={m}
                          type="button"
                          className={`bx-due-slot${minute === m ? " selected" : ""}`}
                          onClick={() => setMinute(m)}
                        >
                          {m}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}

            <div className="bx-due-presets">
              {presets.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={`bx-due-preset${value === p.iso ? " active" : ""}`}
                  onClick={() => pickPreset(p.iso)}
                >
                  <strong>{p.label}</strong>
                  <span>{p.subtitle}</span>
                </button>
              ))}
            </div>
          </div>
        </div>,
          document.body
        )}
    </div>
  );
}
