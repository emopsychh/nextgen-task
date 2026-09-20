import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useLocation, useParams } from "react-router-dom";
import {
  api,
  unwrapList,
  type Project,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { usePortalLiveSync } from "../hooks/usePortalLiveSync";
import { useSeenProjects } from "../hooks/useSeenProjects";
import {
  getPortalLabel,
  PORTAL_LABEL_EVENT,
  portalDisplayName,
  setPortalLabel,
} from "../lib/portalLabelCache";
import { PICKER_PAGE_SIZE, withPage } from "../lib/pagination";
import {
  CACHE_PROJECTS,
  readPortalCache,
  writePortalCache,
} from "../lib/portalSessionCache";
import { HouseGlyph } from "./icons";

export function ProjectSidebarNav() {
  const { token, portal } = useAuth();
  const params = useParams();
  const location = useLocation();
  const isAgency = portal?.role === "agency";

  const routePortalId = params.portalId ? Number(params.portalId) : null;
  const routeProjectId = params.projectId ? Number(params.projectId) : null;

  const [projects, setProjects] = useState<Project[]>([]);
  const [resolvedPortalId, setResolvedPortalId] = useState<number | null>(null);
  const [clientLabel, setClientLabel] = useState("");
  const [reportsAttention, setReportsAttention] = useState(0);
  const [pendingRequests, setPendingRequests] = useState(0);
  const lastPortalRef = useRef<number | null>(null);

  const contextPortalId = useMemo(() => {
    if (routePortalId) return routePortalId;
    if (!isAgency && portal?.id) return portal.id;
    if (resolvedPortalId) return resolvedPortalId;
    if (routeProjectId && lastPortalRef.current) return lastPortalRef.current;
    return null;
  }, [routePortalId, isAgency, portal?.id, resolvedPortalId, routeProjectId]);

  const { seedIfEmpty, unseenCount } = useSeenProjects(contextPortalId);
  const projectsUnseen = unseenCount(projects);
  const reportsCountCache = `sidebar-reports:${isAgency ? "agency" : "client"}`;
  const requestsCountCache = `sidebar-requests:${isAgency ? "agency" : "client"}`;

  useEffect(() => {
    if (contextPortalId) lastPortalRef.current = contextPortalId;
  }, [contextPortalId]);

  // Instant label from auth / ClientRail cache — don't wait for projects list
  useEffect(() => {
    setClientLabel("");
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
    if (!contextPortalId) {
      setReportsAttention(0);
      setPendingRequests(0);
      return;
    }
    setReportsAttention(
      readPortalCache<number>(reportsCountCache, contextPortalId) || 0
    );
    setPendingRequests(
      readPortalCache<number>(requestsCountCache, contextPortalId) || 0
    );
  }, [contextPortalId, reportsCountCache, requestsCountCache]);

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
    if (isAgency && !routePortalId && !routeProjectId) {
      lastPortalRef.current = null;
      setResolvedPortalId(null);
      setProjects([]);
      setClientLabel("");
      setReportsAttention(0);
      setPendingRequests(0);
    }
  }, [isAgency, routePortalId, routeProjectId]);

  const showClientNav = Boolean(contextPortalId);

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

    // This sidebar survives route changes. Clear the previous tenant before
    // hydrating the cache for the newly selected portal.
    setProjects([]);
    const cached = readPortalCache<Project[]>(CACHE_PROJECTS, contextPortalId);
    const scoped =
      cached?.filter((project) => project.portal === contextPortalId) || [];
    if (scoped.length) {
      setProjects(scoped);
      seedIfEmpty(scoped.map((p) => p.id));
    }

    let cancelled = false;
    async function load() {
      try {
        const data = await api<Project[] | { results: Project[] }>(
          withPage(`/api/projects/?portal=${contextPortalId}`, 1, PICKER_PAGE_SIZE),
          {},
          token!
        );
        if (cancelled) return;
        const list = unwrapList(data).filter(
          (project) => project.portal === contextPortalId
        );
        setProjects(list);
        seedIfEmpty(list.map((p) => p.id));
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
  }, [token, contextPortalId, seedIfEmpty]);

  useEffect(() => {
    if (!token || !contextPortalId) {
      if (!contextPortalId) setReportsAttention(0);
      return;
    }
    const portalId = contextPortalId;
    let cancelled = false;

    async function loadReports() {
      try {
        const data = await api<{
          draft?: number;
          disputed?: number;
          review?: number;
        }>(`/api/reports/counts/?portal=${portalId}`, {}, token!);
        if (cancelled) return;
        if (isAgency) {
          const count = (data.draft || 0) + (data.disputed || 0);
          setReportsAttention(count);
          writePortalCache(reportsCountCache, portalId, count);
        } else {
          const count = data.review || 0;
          setReportsAttention(count);
          writePortalCache(reportsCountCache, portalId, count);
        }
      } catch {
        // Keep the last known count.
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
  }, [token, contextPortalId, isAgency, location.pathname, reportsCountCache]);

  useEffect(() => {
    if (!token || !contextPortalId) {
      if (!contextPortalId) setPendingRequests(0);
      return;
    }
    const portalId = contextPortalId;
    let cancelled = false;

    async function loadRequests() {
      try {
        const data = await api<{ pending?: number }>(
          `/api/backlog-items/counts/?portal=${portalId}`,
          {},
          token!
        );
        if (cancelled) return;
        const count = data.pending || 0;
        setPendingRequests(count);
        writePortalCache(requestsCountCache, portalId, count);
      } catch {
        // Keep the last known count.
      }
    }

    void loadRequests();
    const onVisible = () => {
      if (document.visibilityState === "visible") void loadRequests();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [token, contextPortalId, location.pathname, requestsCountCache]);

  usePortalLiveSync({
    token,
    portalId: contextPortalId,
    enabled: !!contextPortalId,
    onEvent: (payload) => {
      if (!token) return;
      const kind = payload?.kind || "";
      // Cursor polling only carries a version, not the original event kind.
      // In that fallback mode refresh all small sidebar datasets.
      const cursorBump = !kind && typeof payload?.v === "number";
      const refreshReports = cursorBump || kind.startsWith("report_");
      const refreshRequests = cursorBump || kind.startsWith("backlog_");
      const refreshProjects =
        cursorBump ||
        kind.startsWith("project_") ||
        kind.startsWith("task_") ||
        kind === "ontaskadd" ||
        kind === "ontaskupdate" ||
        kind === "ontaskdelete";
      if (!refreshReports && !refreshProjects && !refreshRequests) return;
      void (async () => {
        try {
          if (refreshProjects && contextPortalId) {
            const data = await api<Project[] | { results: Project[] }>(
              withPage(`/api/projects/?portal=${contextPortalId}`, 1, PICKER_PAGE_SIZE),
              {},
              token
            );
            const list = unwrapList(data).filter(
              (project) => project.portal === contextPortalId
            );
            setProjects(list);
            seedIfEmpty(list.map((p) => p.id));
            writePortalCache(CACHE_PROJECTS, contextPortalId, list);
          }
          if (refreshReports && contextPortalId) {
            const data = await api<{
              draft?: number;
              disputed?: number;
              review?: number;
            }>(`/api/reports/counts/?portal=${contextPortalId}`, {}, token);
            if (isAgency) {
              const count = (data.draft || 0) + (data.disputed || 0);
              setReportsAttention(count);
              writePortalCache(reportsCountCache, contextPortalId, count);
            } else {
              const count = data.review || 0;
              setReportsAttention(count);
              writePortalCache(reportsCountCache, contextPortalId, count);
            }
          }
          if (refreshRequests && contextPortalId) {
            const data = await api<{ pending?: number }>(
              `/api/backlog-items/counts/?portal=${contextPortalId}`,
              {},
              token
            );
            const count = data.pending || 0;
            setPendingRequests(count);
            writePortalCache(requestsCountCache, contextPortalId, count);
          }
        } catch {
          // keep previous
        }
      })();
    },
  });

  if (!showClientNav) {
    return (
      <nav className="nav-list" data-tour="tour-sidebar">
        <NavLink to="/" end className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          Обзор
        </NavLink>
        {isAgency ? (
          <NavLink
            to="/dashboard"
            className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
          >
            Дашборд
          </NavLink>
        ) : null}
        {isAgency ? (
          <p className="sidebar-hint muted">
            {location.pathname.startsWith("/dashboard")
              ? "Горящие задачи со всех клиентов в одном месте."
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
        {isAgency ? clientLabel || "Кабинет клиента" : "Навигация"}
      </div>
      <NavLink
        to={feedTo}
        end
        className={({ isActive }) => `feed-nav-item${isActive || onFeed ? " active" : ""}`}
      >
        <span className="feed-nav-icon" aria-hidden>
          <HouseGlyph />
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
        {projectsUnseen > 0 ? (
          <span className="feed-nav-count" aria-label={`${projectsUnseen} новых проектов`}>
            {projectsUnseen > 99 ? "99+" : projectsUnseen}
          </span>
        ) : null}
      </NavLink>
      {!isAgency ? (
        <NavLink
          to="/requests"
          className={({ isActive }) => `feed-nav-item${isActive ? " active" : ""}`}
        >
          <span className="feed-nav-icon" aria-hidden>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path
                d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </span>
          <span className="feed-nav-label">На согласование</span>
          {pendingRequests > 0 ? (
            <span className="feed-nav-count" aria-label={`${pendingRequests} заявок`}>
              {pendingRequests > 99 ? "99+" : pendingRequests}
            </span>
          ) : null}
        </NavLink>
      ) : null}
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
      {isAgency ? (
        <NavLink
          to={`/portals/${contextPortalId}/backlog`}
          className={({ isActive }) => `feed-nav-item${isActive ? " active" : ""}`}
        >
          <span className="feed-nav-icon" aria-hidden>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path
                d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </span>
          <span className="feed-nav-label">Бэклог</span>
          {pendingRequests > 0 ? (
            <span className="feed-nav-count" aria-label={`${pendingRequests} заявок клиента`}>
              {pendingRequests > 99 ? "99+" : pendingRequests}
            </span>
          ) : null}
        </NavLink>
      ) : null}
    </div>
  );
}
