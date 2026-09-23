import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import {
  api,
  isAbortError,
  unwrapList,
  type DealBinding,
  type Paginated,
  type Portal,
  type Project,
  type ProjectMeeting,
  type Task,
  type WorkReport,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { DealHoursCard, hasDealHoursPackage } from "../../components/DealHoursCard";
import { FlashToast } from "../../components/FlashToast";
import { ModalPortal } from "../../components/ModalPortal";
import {
  CalendarGlyph,
  DisputeIcon,
  FlameIcon,
  TaskGlyph,
} from "../../components/icons";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { addDays, formatRuDateTime, isValidDate, parseDue, startOfDay, toISODate } from "../../lib/dates";
import { formatDueFull, formatDuration } from "../../lib/format";
import { PICKER_PAGE_SIZE, withPage } from "../../lib/pagination";
import { linkStateFrom } from "../../lib/smartBack";
import { displayTimeZone, formatInTimeZone } from "../../lib/timezone";
import {
  getPortalLabel,
  PORTAL_LABEL_EVENT,
  portalDisplayName,
  setPortalLabel,
} from "../../lib/portalLabelCache";
import {
  CACHE_DEAL_HOURS,
  CACHE_PROJECTS,
  clearPortalCache,
  readPortalCache,
  writePortalCache,
} from "../../lib/portalSessionCache";
import { isProjectInProgress, projectProgress } from "../../lib/projectProgress";
import { isTaskOverdue, STATUS_LABEL } from "../../lib/status";
import { reportDetailPath } from "../shared/reportHelpers";

function linkify(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const re = /https?:\/\/[^\s<>"']+/g;
  let last = 0;
  for (const match of text.matchAll(re)) {
    const index = match.index ?? 0;
    if (index > last) parts.push(text.slice(last, index));
    const tail = match[0].match(/[),.;]+$/)?.[0] ?? "";
    const url = match[0].slice(0, match[0].length - tail.length);
    parts.push(
      <a key={`${index}-${url}`} href={url} target="_blank" rel="noreferrer">
        {url}
      </a>
    );
    if (tail) parts.push(tail);
    last = index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

const RECENT_DONE_MS = 7 * 24 * 60 * 60 * 1000;
const HOT_DUE_DAYS = 2;
const CACHE_OVERVIEW = "overview";

type OverviewSnapshot = {
  openTasks: Task[];
  recentDone: Task[];
  disputedReports: WorkReport[];
  workingTasks?: Task[];
  meetings?: ProjectMeeting[];
};

type MeetingSlot = {
  starts_at: string;
  label: string;
  available: boolean;
  reason: "" | "past" | "occupied" | "weekend";
};

type MeetingAvailability = {
  date: string;
  timezone: string;
  duration_minutes: number;
  slots: MeetingSlot[];
};

function initialMeetingDate(): string {
  const now = new Date();
  let candidate = now.getHours() >= 17 ? addDays(now, 1) : now;
  while (candidate.getDay() === 0 || candidate.getDay() === 6) candidate = addDays(candidate, 1);
  return toISODate(candidate);
}

function earliestProjectDue(projectId: number, tasks: Task[]): string | null {
  const dues = tasks
    .filter((t) => t.project === projectId && t.status !== "done" && t.due_date)
    .map((t) => t.due_date as string)
    .sort();
  return dues[0] || null;
}

function taskDueLabel(task: Task, timeZone: string): string | null {
  if (!task.due_date) return null;
  return formatDueFull(task.due_date, timeZone);
}

function dueChip(iso: string | null | undefined, timeZone: string): { day: string; month: string } | null {
  if (!iso) return null;
  const date = parseDue(iso);
  if (!isValidDate(date)) return null;
  return {
    day: formatInTimeZone(date, timeZone, { day: "numeric" }),
    month: formatInTimeZone(date, timeZone, { month: "short" }).replace(/\./g, ""),
  };
}

function meetingStamp(iso: string, timeZone: string): { day: string; month: string; time: string } {
  const date = new Date(iso);
  const day = new Intl.DateTimeFormat("ru-RU", { day: "numeric", timeZone }).format(date);
  const month = new Intl.DateTimeFormat("ru-RU", { month: "short", timeZone })
    .format(date)
    .replace(".", "");
  const time = new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  }).format(date);
  return { day, month, time };
}

/** Due today / tomorrow / within N calendar days (not yet overdue). */
function isDueSoon(dueDate: string | null | undefined, status: Task["status"]): boolean {
  if (!dueDate || status === "done") return false;
  if (isTaskOverdue(dueDate, status)) return false;
  const target = parseDue(dueDate);
  if (!isValidDate(target)) return false;
  const today = startOfDay(new Date());
  const targetDay = startOfDay(target);
  const days = Math.round((targetDay.getTime() - today.getTime()) / 86400000);
  return days >= 0 && days <= HOT_DUE_DAYS;
}

function hotPriority(task: Task): number {
  if (isTaskOverdue(task.due_date, task.status)) return 0;
  if (isDueSoon(task.due_date, task.status)) return 1;
  if (task.is_important) return 2;
  return 3;
}

export function ClientProjects() {
  const { token, portal } = useAuth();
  const params = useParams();
  const location = useLocation();
  const fromState = linkStateFrom(location);
  const portalId = Number(params.portalId || portal?.id);
  const isAgency = portal?.role === "agency";
  const toast = useFlashToast();

  const [portalInfo, setPortalInfo] = useState<Portal | null>(null);
  const [dealHours, setDealHours] = useState<DealBinding | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [openTasks, setOpenTasks] = useState<Task[]>([]);
  const [, setRecentDone] = useState<Task[]>([]);
  const [disputedReports, setDisputedReports] = useState<WorkReport[]>([]);
  const [meetings, setMeetings] = useState<ProjectMeeting[]>([]);
  const [showMeetingList, setShowMeetingList] = useState(false);
  const [editingMeetingId, setEditingMeetingId] = useState<number | null>(null);
  const [outcomeDrafts, setOutcomeDrafts] = useState<Record<number, string>>({});
  const [showMeetingForm, setShowMeetingForm] = useState(false);
  const [meetingProjectId, setMeetingProjectId] = useState("");
  const [meetingAt, setMeetingAt] = useState("");
  const [meetingDate, setMeetingDate] = useState(initialMeetingDate);
  const [meetingSlots, setMeetingSlots] = useState<MeetingSlot[]>([]);
  const [meetingSlotsLoading, setMeetingSlotsLoading] = useState(false);
  const [meetingSlotError, setMeetingSlotError] = useState<string | null>(null);
  const [meetingNotes, setMeetingNotes] = useState("");
  const [meetingBusy, setMeetingBusy] = useState(false);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const dueTz = displayTimeZone({
    role: portal?.role,
    portalTimezone: portalInfo?.timezone || portal?.timezone,
  });

  const activeProjects = useMemo(
    () =>
      projects
        .filter(isProjectInProgress)
        .slice()
        .sort((a, b) => {
          const byCreated = String(a.created_at || "").localeCompare(String(b.created_at || ""));
          return byCreated || a.id - b.id;
        })
        .slice(0, 3),
    [projects]
  );

  const hotTasks = useMemo(() => {
    const seen = new Set<number>();
    const out: Task[] = [];
    for (const t of openTasks) {
      const overdue = isTaskOverdue(t.due_date, t.status);
      const soon = isDueSoon(t.due_date, t.status);
      const important = Boolean(t.is_important);
      if (!overdue && !soon && !important) continue;
      if (seen.has(t.id)) continue;
      seen.add(t.id);
      out.push(t);
    }
    out.sort((a, b) => hotPriority(a) - hotPriority(b));
    return out.slice(0, 12);
  }, [openTasks]);

  const nextDueTask = useMemo(
    () =>
      openTasks
        .filter((task) => Boolean(task.due_date))
        .slice()
        .sort((a, b) => String(a.due_date).localeCompare(String(b.due_date)))[0] || null,
    [openTasks]
  );
  const nextDueStamp = nextDueTask ? dueChip(nextDueTask.due_date, dueTz) : null;
  const nextDueOverdue = nextDueTask
    ? isTaskOverdue(nextDueTask.due_date, nextDueTask.status)
    : false;

  const nextMeeting = useMemo(
    () =>
      meetings
        .filter((meeting) => !meeting.cancelled_at && new Date(meeting.scheduled_at).getTime() >= Date.now())
        .slice()
        .sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at))[0] || null,
    [meetings]
  );
  const nextMeetingStamp = nextMeeting ? meetingStamp(nextMeeting.scheduled_at, dueTz) : null;

  const loadGenRef = useRef(0);

  useEffect(() => {
    if (!portalId) {
      setPortalInfo(null);
      return;
    }
    // Route changes reuse this component. Never leave the previous client's
    // identity visible while the next client's data is loading.
    setPortalInfo(null);
    if (!isAgency && portal) {
      setPortalInfo(portal);
      const label = portalDisplayName(portal);
      if (label) setPortalLabel(portal.id, label);
      return;
    }
    const cached = getPortalLabel(portalId);
    if (cached) {
      setPortalInfo({
        id: portalId,
        name: cached,
        domain: "",
        role: "client",
        member_id: "",
        is_active: true,
      } as Portal);
    }
  }, [portalId, isAgency, portal]);

  useEffect(() => {
    if (!isAgency || !portalId) return;
    const onLabel = (event: Event) => {
      const detail = (event as CustomEvent<{ portalId: number; label: string }>).detail;
      if (!detail || detail.portalId !== portalId) return;
      setPortalInfo((prev) =>
        prev && prev.id === portalId
          ? { ...prev, name: detail.label }
          : ({
              id: portalId,
              name: detail.label,
              domain: "",
              role: "client",
              member_id: "",
              is_active: true,
            } as Portal)
      );
    };
    window.addEventListener(PORTAL_LABEL_EVENT, onLabel);
    return () => window.removeEventListener(PORTAL_LABEL_EVENT, onLabel);
  }, [isAgency, portalId]);

  useEffect(() => {
    // Clear first: an absent cache must not mean "keep the previous client".
    setDealHours(null);
    if (!portalId) {
      return;
    }
    const cached = readPortalCache<DealBinding>(CACHE_DEAL_HOURS, portalId);
    if (cached?.client_portal.id === portalId && cached.is_active) {
      setDealHours(cached);
    } else if (cached) {
      clearPortalCache(CACHE_DEAL_HOURS, portalId);
    }
  }, [portalId]);

  useEffect(() => {
    setProjects([]);
    const cachedOverview = portalId
      ? readPortalCache<OverviewSnapshot>(CACHE_OVERVIEW, portalId)
      : null;
    const scopedOpen =
      cachedOverview?.openTasks.filter((task) => task.portal_id === portalId) || [];
    const scopedDone =
      cachedOverview?.recentDone.filter((task) => task.portal_id === portalId) || [];
    const scopedDisputes =
      cachedOverview?.disputedReports.filter(
        (report) => report.portal_id === portalId
      ) || [];
    setOpenTasks(scopedOpen);
    setRecentDone(scopedDone);
    setDisputedReports(scopedDisputes);
    setMeetings(cachedOverview?.meetings || []);
    setOverviewLoading(cachedOverview === null);
    setError(null);
    if (!portalId) {
      return;
    }
    const cached = readPortalCache<Project[]>(CACHE_PROJECTS, portalId);
    const scoped = cached?.filter((project) => project.portal === portalId) || [];
    if (scoped.length) setProjects(scoped);
    if (cached && scoped.length !== cached.length) {
      writePortalCache(CACHE_PROJECTS, portalId, scoped);
    }
  }, [portalId]);

  async function load(signal?: AbortSignal) {
    if (!token || !portalId) return;
    const requestedPortalId = portalId;
    const gen = ++loadGenRef.current;
    try {
      const [openData, doneData, hoursData, disputedData, projectsData, meetingsData] =
        await Promise.all([
        api<Task[] | Paginated<Task>>(
          withPage(`/api/tasks/?portal=${requestedPortalId}&open=1`, 1, PICKER_PAGE_SIZE),
          { signal },
          token
        ),
        !isAgency
          ? api<Task[] | Paginated<Task>>(
              withPage(
                `/api/tasks/?portal=${requestedPortalId}&status=done&ordering=-updated_at`,
                1,
                PICKER_PAGE_SIZE
              ),
              { signal },
              token
            )
          : Promise.resolve([] as Task[]),
        isAgency
          ? api<DealBinding[] | Paginated<DealBinding>>(
              `/api/deal-bindings/?client_portal=${requestedPortalId}&is_active=true`,
              { signal },
              token
            ).catch((e) => {
              if (isAbortError(e)) throw e;
              return [] as DealBinding[];
            })
          : api<DealBinding>("/api/deal-bindings/mine/", { signal }, token).catch((e) => {
              if (isAbortError(e)) throw e;
              return null;
            }),
        isAgency
          ? api<WorkReport[] | Paginated<WorkReport>>(
              `/api/reports/?portal=${requestedPortalId}&status=disputed`,
              { signal },
              token
            )
          : Promise.resolve([] as WorkReport[]),
        api<Project[] | Paginated<Project>>(
          withPage(`/api/projects/?portal=${requestedPortalId}`, 1, PICKER_PAGE_SIZE),
          { signal },
          token
        ),
        api<ProjectMeeting[] | Paginated<ProjectMeeting>>(
          "/api/meetings/?page_size=100",
          { signal },
          token
        ).catch((e) => {
          if (isAbortError(e)) throw e;
          return [] as ProjectMeeting[];
        }),
      ]);
      if (gen !== loadGenRef.current || signal?.aborted) return;

      const scopedOpen = unwrapList(openData).filter(
        (task) => task.portal_id === requestedPortalId
      );
      setOpenTasks(scopedOpen);
      const projectList = unwrapList(projectsData).filter(
        (project) => project.portal === requestedPortalId
      );
      setProjects(projectList);
      writePortalCache(CACHE_PROJECTS, requestedPortalId, projectList);
      const projectIds = new Set(projectList.map((project) => project.id));
      const scopedMeetings = unwrapList(
        meetingsData as ProjectMeeting[] | Paginated<ProjectMeeting>
      ).filter((meeting) => projectIds.has(meeting.project));
      setMeetings(scopedMeetings);

      if (!isAgency) {
        const cutoff = Date.now() - RECENT_DONE_MS;
        const scopedDone =
          unwrapList(doneData as Task[] | Paginated<Task>)
            .filter((task) => task.portal_id === requestedPortalId)
            .filter((t) => new Date(t.updated_at).getTime() >= cutoff)
            .slice(0, 6);
        setRecentDone(scopedDone);
        setDisputedReports([]);
        writePortalCache<OverviewSnapshot>(CACHE_OVERVIEW, requestedPortalId, {
          openTasks: scopedOpen,
          recentDone: scopedDone,
          disputedReports: [],
          workingTasks: [],
          meetings: scopedMeetings,
        });
        const mine = hoursData as DealBinding | null;
        const scopedMine =
          mine?.client_portal.id === requestedPortalId && mine.is_active ? mine : null;
        setDealHours(scopedMine);
        if (scopedMine) {
          writePortalCache(CACHE_DEAL_HOURS, requestedPortalId, scopedMine);
        } else {
          clearPortalCache(CACHE_DEAL_HOURS, requestedPortalId);
        }
        if (portal) setPortalInfo(portal);
      } else {
        const bindings = unwrapList(hoursData as DealBinding[] | Paginated<DealBinding>);
        const binding =
          bindings.find(
            (row) => row.client_portal.id === requestedPortalId && row.is_active
          ) || null;
        setDealHours(binding);
        if (binding) writePortalCache(CACHE_DEAL_HOURS, requestedPortalId, binding);
        else clearPortalCache(CACHE_DEAL_HOURS, requestedPortalId);
        const scopedDisputes = unwrapList(
          disputedData as WorkReport[] | Paginated<WorkReport>
        ).filter((report) => report.portal_id === requestedPortalId);
        setRecentDone([]);
        setDisputedReports(scopedDisputes);
        writePortalCache<OverviewSnapshot>(CACHE_OVERVIEW, requestedPortalId, {
          openTasks: scopedOpen,
          recentDone: [],
          disputedReports: scopedDisputes,
          workingTasks: [],
          meetings: scopedMeetings,
        });
        const fromBinding = binding?.client_portal;
        if (fromBinding) {
          const label = portalDisplayName(fromBinding);
          if (label) {
            setPortalLabel(requestedPortalId, label);
            setPortalInfo(fromBinding);
          }
        } else {
          const cached = getPortalLabel(requestedPortalId);
          if (cached) {
            setPortalInfo((prev) =>
              prev?.id === portalId
                ? prev
                : ({
                    id: portalId,
                    name: cached,
                    domain: "",
                    role: "client",
                    member_id: "",
                    is_active: true,
                  } as Portal)
            );
          }
        }
      }
    } finally {
      if (gen === loadGenRef.current && !signal?.aborted) {
        setOverviewLoading(false);
      }
    }
  }

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal).catch((e) => {
      if (!isAbortError(e)) setError(e instanceof Error ? e.message : "Ошибка");
    });
    return () => {
      loadGenRef.current += 1;
      ac.abort();
    };
  }, [token, portalId]);

  const reloadRef = useRef<() => void>(() => undefined);
  reloadRef.current = () => {
    void load().catch(() => undefined);
  };

  usePortalLiveSync({
    token,
    portalId,
    onEvent: () => reloadRef.current(),
  });

  useEffect(() => {
    if (!token || !portalId) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") reloadRef.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [token, portalId]);

  const titleName = portalInfo?.name || portalInfo?.domain || "Клиент";
  const agencyNeedsAttention =
    disputedReports.length > 0 || activeProjects.length > 0 || hotTasks.length > 0;
  const projectsListPath = isAgency ? `/portals/${portalId}/projects` : "/projects";

  function openMeetingScheduler(meeting?: ProjectMeeting) {
    const firstProject = activeProjects[0] || projects[0];
    setEditingMeetingId(meeting?.id ?? null);
    setMeetingProjectId(meeting ? String(meeting.project) : firstProject ? String(firstProject.id) : "");
    setMeetingDate(meeting ? toISODate(new Date(meeting.scheduled_at)) : initialMeetingDate());
    setMeetingAt(meeting?.scheduled_at || "");
    setMeetingNotes(meeting?.notes || "");
    setMeetingSlotError(null);
    setShowMeetingList(false);
    setShowMeetingForm(true);
  }

  useEffect(() => {
    if (!showMeetingForm || !token || !meetingProjectId || !meetingDate) return;
    const controller = new AbortController();
    setMeetingSlotsLoading(true);
    setMeetingSlotError(null);
    api<MeetingAvailability>(
      `/api/meetings/availability/?project=${encodeURIComponent(meetingProjectId)}&date=${encodeURIComponent(meetingDate)}${editingMeetingId ? `&exclude=${editingMeetingId}` : ""}`,
      { signal: controller.signal },
      token
    ).then((data) => {
      setMeetingSlots(data.slots);
      setMeetingAt((current) => data.slots.some((slot) => slot.available && slot.starts_at === current) ? current : "");
    }).catch((err) => {
      if (!isAbortError(err)) {
        setMeetingSlots([]);
        setMeetingSlotError(err instanceof Error ? err.message : "Не удалось загрузить свободное время");
      }
    }).finally(() => {
      if (!controller.signal.aborted) setMeetingSlotsLoading(false);
    });
    return () => controller.abort();
  }, [showMeetingForm, token, meetingProjectId, meetingDate, editingMeetingId]);

  async function createMeeting(event: React.FormEvent) {
    event.preventDefault();
    if (!token || !meetingProjectId || !meetingAt) return;
    setMeetingBusy(true);
    setError(null);
    try {
      const payload = {
        project: Number(meetingProjectId),
        title: "Встреча с командой",
        scheduled_at: new Date(meetingAt).toISOString(),
        duration_minutes: 60,
        format: "video" as const,
        location: "",
        notes: meetingNotes.trim(),
      };
      const saved = editingMeetingId
        ? await api<ProjectMeeting>(
            `/api/meetings/${editingMeetingId}/`,
            { method: "PATCH", body: JSON.stringify({ scheduled_at: payload.scheduled_at, duration_minutes: payload.duration_minutes, notes: payload.notes }) },
            token
          )
        : await api<ProjectMeeting>("/api/meetings/", { method: "POST", body: JSON.stringify(payload) }, token);
      setMeetings((current) =>
        [...current.filter((item) => item.id !== saved.id), saved].sort((a, b) =>
          a.scheduled_at.localeCompare(b.scheduled_at)
        )
      );
      setEditingMeetingId(null);
      setMeetingAt("");
      setMeetingNotes("");
      setShowMeetingForm(false);
      const moved = Boolean(editingMeetingId);
      toast.show(
        moved ? "Новое время сохранено" : "Встреча появилась в обзоре и внутри проекта",
        moved ? "Встреча перенесена" : "Встреча запланирована"
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "Не удалось запланировать встречу";
      setMeetingSlotError(message);
      setMeetingAt("");
      reloadRef.current();
    } finally {
      setMeetingBusy(false);
    }
  }

  async function cancelMeeting(meeting: ProjectMeeting) {
    if (!token || meetingBusy) return;
    if (!window.confirm(`Отменить встречу ${formatRuDateTime(meeting.scheduled_at, dueTz)}?`)) return;
    setMeetingBusy(true);
    setError(null);
    try {
      const saved = await api<ProjectMeeting>(
        `/api/meetings/${meeting.id}/`,
        { method: "PATCH", body: JSON.stringify({ cancelled_at: new Date().toISOString() }) },
        token
      );
      setMeetings((current) => current.map((item) => (item.id === saved.id ? saved : item)));
      toast.show("Это время снова можно занять", "Встреча отменена");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отменить встречу");
    } finally {
      setMeetingBusy(false);
    }
  }

  async function saveOutcome(meeting: ProjectMeeting) {
    if (!token || !isAgency || meetingBusy) return;
    setMeetingBusy(true);
    setError(null);
    try {
      const saved = await api<ProjectMeeting>(
        `/api/meetings/${meeting.id}/`,
        {
          method: "PATCH",
          body: JSON.stringify({ outcome: (outcomeDrafts[meeting.id] ?? meeting.outcome ?? "").trim() }),
        },
        token
      );
      setMeetings((current) => current.map((item) => (item.id === saved.id ? saved : item)));
      toast.show("Клиент увидит текст в списке встреч", "Итог сохранён");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить итог");
    } finally {
      setMeetingBusy(false);
    }
  }

  const visibleMeetings = meetings
    .slice()
    .sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at));

  return (
    <div className="workspace-page overview-workspace">
      {isAgency ? (
      <div className="page-header">
        <div>
          <h1 className="page-title">{titleName}</h1>
        </div>
      </div>
      ) : null}

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <div className={`overview-hours-split${!isAgency ? " is-client-home is-hours-only" : ""}`} data-tour="tour-deal-hours">
        <div className="overview-hours-col">
          {hasDealHoursPackage(dealHours) && dealHours ? (
            <DealHoursCard binding={dealHours} audience={isAgency ? "agency" : "client"} />
          ) : (
            <section className="deal-hours-card is-client-pack is-empty-pack" aria-label="Пакет часов">
              <div className="deal-hours-card-head">
                <h2 className="section-title">Пакет часов</h2>
              </div>
              <p className="muted">
                {overviewLoading
                  ? "Загружаем пакет…"
                  : "Пакет часов по этому кабинету пока не подключён."}
              </p>
            </section>
          )}
        </div>
      </div>

      {showMeetingForm ? (
        <ModalPortal>
        <div className="modal-backdrop" onMouseDown={() => !meetingBusy && setShowMeetingForm(false)}>
          <form className="modal-card meeting-modal-card" onSubmit={createMeeting} onMouseDown={(event) => event.stopPropagation()}>
            <div className="meeting-modal-icon"><CalendarGlyph /></div>
            <h2 className="modal-title">{editingMeetingId ? "Перенести встречу" : "Запланировать встречу"}</h2>
            <p className="meeting-modal-lead">Выберите свободное время. Продолжительность встречи — 1 час.</p>
            <div className="meeting-form-fields">
              <label className="field meeting-date-field"><span>Дата</span><input type="date" value={meetingDate} min={toISODate(new Date())} max={toISODate(addDays(new Date(), 30))} onChange={(event) => { setMeetingDate(event.target.value); setMeetingAt(""); }} required autoFocus /></label>
              <section className="meeting-slot-picker" aria-label="Доступное время">
                <div className="meeting-slot-picker-head">
                  <strong>Время</strong>
                </div>
                {meetingSlotsLoading ? <p className="meeting-slots-state">Проверяем расписание…</p> : null}
                {!meetingSlotsLoading && meetingSlotError ? <p className="meeting-slots-error">{meetingSlotError}</p> : null}
                {!meetingSlotsLoading && !meetingSlotError ? (
                  <div className="meeting-slot-grid">
                    {meetingSlots.map((slot) => (
                      <button
                        key={slot.starts_at}
                        type="button"
                        className={meetingAt === slot.starts_at ? "is-selected" : ""}
                        disabled={!slot.available}
                        title={slot.reason === "occupied" ? "Это время занято" : slot.reason === "past" ? "Время уже прошло" : slot.reason === "weekend" ? "Выходной день" : "Свободное время"}
                        onClick={() => setMeetingAt(slot.starts_at)}
                      >
                        {slot.label}
                      </button>
                    ))}
                  </div>
                ) : null}
                {!meetingSlotsLoading && !meetingSlotError && meetingSlots.every((slot) => !slot.available) ? <p className="meeting-slots-state">На эту дату свободных слотов нет. Выберите другой день.</p> : null}
              </section>
              <label className="field"><span>Комментарий</span><textarea value={meetingNotes} onChange={(event) => setMeetingNotes(event.target.value)} rows={3} placeholder="Напишите, если есть пожелания к встрече" /></label>
            </div>
            <div className="modal-actions"><button type="button" className="btn btn-ghost" onClick={() => { setShowMeetingForm(false); setEditingMeetingId(null); }} disabled={meetingBusy}>Отмена</button><button className="btn btn-primary" disabled={meetingBusy || !meetingAt}>{meetingBusy ? "Сохраняем…" : meetingAt ? (editingMeetingId ? "Перенести" : "Запланировать") : "Выберите время"}</button></div>
          </form>
        </div>
        </ModalPortal>
      ) : null}

      {!isAgency ? (
        <div className="overview-focus">
          <section className="overview-focus-card overview-focus-task" aria-label="Ближайшая задача">
            <div className="overview-focus-kicker">
              <TaskGlyph />
              <span>Ближайшая задача</span>
            </div>
            {nextDueTask ? (
              <Link to={`/tasks/${nextDueTask.id}`} state={fromState} className="overview-focus-task-body">
                <div className="overview-focus-copy">
                  <strong>{nextDueTask.title}</strong>
                  <span>{nextDueTask.project_name}</span>
                </div>
                <div className={`overview-focus-due${nextDueOverdue ? " is-overdue" : ""}`}>
                  {nextDueStamp ? (
                    <>
                      <strong>{nextDueStamp.day}</strong>
                      <span>{nextDueStamp.month}</span>
                      {nextDueOverdue ? <em>Просрочено</em> : null}
                    </>
                  ) : (
                    <strong>Без срока</strong>
                  )}
                </div>
              </Link>
            ) : (
              <p className="overview-focus-empty">
                {overviewLoading ? "Загружаем задачи…" : "Сроки пока не назначены"}
              </p>
            )}
          </section>

          <div className="overview-focus-pair">
            <section className="overview-focus-card overview-focus-meeting" aria-label="Ближайшая встреча">
              <div className="overview-focus-card-head">
                <div className="overview-focus-kicker">
                  <CalendarGlyph />
                  <span>Ближайшая встреча</span>
                </div>
                <div className="overview-meeting-actions">
                  <button type="button" onClick={() => setShowMeetingList(true)}>Все</button>
                  <button type="button" onClick={() => openMeetingScheduler()}>Ещё</button>
                </div>
              </div>
              {nextMeeting ? (
                <Link to={`/projects/${nextMeeting.project}`} state={fromState} className="overview-focus-meeting-body">
                  <div className="overview-focus-date" aria-hidden>
                    <strong>{nextMeetingStamp?.day}</strong>
                    <span>{nextMeetingStamp?.month}</span>
                    <em>{nextMeetingStamp?.time}</em>
                  </div>
                  <div className="overview-focus-copy">
                    <strong>{nextMeeting.title}</strong>
                    <i>
                      {nextMeeting.format === "video"
                        ? "Видеовстреча"
                        : nextMeeting.format === "phone"
                          ? "Звонок"
                          : "Офлайн"}
                    </i>
                  </div>
                </Link>
              ) : (
                <div className="overview-focus-meeting-empty">
                  <div className="overview-focus-copy">
                    <strong>Встреч пока нет</strong>
                    <button type="button" onClick={() => openMeetingScheduler()}>Запланировать</button>
                  </div>
                </div>
              )}
            </section>

            <section className="overview-focus-card overview-focus-projects" aria-label="Проекты в работе">
              <div className="overview-focus-card-head">
                <div className="overview-focus-kicker">
                  <span>Проекты в работе</span>
                </div>
                <Link to={projectsListPath} className="overview-focus-more">Все проекты</Link>
              </div>
              {overviewLoading && activeProjects.length === 0 ? (
                <p className="overview-focus-empty">Загружаем проекты…</p>
              ) : activeProjects.length === 0 ? (
                <p className="overview-focus-empty">Сейчас нет проектов в работе.</p>
              ) : (
                <div className="overview-focus-project-list">
                  {activeProjects.map((p) => {
                    const { done, total } = projectProgress(p);
                    const due = p.due_date || earliestProjectDue(p.id, openTasks);
                    const dueLabel = due ? formatRuDateTime(due, dueTz) : "";
                    const tracked = p.total_tracked_seconds || 0;
                    const progress = total > 0 ? Math.round((done / total) * 100) : 0;
                    return (
                      <Link
                        key={`project-${p.id}`}
                        to={`/projects/${p.id}`}
                        state={fromState}
                        className="overview-focus-project"
                      >
                        <div className="overview-focus-project-top">
                          <strong>{p.name}</strong>
                          <span>{total === 0 ? "План" : `${progress}%`}</span>
                        </div>
                        <div
                          className="overview-focus-bar"
                          role="progressbar"
                          aria-label={`Прогресс проекта ${p.name}`}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={progress}
                        >
                          <span style={{ width: `${progress}%` }} />
                        </div>
                        <div className="overview-focus-project-meta">
                          <span>{total === 0 ? "Планирование" : `${done} из ${total} задач`}</span>
                          <span>
                            {tracked > 0 ? formatDuration(tracked) : dueLabel ? `До ${dueLabel}` : "Срок не задан"}
                          </span>
                        </div>
                      </Link>
                    );
                  })}
                </div>
              )}
            </section>
          </div>
        </div>
      ) : null}

      {showMeetingList ? (
        <ModalPortal>
        <div className="modal-backdrop meeting-list-backdrop" onMouseDown={() => !meetingBusy && setShowMeetingList(false)}>
          <div className="modal-card meeting-list-card" onMouseDown={(event) => event.stopPropagation()}>
            <div className="meeting-list-head">
              <h2 className="modal-title">Встречи</h2>
              <button type="button" onClick={() => openMeetingScheduler()} disabled={meetingBusy}>
                Новая встреча
              </button>
            </div>
            {visibleMeetings.length === 0 ? (
              <p className="muted">Запланированных встреч пока нет.</p>
            ) : (
              <div className="meeting-list">
                {visibleMeetings.map((meeting) => {
                  const past = new Date(meeting.scheduled_at).getTime() < Date.now();
                  const cancelled = Boolean(meeting.cancelled_at);
                  return (
                    <article key={meeting.id} className={`meeting-list-item${cancelled ? " is-cancelled" : ""}`}>
                      <div>
                        <strong>{meeting.title}</strong>
                        <span>
                          {formatRuDateTime(meeting.scheduled_at, dueTz)}
                          {cancelled ? " · отменена" : past ? " · прошла" : ""}
                        </span>
                        {meeting.outcome?.trim() ? <p>{linkify(meeting.outcome)}</p> : null}
                      </div>
                      {!cancelled && !past ? (
                        <div className="meeting-list-actions">
                          <button type="button" onClick={() => openMeetingScheduler(meeting)} disabled={meetingBusy}>Перенести</button>
                          <button type="button" onClick={() => void cancelMeeting(meeting)} disabled={meetingBusy}>Отменить</button>
                        </div>
                      ) : null}
                      {isAgency && !cancelled ? (
                        <form
                          className="meeting-outcome-form"
                          onSubmit={(event) => {
                            event.preventDefault();
                            void saveOutcome(meeting);
                          }}
                        >
                          <textarea
                            value={outcomeDrafts[meeting.id] ?? meeting.outcome ?? ""}
                            onChange={(event) =>
                              setOutcomeDrafts((current) => ({ ...current, [meeting.id]: event.target.value }))
                            }
                            rows={3}
                            placeholder="Итог встречи. Ссылку можно вставить как https://…"
                          />
                          <button type="submit" className="btn btn-primary" disabled={meetingBusy}>
                            Сохранить итог
                          </button>
                        </form>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            )}
            <div className="modal-actions">
              <button type="button" className="btn btn-ghost" onClick={() => setShowMeetingList(false)}>
                Закрыть
              </button>
            </div>
          </div>
        </div>
        </ModalPortal>
      ) : null}

      {isAgency ? (
        <div className="workspace-focus" data-tour="tour-agency-focus">
          <section className="workspace-focus-block agency-meetings">
            <div className="linked-head">
              <h2 className="section-title">Встречи</h2>
              <button type="button" className="btn btn-ghost" onClick={() => setShowMeetingList(true)}>
                Список и итоги
              </button>
            </div>
            <p className="muted">
              {nextMeeting
                ? `Ближайшая: ${nextMeeting.title}, ${formatRuDateTime(nextMeeting.scheduled_at, dueTz)}`
                : "Ближайших встреч нет. Итог прошедшей встречи можно записать в списке — ссылки в тексте станут кликабельными."}
            </p>
          </section>
          {overviewLoading && !agencyNeedsAttention ? (
            <div className="empty-linked workspace-empty data-loading-state">
              <span className="data-loading-spinner" aria-hidden />
              <p className="muted">Загружаем обзор клиента…</p>
            </div>
          ) : !agencyNeedsAttention ? (
            <div className="empty-linked workspace-empty">
              <p className="muted">
                Нет обращений по отчётам, активных проектов и горящих сроков. Полный список — во
                вкладке «Проекты».
              </p>
            </div>
          ) : (
            <>
              {disputedReports.length > 0 ? (
                <section className="workspace-focus-block workspace-dispute-section">
                  <div className="linked-head">
                    <div className="workspace-dispute-title-row">
                      <h2 className="section-title workspace-dispute-title">
                        <span className="workspace-dispute-badge" aria-hidden>
                          <DisputeIcon size={15} />
                        </span>
                        <span>Связь с менеджером</span>
                      </h2>
                    </div>
                    <p className="muted">Клиент написал по отчёту — нужно разобрать</p>
                  </div>
                  <div className="workspace-attention-list">
                    {disputedReports.map((r) => (
                      <Link
                        key={`dispute-${r.id}`}
                        to={reportDetailPath(portalId, true, r.id)}
                        state={fromState}
                        className="workspace-attention-card is-dispute"
                      >
                        <div className="workspace-attention-top">
                          <span className="workspace-dispute-pill">Обсуждение</span>
                          <span className="muted">Отчёт №{r.id}</span>
                        </div>
                        <span className="muted">Открыть и разобрать</span>
                      </Link>
                    ))}
                  </div>
                </section>
              ) : null}

              <div className="workspace-split-focus">
                <section className="workspace-focus-block">
                  <div className="linked-head">
                    <h2 className="section-title">Проекты в работе</h2>
                    <p className="muted">Модули клиента, которые ещё не на 100%</p>
                  </div>
                  {activeProjects.length === 0 ? (
                    <div className="empty-linked workspace-empty">
                      <p className="muted">Все проекты закрыты или ещё не созданы.</p>
                    </div>
                  ) : (
                    <div className="workspace-attention-list">
                      {activeProjects.map((p) => {
                        const { done, total, pct } = projectProgress(p);
                        return (
                          <Link
                            key={p.id}
                            to={`/projects/${p.id}`}
                            state={fromState}
                            className="workspace-attention-card is-project"
                          >
                            <div className="workspace-attention-top">
                              <span className="workspace-chip tone-project">{pct}%</span>
                              {p.has_active_work ? (
                                <span className="task-working-pill">Сейчас в работе</span>
                              ) : null}
                              <span className="muted">
                                {done}/{total} задач
                              </span>
                            </div>
                            <strong>{p.name}</strong>
                            <span className="muted">Открыть проект</span>
                          </Link>
                        );
                      })}
                    </div>
                  )}
                </section>

                <section className="workspace-focus-block">
                  <div className="linked-head">
                    <h2
                      className={`workspace-hot-heading${
                        hotTasks.length > 0 ? " is-shaking" : " is-calm"
                      }`}
                    >
                      <span className="workspace-hot-pill">
                        <FlameIcon filled size={14} />
                        <span className="workspace-hot-label">Горят</span>
                      </span>
                    </h2>
                    <p className="muted">Просроченные, срок 1–2 дня и важные</p>
                  </div>
                  {hotTasks.length === 0 ? (
                    <div className="empty-linked workspace-empty">
                      <p className="muted">Сроков и важных задач нет.</p>
                    </div>
                  ) : (
                    <div className="workspace-attention-list">
                      {hotTasks.map((t) => {
                        const overdue = isTaskOverdue(t.due_date, t.status);
                        const soon = isDueSoon(t.due_date, t.status);
                        const due = taskDueLabel(t, dueTz);
                        return (
                          <Link
                            key={t.id}
                            to={`/tasks/${t.id}`}
                            state={fromState}
                            className={`workspace-attention-card${
                              overdue ? " is-overdue" : soon ? " is-soon" : ""
                            }`}
                          >
                            <div className="workspace-attention-top">
                              {overdue ? (
                                <span className="workspace-chip tone-overdue">Просрочена</span>
                              ) : null}
                              {soon ? (
                                <span className="workspace-chip tone-soon">Скоро срок</span>
                              ) : null}
                              {t.is_important ? (
                                <span className="task-important-pill" title="Важная задача">
                                  <FlameIcon filled size={14} />
                                  Важно
                                </span>
                              ) : null}
                              <span className="muted">{t.project_name}</span>
                            </div>
                            <strong>{t.title}</strong>
                            <span className="muted">
                              {t.status !== "todo" ? STATUS_LABEL[t.status] : null}
                              {t.status !== "todo" && due ? " · " : ""}
                              {due ? `до ${due}` : ""}
                            </span>
                          </Link>
                        );
                      })}
                    </div>
                  )}
                </section>
              </div>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
