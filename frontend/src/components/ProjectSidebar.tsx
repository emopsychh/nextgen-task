import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useLocation, useParams } from "react-router-dom";
import { api, unwrapList, type Project } from "../api/types";
import { useAuth } from "../auth/AuthContext";

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
  const lastPortalRef = useRef<number | null>(null);

  // Keep last known portal so /projects/:id doesn't blank the sidebar while resolving.
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
    if (isAgency && !routePortalId && !routeProjectId) {
      lastPortalRef.current = null;
      setResolvedPortalId(null);
      setProjects([]);
      setClientLabel("");
    }
  }, [isAgency, routePortalId, routeProjectId]);

  const showProjects = Boolean(contextPortalId);

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
  }, [token, contextPortalId]);

  if (!showProjects) {
    return (
      <nav className="nav-list" data-tour="tour-sidebar">
        <NavLink to="/" end className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          Обзор
        </NavLink>
        <p className="sidebar-hint muted">
          Выберите клиента слева, чтобы открыть проекты и ленту активности.
        </p>
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
        Лента
      </NavLink>
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
