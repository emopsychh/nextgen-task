import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { api, unwrapList, type Portal, type Project, type Task } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { hueFromId, initialsFromLabel } from "../lib/portalUi";

type LinkRow = {
  id: number;
  client_portal: Portal;
};

function initials(portal: Portal): string {
  return initialsFromLabel(portal.name || portal.domain || "?");
}

function LogoutIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M10 7V6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-7a2 2 0 0 1-2-2v-1"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M15 12H3m0 0 3-3m-3 3 3 3"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function ClientRail() {
  const { token, logout } = useAuth();
  const location = useLocation();
  const [resolvedPortalId, setResolvedPortalId] = useState<number | null>(null);
  const routePortalId = useMemo(() => {
    const match = location.pathname.match(/^\/portals\/(\d+)/);
    return match ? Number(match[1]) : null;
  }, [location.pathname]);
  const activeId = routePortalId ?? resolvedPortalId;
  const addActive = location.pathname === "/";
  const [links, setLinks] = useState<LinkRow[]>([]);
  const [enteringPortalId, setEnteringPortalId] = useState<number | null>(null);

  useEffect(() => {
    if (!token || routePortalId) {
      if (routePortalId) setResolvedPortalId(null);
      return;
    }
    const projectMatch = location.pathname.match(/^\/projects\/(\d+)/);
    const taskMatch = location.pathname.match(/^\/tasks\/(\d+)/);
    let cancelled = false;

    async function resolve() {
      try {
        if (projectMatch) {
          const p = await api<Project>(`/api/projects/${projectMatch[1]}/`, {}, token!);
          if (!cancelled) setResolvedPortalId(p.portal);
          return;
        }
        if (taskMatch) {
          const t = await api<Task>(`/api/tasks/${taskMatch[1]}/`, {}, token!);
          if (!cancelled) setResolvedPortalId(t.portal_id);
          return;
        }
        if (!cancelled) setResolvedPortalId(null);
      } catch {
        if (!cancelled) setResolvedPortalId(null);
      }
    }

    void resolve();
    return () => {
      cancelled = true;
    };
  }, [token, location.pathname, routePortalId]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;

    async function refresh() {
      try {
        const data = await api<LinkRow[] | { results: LinkRow[] }>(
          "/api/portal-links/",
          {},
          token
        );
        if (!cancelled) setLinks(unwrapList(data));
      } catch {
        if (!cancelled) setLinks([]);
      }
    }

    void refresh();

    const onUpdate = (event: Event) => {
      const detail = (event as CustomEvent<{ addedPortalId?: number }>).detail;
      if (detail?.addedPortalId) {
        setEnteringPortalId(detail.addedPortalId);
        window.setTimeout(() => setEnteringPortalId(null), 900);
      }
      void refresh();
    };

    window.addEventListener("clients-updated", onUpdate);
    return () => {
      cancelled = true;
      window.removeEventListener("clients-updated", onUpdate);
    };
  }, [token, location.key]);

  return (
    <aside className="client-rail" aria-label="Клиенты" data-tour="tour-client-rail">
      <div className="client-rail-list">
        {links.map((link) => {
          const p = link.client_portal;
          const active = activeId === p.id;
          const entering = enteringPortalId === p.id;
          return (
            <NavLink
              key={link.id}
              to={`/portals/${p.id}/projects`}
              className={`client-avatar${active ? " active" : ""}${entering ? " is-entering" : ""}`}
              title={p.name || p.domain}
              style={{ ["--avatar-bg" as string]: hueFromId(p.id) }}
              data-tour={links[0]?.id === link.id ? "tour-first-client" : undefined}
            >
              <span className="client-avatar-face">{initials(p)}</span>
            </NavLink>
          );
        })}
        {links.length > 0 && <div className="client-rail-sep" aria-hidden />}
        <NavLink
          to="/"
          end
          className={`client-avatar add${addActive ? " active" : ""}`}
          title="Новый клиент"
          data-tour="tour-add-client"
        >
          <span className="client-avatar-face">+</span>
        </NavLink>
      </div>

      <button
        type="button"
        className="client-avatar logout"
        title="Выйти"
        onClick={logout}
      >
        <span className="client-avatar-face">
          <LogoutIcon />
        </span>
      </button>
    </aside>
  );
}
