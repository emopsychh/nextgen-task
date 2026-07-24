import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useLocation, useParams } from "react-router-dom";
import {
  api,
  unwrapList,
  type Paginated,
  type Project,
  type SupportTicket,
  type WorkReport,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { usePortalLiveSync } from "../hooks/usePortalLiveSync";
import {
  getPortalLabel,
  PORTAL_LABEL_EVENT,
  portalDisplayName,
  setPortalLabel,
} from "../lib/portalLabelCache";
import {
  CACHE_PROJECTS,
  readPortalCache,
  writePortalCache,
} from "../lib/portalSessionCache";
import { useSupportWidget } from "./support/SupportWidgetContext";

function TicketsNavIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M4 7a2 2 0 0 1 2-2h8l4 4v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7Z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path
        d="M14 5v4h4M8 13h8M8 17h5"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function ProjectSidebarNav() {
  const { token, portal } = useAuth();
  const params = useParams();
  const location = useLocation();
  const isAgency = portal?.role === "agency";
  const supportWidget = useSupportWidget();

  const routePortalId = params.portalId ? Number(params.portalId) : null;
  const routeProjectId = params.projectId ? Number(params.projectId) : null;
  const onTicketsRoute = location.pathname.startsWith("/tickets");

  const [projects, setProjects] = useState<Project[]>([]);
  const [resolvedPortalId, setResolvedPortalId] = useState<number | null>(null);
  const [clientLabel, setClientLabel] = useState("");
  const [reportsAttention, setReportsAttention] = useState(0);
  const [openTickets, setOpenTickets] = useState(0);
  const lastPortalRef = useRef<number | null>(null);

  const contextPortalId = useMemo(() => {
    if (routePortalId) return routePortalId;
    if (!isAgency && portal?.id) return portal.id;
    if (resolvedPortalId) return resolvedPortalId;
    if (routeProjectId && lastPortalRef.current) return lastPortalRef.current;
    return null;
  }, [routePortalId, isAgency, portal?.id, resolvedPortalId, routeProjectId]);

  useEffect(() => {
    if (contextPortalId) lastPortalRef.current = contextPortalId;
  }, [contextPortalId]);

  // Instant label from auth / ClientRail cache — don't wait for projects list
  useEffect(() => {
    if (!contextPortalId) return;
    if (!isAgency && portal?.id === contextPortalId) {
      const label = portalDisplayName(portal);
      if (label) {
        setPortalLabel(contextPortalId, label);
        setClientLabel(label);
        return;
      }
    }
    const cached = getPortalLabel(contextPortalId);
    if (cached) setClientLabel(cached);
  }, [contextPortalId, isAgency, portal]);

  useEffect(() => {
    const onLabel = (event: Event) => {
      const detail = (event as CustomEvent<{ portalId: number; label: string }>).detail;
      if (!detail || detail.portalId !== contextPortalId) return;
      setClientLabel(detail.label);
    };
    window.addEventListener(PORTAL_LABEL_EVENT, onLabel);
    return () => window.removeEventListener(PORTAL_LABEL_EVENT, onLabel);
  }, [contextPortalId]);

  useEffect(() => {
    if (isAgency && !routePortalId && !routeProjectId && !onTicketsRoute) {
      lastPortalRef.current = null;
      setResolvedPortalId(null);
      setProjects([]);
      setClientLabel("");
      setReportsAttention(0);
    }
  }, [isAgency, routePortalId, routeProjectId, onTicketsRoute]);

  const showClientNav = Boolean(contextPortalId) && !(isAgency && onTicketsRoute);

  useEffect(() => {
    if (!token || !routeProjectId || routePortalId) return;
    let cancelled = false;
    void api<Project>(`/api/projects/${routeProjectId}/`, {}, token)
      .then((p) => {
        if (cancelled) return;
        setResolvedPortalId(p.portal);
        lastPortalRef.current = p.portal;
        if (p.portal_name) {
          setPortalLabel(p.portal, p.portal_name);
          setClientLabel(p.portal_name);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token, routeProjectId, routePortalId]);

  useEffect(() => {
    if (!token || !contextPortalId) return;
    if (isAgency && onTicketsRoute) return;

    const cached = readPortalCache<Project[]>(CACHE_PROJECTS, contextPortalId);
    if (cached?.length) setProjects(cached);

    let cancelled = false;
    async function load() {
      try {
        const data = await api<Project[] | { results: Project[] }>(
          `/api/projects/?portal=${contextPortalId}`,
          {},
          token!
        );
        if (cancelled) return;
        const list = unwrapList(data);
        setProjects(list);
        writePortalCache(CACHE_PROJECTS, contextPortalId!, list);
        if (list[0]?.portal_name) {
          setPortalLabel(contextPortalId!, list[0].portal_name);
          setClientLabel(list[0].portal_name);
        }
      } catch {
        if (!cancelled && !cached?.length) setProjects([]);
      }
    }

    void load();
    const onUpdate = () => void load();
    window.addEventListener("projects-updated", onUpdate);
    return () => {
      cancelled = true;
      window.removeEventListener("projects-updated", onUpdate);
    };
  }, [token, contextPortalId, isAgency, onTicketsRoute]);

  // Open tickets badge — client only (agency badge lives in ClientRail)
  useEffect(() => {
    if (!token || isAgency || !contextPortalId) {
      if (isAgency) setOpenTickets(0);
      else if (!contextPortalId) setOpenTickets(0);
      return;
    }
    let cancelled = false;

    async function loadTickets() {
      try {
        const ticketsData = await api<SupportTicket[] | Paginated<SupportTicket>>(
          `/api/tickets/?portal=${contextPortalId}&bucket=open&awaiting=client`,
          {},
          token!
        );
        if (!cancelled) setOpenTickets(unwrapList(ticketsData).length);
      } catch {
        if (!cancelled) setOpenTickets(0);
      }
    }

    void loadTickets();
    const onVisible = () => {
      if (document.visibilityState === "visible") void loadTickets();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [token, contextPortalId, isAgency, location.pathname]);

  useEffect(() => {
    if (!token || !contextPortalId || (isAgency && onTicketsRoute)) {
      if (!contextPortalId || (isAgency && onTicketsRoute)) setReportsAttention(0);
      return;
    }
    let cancelled = false;

    async function loadReports() {
      try {
        let next = 0;
        if (isAgency) {
          const [drafts, disputed] = await Promise.all([
            api<WorkReport[] | Paginated<WorkReport>>(
              `/api/reports/?portal=${contextPortalId}&status=draft`,
              {},
              token!
            ),
            api<WorkReport[] | Paginated<WorkReport>>(
              `/api/reports/?portal=${contextPortalId}&status=disputed`,
              {},
              token!
            ),
          ]);
          next = unwrapList(drafts).length + unwrapList(disputed).length;
        } else {
          const data = await api<WorkReport[] | Paginated<WorkReport>>(
            `/api/reports/?portal=${contextPortalId}&bucket=review`,
            {},
            token!
          );
          next = unwrapList(data).length;
        }
        if (!cancelled) setReportsAttention(next);
      } catch {
        if (!cancelled) setReportsAttention(0);
      }
    }

    void loadReports();
    const onVisible = () => {
      if (document.visibilityState === "visible") void loadReports();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [token, contextPortalId, isAgency, location.pathname, onTicketsRoute]);

  usePortalLiveSync({
    token,
    portalId: contextPortalId,
    enabled: !!contextPortalId,
    onEvent: (payload) => {
      if (!token) return;
      const kind = payload?.kind || "";
      const refreshReports = kind.startsWith("report_") || !kind;
      const refreshTickets = kind.startsWith("ticket_") || !kind;
      if (!refreshReports && !refreshTickets) return;
      void (async () => {
        try {
          if (refreshReports && contextPortalId && !(isAgency && onTicketsRoute)) {
            if (isAgency) {
              const [drafts, disputed] = await Promise.all([
                api<WorkReport[] | Paginated<WorkReport>>(
                  `/api/reports/?portal=${contextPortalId}&status=draft`,
                  {},
                  token
                ),
                api<WorkReport[] | Paginated<WorkReport>>(
                  `/api/reports/?portal=${contextPortalId}&status=disputed`,
                  {},
                  token
                ),
              ]);
              setReportsAttention(unwrapList(drafts).length + unwrapList(disputed).length);
            } else {
              const data = await api<WorkReport[] | Paginated<WorkReport>>(
                `/api/reports/?portal=${contextPortalId}&bucket=review`,
                {},
                token
              );
              setReportsAttention(unwrapList(data).length);
            }
          }
          if (refreshTickets && !isAgency && contextPortalId) {
            const ticketsData = await api<SupportTicket[] | Paginated<SupportTicket>>(
              `/api/tickets/?portal=${contextPortalId}&bucket=open&awaiting=client`,
              {},
              token
            );
            setOpenTickets(unwrapList(ticketsData).length);
          }
        } catch {
          // keep previous
        }
      })();
    },
  });

  const ticketsLink = !isAgency ? (
    <button
      type="button"
      className={`${showClientNav ? "feed-nav-item" : "nav-item"}${supportWidget.isOpen ? " active" : ""}`}
      onClick={() => supportWidget.toggle()}
    >
      {showClientNav ? (
        <span className="feed-nav-icon" aria-hidden>
          <TicketsNavIcon />
        </span>
      ) : null}
      <span className={showClientNav ? "feed-nav-label" : undefined}>Поддержка</span>
      {openTickets > 0 ? (
        <span className="feed-nav-count" aria-label={`${openTickets} открытых тикетов`}>
          {openTickets > 99 ? "99+" : openTickets}
        </span>
      ) : null}
    </button>
  ) : null;

  if (!showClientNav) {
    return (
      <nav className="nav-list" data-tour="tour-sidebar">
        <NavLink to="/" end className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          Обзор
        </NavLink>
        {ticketsLink}
        {isAgency ? (
          <p className="sidebar-hint muted">
            {onTicketsRoute
              ? "Общая лента тикетов по всем клиентам."
              : "Выберите клиента слева, чтобы открыть проекты и отчёты."}
          </p>
        ) : null}
      </nav>
    );
  }

  const feedTo = isAgency ? `/portals/${contextPortalId}` : "/";
  const projectsTo = isAgency ? `/portals/${contextPortalId}/projects` : "/projects";
  const onFeed =
    location.pathname === feedTo ||
    (!isAgency && location.pathname === "/") ||
    (isAgency && location.pathname === `/portals/${contextPortalId}`);
  const onProjectsList =
    location.pathname === projectsTo ||
    location.pathname === `/portals/${contextPortalId}/projects`;

  return (
    <div className="project-sidebar" data-tour="tour-sidebar">
      <div className="sidebar-section-label">
        {clientLabel || (isAgency ? "Кабинет клиента" : "Ваш кабинет")}
      </div>
      <NavLink
        to={feedTo}
        end
        className={({ isActive }) => `feed-nav-item${isActive || onFeed ? " active" : ""}`}
      >
        <span className="feed-nav-icon" aria-hidden>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path
              d="M4 6h16M4 12h10M4 18h14"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        </span>
        Обзор
      </NavLink>
      <NavLink
        to={projectsTo}
        end
        className={({ isActive }) =>
          `feed-nav-item${isActive || onProjectsList ? " active" : ""}`
        }
      >
        <span className="feed-nav-icon" aria-hidden>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path
              d="M4 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7Z"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <span className="feed-nav-label">Проекты</span>
        {projects.length > 0 ? (
          <span className="feed-nav-count" aria-label={`${projects.length} проектов`}>
            {projects.length > 99 ? "99+" : projects.length}
          </span>
        ) : null}
      </NavLink>
      <NavLink
        to={isAgency ? `/portals/${contextPortalId}/reports` : "/reports"}
        className={({ isActive }) => `feed-nav-item${isActive ? " active" : ""}`}
      >
        <span className="feed-nav-icon" aria-hidden>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path
              d="M7 4h10a2 2 0 0 1 2 2v14l-3-2-3 2-3-2-3 2V6a2 2 0 0 1 2-2Z"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            <path d="M9 9h6M9 13h4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </span>
        <span className="feed-nav-label">Отчёты</span>
        {reportsAttention > 0 ? (
          <span className="feed-nav-count" aria-label={`${reportsAttention} требуют внимания`}>
            {reportsAttention > 99 ? "99+" : reportsAttention}
          </span>
        ) : null}
      </NavLink>
      {ticketsLink}
    </div>
  );
}
