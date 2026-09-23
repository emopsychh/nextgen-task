import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Link } from "react-router-dom";
import type { Project, Task } from "../api/types";
import { addDays, isValidDate, parseDue, startOfDay } from "../lib/dates";
import { isProjectComplete, projectProgress } from "../lib/projectProgress";

const DAY_MS = 86400000;
const BAR_H = 20;
const ZOOM_MIN = 10;
const ZOOM_MAX = 48;
const ZOOM_DEFAULT = 22;
const ZOOM_STORAGE = "nextgen-gantt-zoom";
const NAME_MIN = 240;
const NAME_MAX = 560;
const NAME_DEFAULT = 380;
const NAME_STORAGE = "nextgen-gantt-name-w";
const MONTHS_RU = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];

type BarTone = "active" | "working" | "done" | "overdue";

type TimelineBar = {
  id: number;
  name: string;
  href: string;
  start: Date;
  end: Date;
  openEnded: boolean;
  tone: BarTone;
  pct: number;
  complete: boolean;
};

function dayOffset(from: Date, to: Date): number {
  return Math.round((startOfDay(to).getTime() - startOfDay(from).getTime()) / DAY_MS);
}

function eachDay(from: Date, to: Date): Date[] {
  const days: Date[] = [];
  const last = startOfDay(to);
  let cursor = startOfDay(from);
  while (cursor.getTime() <= last.getTime()) {
    days.push(cursor);
    cursor = addDays(cursor, 1);
  }
  return days;
}

function projectBar(project: Project, today: Date): TimelineBar {
  const created = parseDue(project.created_at);
  const due = parseDue(project.due_date);
  const completed = parseDue(project.completed_at);
  const start = isValidDate(created) ? startOfDay(created) : today;
  const complete = isProjectComplete(project);
  let end = today;
  let openEnded = false;
  if (complete && isValidDate(completed)) {
    end = startOfDay(completed);
  } else if (isValidDate(due)) {
    end = startOfDay(due);
  } else {
    openEnded = true;
    end = addDays(today, 7);
    if (end.getTime() < start.getTime()) end = start;
  }
  if (end.getTime() < start.getTime()) end = start;
  const { pct } = projectProgress(project);
  let tone: BarTone = "active";
  if (complete) tone = "done";
  else if (isValidDate(due) && startOfDay(due).getTime() < today.getTime()) tone = "overdue";
  else if (project.has_active_work) tone = "working";
  return { id: project.id, name: project.name, href: `/projects/${project.id}`, start, end, openEnded, tone, pct, complete };
}

function taskBar(task: Task, today: Date): TimelineBar {
  const created = parseDue(task.created_at);
  const due = parseDue(task.due_date);
  const completed = parseDue(task.completed_at);
  const start = isValidDate(created) ? startOfDay(created) : today;
  const complete = task.status === "done";
  let end = today;
  let openEnded = false;
  if (complete && isValidDate(completed)) {
    end = startOfDay(completed);
  } else if (isValidDate(due)) {
    end = startOfDay(due);
  } else {
    openEnded = true;
    end = addDays(today, 7);
  }
  if (end.getTime() < start.getTime()) end = start;
  let tone: BarTone = "active";
  if (complete) tone = "done";
  else if (isValidDate(due) && startOfDay(due).getTime() < today.getTime()) tone = "overdue";
  else if (task.status === "in_progress" || task.is_working) tone = "working";
  const pct = complete ? 100 : task.status === "in_progress" ? 50 : 0;
  return { id: task.id, name: task.title, href: `/tasks/${task.id}`, start, end, openEnded, tone, pct, complete };
}

function toneLabel(tone: BarTone, openEnded: boolean): string {
  if (tone === "done") return "Завершён";
  if (tone === "overdue") return "Просрочен";
  if (tone === "working") return "Сейчас в работе";
  if (openEnded) return "Активный · без срока";
  return "Активный";
}

function formatDay(d: Date): string {
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function readNameWidth(): number {
  try {
    const raw = Number(window.localStorage.getItem(NAME_STORAGE));
    if (Number.isFinite(raw)) return Math.min(NAME_MAX, Math.max(NAME_MIN, raw));
  } catch {
    /* ignore */
  }
  return NAME_DEFAULT;
}

function writeNameWidth(width: number) {
  try {
    window.localStorage.setItem(NAME_STORAGE, String(width));
  } catch {
    /* ignore */
  }
}

function readZoom(): number {
  try {
    const raw = Number(window.localStorage.getItem(ZOOM_STORAGE));
    if (Number.isFinite(raw)) return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, raw));
  } catch {
    /* ignore */
  }
  return ZOOM_DEFAULT;
}

function writeZoom(zoom: number) {
  try {
    window.localStorage.setItem(ZOOM_STORAGE, String(zoom));
  } catch {
    /* ignore */
  }
}

type Props = {
  projects?: Project[];
  tasks?: Task[];
  mode?: "projects" | "tasks";
  timeZone: string;
  linkState?: { from: string };
};

