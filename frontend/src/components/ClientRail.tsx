import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  api,
  unwrapList,
  type Portal,
  type Project,
  type Task,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { portalDisplayName, setPortalLabel } from "../lib/portalLabelCache";
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

function TicketsIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
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

export function ClientRail() {
  const { token, logout } = useAuth();
  const location = useLocation();
  const [resolvedPortalId, setResolvedPortalId] = useState<number | null>(null);
  const [openTickets, setOpenTickets] = useState(0);
  const routePortalId = useMemo(() => {
    const match = location.pathname.match(/^\/portals\/(\d+)/);
    return match ? Number(match[1]) : null;
  }, [location.pathname]);
  const activeId = routePortalId ?? resolvedPortalId;
  const addActive = location.pathname === "/";
  const ticketsActive = location.pathname.startsWith("/tickets");
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
        if (cancelled) return;
        const list = unwrapList(data);
        setLinks(list);
        for (const link of list) {
          const p = link.client_portal;
          const label = portalDisplayName(p);
          if (label) setPortalLabel(p.id, label);
        }
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

  useEffect(() => {
    if (!token) {
      setOpenTickets(0);
      return;
    }
    let cancelled = false;

    async function loadTickets() {
      try {
        const data = await api<{ awaiting_agency?: number }>(
          "/api/tickets/counts/",
          {},
          token!
        );
        if (!cancelled) setOpenTickets(data.awaiting_agency || 0);
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
  }, [token, location.pathname]);

  return (
    <aside className="client-rail" aria-label="Клиенты" data-tour="tour-client-rail">
      <div className="client-rail-list">
        {links.map((link) => {
          const p = link.client_portal;
          const active = !ticketsActive && activeId === p.id;
          const entering = enteringPortalId === p.id;
          return (
            <NavLink
              key={link.id}
              to={`/portals/${p.id}`}
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
          className={`client-avatar add${addActive && !ticketsActive ? " active" : ""}`}
          title="Новый клиент"
          data-tour="tour-add-client"
        >
          <span className="client-avatar-face">+</span>
        </NavLink>
        <NavLink
          to="/tickets"
          className={`client-avatar tickets${ticketsActive ? " active" : ""}`}
          title="Тикеты"
        >
          <span className="client-avatar-face">
            <TicketsIcon />
          </span>
          {openTickets > 0 ? (
            <span className="client-rail-badge" aria-label={`${openTickets} открытых тикетов`}>
              {openTickets > 99 ? "99+" : openTickets}
            </span>
          ) : null}
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
