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

  useEffect(() => {
    if (isAgency && !routePortalId && !routeProjectId && !onTicketsRoute) {
      lastPortalRef.current = null;
      setResolvedPortalId(null);
      setProjects([]);
      setClientLabel("");
      setReportsAttention(0);
    }
  }, [isAgency, routePortalId, routeProjectId, onTicketsRoute]);

  const showProjects = Boolean(contextPortalId) && !(isAgency && onTicketsRoute);

  useEffect(() => {
    if (!token || !routeProjectId || routePortalId) return;
    let cancelled = false;
    void api<Project>(`/api/projects/${routeProjectId}/`, {}, token)
      .then((p) => {
        if (cancelled) return;
        setResolvedPortalId(p.portal);
        lastPortalRef.current = p.portal;
        if (p.portal_name) setClientLabel(p.portal_name);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token, routeProjectId, routePortalId]);

  useEffect(() => {
    if (!token || !contextPortalId) return;
    if (isAgency && onTicketsRoute) return;

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
        if (list[0]?.portal_name) setClientLabel(list[0].portal_name);
      } catch {
        if (!cancelled) setProjects([]);
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
          `/api/tickets/?portal=${contextPortalId}&bucket=open`,
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
              `/api/tickets/?portal=${contextPortalId}&bucket=open`,
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
      className={`${showProjects ? "feed-nav-item" : "nav-item"}${supportWidget.isOpen ? " active" : ""}`}
      onClick={() => supportWidget.toggle()}
    >
      {showProjects ? (
        <span className="feed-nav-icon" aria-hidden>
          <TicketsNavIcon />
        </span>
      ) : null}
      <span className={showProjects ? "feed-nav-label" : undefined}>Поддержка</span>
      {openTickets > 0 ? (
        <span className="feed-nav-count" aria-label={`${openTickets} открытых тикетов`}>
          {openTickets > 99 ? "99+" : openTickets}
        </span>
      ) : null}
    </button>
  ) : null;

  if (!showProjects) {
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

  const feedTo = isAgency ? `/portals/${contextPortalId}/projects` : "/";
  const onFeed =
    !routeProjectId &&
    (location.pathname === feedTo ||
      location.pathname === `/portals/${contextPortalId}/projects` ||
      (!isAgency && location.pathname === "/"));

  return (
    <div className="project-sidebar" data-tour="tour-sidebar">
      <div className="sidebar-section-label">
        {clientLabel || (isAgency ? "Проекты клиента" : "Ваши проекты")}
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
      <div className="project-nav-heading">Проекты</div>
      <nav className="project-nav">
        {projects.map((p) => {
          const total = p.tasks_count || 0;
          const done = p.done_count || 0;
          const pct = total ? Math.round((done / total) * 100) : 0;
          return (
            <NavLink
              key={p.id}
              to={`/projects/${p.id}`}
              className={({ isActive }) => `project-nav-item${isActive ? " active" : ""}`}
            >
              <span className="project-nav-top">
                <span className="project-nav-name">{p.name}</span>
                <span className="project-nav-pct">{pct}%</span>
              </span>
              <span className="project-nav-meta">
                {done}/{total} задач
              </span>
              <span className="project-nav-bar" aria-hidden>
                <span style={{ width: `${pct}%` }} />
              </span>
            </NavLink>
          );
        })}
        {projects.length === 0 && <div className="project-nav-empty">Пока нет проектов</div>}
      </nav>
    </div>
  );
}