function isNameColumn(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(target.closest(".projects-gantt-name, .projects-gantt-corner, .projects-gantt-resizer"));
}

export function ProjectsGantt({ projects = [], tasks = [], mode = "projects", linkState }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [chartW, setChartW] = useState(0);
  const [nameW, setNameW] = useState(readNameWidth);
  const [zoom, setZoom] = useState(readZoom);
  const [panning, setPanning] = useState(false);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);
  const panRef = useRef<{ startX: number; startScroll: number; moved: boolean } | null>(null);
  const suppressClickRef = useRef(false);
  const didInitScroll = useRef(false);
  const zoomAnchorRef = useRef<{ dateMs: number; viewportX: number } | null>(null);
  const zoomRef = useRef(zoom);
  const nameWRef = useRef(nameW);
  const rangeStartRef = useRef(new Date());
  const lastZoomAt = useRef(0);
  zoomRef.current = zoom;
  nameWRef.current = nameW;
  const today = useMemo(() => startOfDay(new Date()), []);
  const bars = useMemo(
    () => mode === "tasks" ? tasks.map((task) => taskBar(task, today)) : projects.map((project) => projectBar(project, today)),
    [mode, projects, tasks, today]
  );

  const { rangeStart, rangeEnd } = useMemo(() => {
    const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
    const nextMonthEnd = new Date(today.getFullYear(), today.getMonth() + 2, 0);
    let start = monthStart;
    let end = nextMonthEnd;
    for (const bar of bars) {
      if (bar.start < start) start = bar.start;
      if (bar.end > end) end = bar.end;
    }
    const dayW = zoom;
    const trackAvail = Math.max(320, chartW - nameW);
    const dataDays = Math.max(1, dayOffset(start, end) + 1);
    const fillDays = Math.ceil(trackAvail / dayW);
    if (fillDays > dataDays) {
      const extra = fillDays - dataDays;
      start = addDays(start, -Math.floor(extra / 2));
      end = addDays(end, Math.ceil(extra / 2));
    }
    return { rangeStart: startOfDay(start), rangeEnd: startOfDay(end) };
  }, [bars, today, zoom, chartW, nameW]);

  const days = useMemo(() => eachDay(rangeStart, rangeEnd), [rangeStart, rangeEnd]);
  const dayW = zoom;
  const trackWidth = days.length * dayW;
  const todayLeft = Math.max(0, dayOffset(rangeStart, today)) * dayW + dayW / 2;
  rangeStartRef.current = rangeStart;

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const measure = () => setChartW(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [bars.length]);

  useEffect(() => {
    writeNameWidth(nameW);
  }, [nameW]);

  useEffect(() => {
    writeZoom(zoom);
  }, [zoom]);

  useEffect(() => {
    const el = chartRef.current;
    if (!el || !chartW) return;
    const anchor = zoomAnchorRef.current;
    if (anchor) {
      zoomAnchorRef.current = null;
      const dayFloat = (anchor.dateMs - rangeStart.getTime()) / DAY_MS;
      el.scrollLeft = Math.max(0, dayFloat * zoom + nameW - anchor.viewportX);
      return;
    }
    if (didInitScroll.current) return;
    didInitScroll.current = true;
    const trackAvail = Math.max(0, el.clientWidth - nameW);
    el.scrollLeft = Math.max(0, todayLeft - trackAvail / 2);
  }, [zoom, rangeStart, nameW, chartW, todayLeft]);

  useEffect(() => {
    const el = chartRef.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
        el.scrollLeft += event.deltaX;
        event.preventDefault();
        return;
      }
      event.preventDefault();
      const now = Date.now();
      if (now - lastZoomAt.current < 28) return;
      lastZoomAt.current = now;
      const current = zoomRef.current;
      const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, current + (event.deltaY > 0 ? -2 : 2)));
      if (next === current) return;
      const rect = el.getBoundingClientRect();
      const viewportX = event.clientX - rect.left;
      const xInTrack = viewportX + el.scrollLeft - nameWRef.current;
      zoomAnchorRef.current = {
        dateMs: rangeStartRef.current.getTime() + (xInTrack / current) * DAY_MS,
        viewportX,
      };
      setZoom(next);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    function onMove(event: globalThis.PointerEvent) {
      if (dragRef.current) {
        const next = dragRef.current.startW + (event.clientX - dragRef.current.startX);
        setNameW(Math.min(NAME_MAX, Math.max(NAME_MIN, next)));
        return;
      }
      const pan = panRef.current;
      const el = chartRef.current;
      if (!pan || !el) return;
      const dx = event.clientX - pan.startX;
      if (Math.abs(dx) > 3) pan.moved = true;
      el.scrollLeft = pan.startScroll - dx;
    }
    function onUp() {
      if (dragRef.current) {
        dragRef.current = null;
        document.body.classList.remove("is-gantt-resizing");
      }
      if (panRef.current) {
        if (panRef.current.moved) {
          suppressClickRef.current = true;
          window.setTimeout(() => {
            suppressClickRef.current = false;
          }, 80);
        }
        panRef.current = null;
        setPanning(false);
        document.body.classList.remove("is-gantt-panning");
      }
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, []);

  const months = useMemo(() => {
    const items: { key: string; label: string; span: number }[] = [];
    for (const day of days) {
      const key = `${day.getFullYear()}-${day.getMonth()}`;
      const last = items[items.length - 1];
      if (last && last.key === key) last.span += 1;
      else {
        items.push({
          key,
          label: `${MONTHS_RU[day.getMonth()]} ${day.getFullYear()}`,
          span: 1,
        });
      }
    }
    return items;
  }, [days]);

  function startResize(event: ReactPointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    dragRef.current = { startX: event.clientX, startW: nameW };
    document.body.classList.add("is-gantt-resizing");
  }

  function startPan(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    if (isNameColumn(event.target)) return;
    const el = chartRef.current;
    if (!el) return;
    panRef.current = { startX: event.clientX, startScroll: el.scrollLeft, moved: false };
    setPanning(true);
    document.body.classList.add("is-gantt-panning");
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      /* ignore */
    }
  }

  function onChartClickCapture(event: ReactMouseEvent) {
    if (!suppressClickRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    suppressClickRef.current = false;
  }

  return (
    <div className="projects-gantt">
      <div
        className={`projects-gantt-chart${panning ? " is-panning" : ""}`}
        ref={chartRef}
        onPointerDown={startPan}
        onClickCapture={onChartClickCapture}
      >
        <div className="projects-gantt-inner" style={{ minWidth: nameW + trackWidth }}>
          <div className="projects-gantt-head">
            <div className="projects-gantt-corner" style={{ width: nameW }}>
              {mode === "tasks" ? "Задача" : "Проект"}
              <button
                type="button"
                className="projects-gantt-resizer"
                aria-label="Изменить ширину названий"
                onPointerDown={startResize}
              />
            </div>
            <div className="projects-gantt-head-track" style={{ width: trackWidth }}>
              <div className="projects-gantt-months">
                {months.map((month) => (
                  <div key={month.key} className="projects-gantt-month" style={{ width: month.span * dayW }}>
                    {month.label}
                  </div>
                ))}
              </div>
              <div className="projects-gantt-days">
                {days.map((day) => {
                  const weekend = day.getDay() === 0 || day.getDay() === 6;
                  const isToday = day.getTime() === today.getTime();
                  const showNum = dayW >= 24 || day.getDate() === 1 || day.getDate() % 5 === 0 || isToday;
                  return (
                    <div
                      key={day.toISOString()}
                      className={`projects-gantt-day${weekend ? " is-weekend" : ""}${isToday ? " is-today" : ""}`}
                      style={{ width: dayW }}
                    >
                      {showNum ? day.getDate() : ""}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {bars.map((bar) => {
            const left = Math.max(0, dayOffset(rangeStart, bar.start)) * dayW;
            const spanDays = Math.max(1, dayOffset(bar.start, bar.end) + 1);
            const width = Math.max(dayW, spanDays * dayW);
            const showLabel = width >= 96;
            const title = `${bar.name} · ${toneLabel(bar.tone, bar.openEnded)} · ${formatDay(bar.start)} – ${formatDay(bar.end)}`;
            return (
              <div key={bar.id} className="projects-gantt-row">
                <Link
                  to={bar.href}
                  state={linkState}
                  className="projects-gantt-name"
                  style={{ width: nameW }}
                  title={bar.name}
                >
                  <strong>{bar.name}</strong>
                </Link>
                <div className="projects-gantt-row-track" style={{ width: trackWidth }}>
                  {days.map((day, index) =>
                    day.getDay() === 0 || day.getDay() === 6 ? (
                      <span
                        key={`wk-${bar.id}-${day.toISOString()}`}
                        className="projects-gantt-weekend"
                        style={{ left: index * dayW, width: dayW }}
                        aria-hidden
                      />
                    ) : null
                  )}
                  <span className="projects-gantt-today" style={{ left: todayLeft }} aria-hidden />
                  <Link
                    to={bar.href}
                    state={linkState}
                    className={`projects-gantt-bar is-${bar.tone}${bar.openEnded ? " is-open" : ""}`}
                    style={{ left, width, height: BAR_H }}
                    title={title}
                    draggable={false}
                    onDragStart={(event) => event.preventDefault()}
                  >
                    <span className="projects-gantt-bar-fill" style={{ width: `${bar.pct}%` }} />
                    {showLabel ? <span className="projects-gantt-bar-label">{bar.pct}%</span> : null}
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <ul className="projects-gantt-legend">
        <li>
          <i className="is-active" /> Активный
        </li>
        <li>
          <i className="is-working" /> Сейчас в работе
        </li>
        <li>
          <i className="is-done" /> Завершён
        </li>
        <li>
          <i className="is-overdue" /> Просрочен
        </li>
        <li>
          <i className="is-today" /> Сегодня
        </li>
      </ul>
    </div>
  );
}
