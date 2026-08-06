import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  api,
  unwrapList,
  type AgencyUserPreferences,
  type Portal,
  type Project,
  type Task,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { portalDisplayName, setPortalLabel } from "../lib/portalLabelCache";
import { readPortalCache, writePortalCache } from "../lib/portalSessionCache";
import { hueFromId, initialsFromLabel } from "../lib/portalUi";

const CACHE_AGENCY_LINKS = "agency-links";
const CACHE_AGENCY_TICKET_COUNT = "agency-ticket-count";

type LinkRow = {
  id: number;
  client_portal: Portal;
};

type ContextMenuState = {
  x: number;
  y: number;
  portalId: number;
  label: string;
  isFavorite: boolean;
};

function initials(portal: Portal): string {
  return initialsFromLabel(portal.name || portal.domain || "?");
}

function collapseStorageKey(agencyId: number, userId: number): string {
  return `nextgen_rail_collapsed_${agencyId}_${userId}`;
}

function readCollapsed(agencyId: number, userId: number): boolean {
  try {
    return localStorage.getItem(collapseStorageKey(agencyId, userId)) === "1";
  } catch {
    return false;
  }
}

function writeCollapsed(agencyId: number, userId: number, value: boolean): void {
  try {
    localStorage.setItem(collapseStorageKey(agencyId, userId), value ? "1" : "0");
  } catch {
    // ignore
  }
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

function DashboardIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3" y="3" width="8" height="8" rx="1.5" stroke="currentColor" strokeWidth="2" />
      <rect x="13" y="3" width="8" height="5" rx="1.5" stroke="currentColor" strokeWidth="2" />
      <rect x="13" y="10" width="8" height="11" rx="1.5" stroke="currentColor" strokeWidth="2" />
      <rect x="3" y="13" width="8" height="8" rx="1.5" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function StarIcon({ filled = false }: { filled?: boolean }) {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" aria-hidden>
      <path
        d="M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8L12 16.8 6.7 19.6l1-5.8L3.5 9.7l5.9-.9L12 3.5z"
        fill={filled ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CollapseIcon({ collapsed }: { collapsed: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      {collapsed ? (
        <path
          d="M6 9l6 6 6-6"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : (
        <path
          d="M6 15l6-6 6 6"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}

export function ClientRail() {
  const { token, logout, portal, user } = useAuth();
  const location = useLocation();
  const agencyId = portal?.id || 0;
  const userId = user?.id || 0;
  const [resolvedPortalId, setResolvedPortalId] = useState<number | null>(null);
  const [openTickets, setOpenTickets] = useState(
    () =>
      readPortalCache<number>(
        CACHE_AGENCY_TICKET_COUNT,
        portal?.id || 0
      ) || 0
  );
  const routePortalId = useMemo(() => {
    const match = location.pathname.match(/^\/portals\/(\d+)/);
    return match ? Number(match[1]) : null;
  }, [location.pathname]);
  const activeId = routePortalId ?? resolvedPortalId;
  const addActive = location.pathname === "/";
  const ticketsActive = location.pathname.startsWith("/tickets");
  const dashboardActive = location.pathname.startsWith("/dashboard");
  const clientNavActive = !ticketsActive && !dashboardActive;
  const [links, setLinks] = useState<LinkRow[]>(
    () =>
      readPortalCache<LinkRow[]>(CACHE_AGENCY_LINKS, portal?.id || 0) || []
  );
  const [enteringPortalId, setEnteringPortalId] = useState<number | null>(null);
  const [favoriteIds, setFavoriteIds] = useState<number[]>([]);
  const [collapsed, setCollapsed] = useState(() =>
    agencyId && userId ? readCollapsed(agencyId, userId) : false
  );
  const [menu, setMenu] = useState<ContextMenuState | null>(null);
  const longPressTimer = useRef<number | null>(null);
  const longPressFired = useRef(false);

  useEffect(() => {
    setLinks(
      readPortalCache<LinkRow[]>(CACHE_AGENCY_LINKS, portal?.id || 0) || []
    );
    setOpenTickets(
      readPortalCache<number>(
        CACHE_AGENCY_TICKET_COUNT,
        portal?.id || 0
      ) || 0
    );
    if (agencyId && userId) {
      setCollapsed(readCollapsed(agencyId, userId));
    }
  }, [portal?.id, agencyId, userId]);

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
        if (portal?.id) {
          writePortalCache(CACHE_AGENCY_LINKS, portal.id, list);
        }
        for (const link of list) {
          const p = link.client_portal;
          const label = portalDisplayName(p);
          if (label) setPortalLabel(p.id, label);
        }
      } catch {
        // Keep the last successful snapshot during transient API failures.
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
  }, [token, portal?.id]);

  useEffect(() => {
    if (!token) {
      setFavoriteIds([]);
      return;
    }
    let cancelled = false;
    async function loadPrefs() {
      try {
        const data = await api<AgencyUserPreferences>(
          "/api/me/preferences/",
          {},
          token!
        );
        if (!cancelled) {
          setFavoriteIds(
            Array.isArray(data.favorite_client_ids) ? data.favorite_client_ids : []
          );
        }
      } catch {
        // Keep empty favorites on failure.
      }
    }
    void loadPrefs();
    return () => {
      cancelled = true;
    };
  }, [token, userId]);

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
        if (!cancelled) {
          const count = data.awaiting_agency || 0;
          setOpenTickets(count);
          if (portal?.id) {
            writePortalCache(CACHE_AGENCY_TICKET_COUNT, portal.id, count);
          }
        }
      } catch {
        // Keep the last known count.
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
  }, [token, location.pathname, portal?.id]);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const favoriteSet = useMemo(() => new Set(favoriteIds), [favoriteIds]);

  const sortedLinks = useMemo(() => {
    const fav: LinkRow[] = [];
    const rest: LinkRow[] = [];
    for (const link of links) {
      if (favoriteSet.has(link.client_portal.id)) fav.push(link);
      else rest.push(link);
    }
    // Keep favorites in the order stored in preferences when possible.
    fav.sort((a, b) => {
      const ai = favoriteIds.indexOf(a.client_portal.id);
      const bi = favoriteIds.indexOf(b.client_portal.id);
      return ai - bi;
    });
    return [...fav, ...rest];
  }, [links, favoriteSet, favoriteIds]);

  const visibleLinks = useMemo(() => {
    if (!collapsed) return sortedLinks;
    return sortedLinks.filter((link) => {
      const id = link.client_portal.id;
      return favoriteSet.has(id) || id === activeId;
    });
  }, [sortedLinks, collapsed, favoriteSet, activeId]);

  const hiddenCount = Math.max(0, sortedLinks.length - visibleLinks.length);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      if (agencyId && userId) writeCollapsed(agencyId, userId, next);
      return next;
    });
  }, [agencyId, userId]);

  const persistFavorites = useCallback(
    async (next: number[]) => {
      setFavoriteIds(next);
      if (!token) return;
      try {
        const data = await api<AgencyUserPreferences>(
          "/api/me/preferences/",
          {
            method: "PUT",
            body: JSON.stringify({ favorite_client_ids: next }),
          },
          token
        );
        setFavoriteIds(
          Array.isArray(data.favorite_client_ids) ? data.favorite_client_ids : next
        );
      } catch {
        // Keep optimistic local state; next load will reconcile.
      }
    },
    [token]
  );

  const toggleFavorite = useCallback(
    (portalId: number) => {
      setMenu(null);
      const isFav = favoriteSet.has(portalId);
      const next = isFav
        ? favoriteIds.filter((id) => id !== portalId)
        : [...favoriteIds, portalId];
      void persistFavorites(next);
    },
    [favoriteSet, favoriteIds, persistFavorites]
  );

  function openMenu(
    e: { clientX: number; clientY: number; preventDefault: () => void },
    portalRow: Portal
  ) {
    e.preventDefault();
    const label = portalRow.name || portalRow.domain || `Клиент #${portalRow.id}`;
    setMenu({
      x: e.clientX,
      y: e.clientY,
      portalId: portalRow.id,
      label,
      isFavorite: favoriteSet.has(portalRow.id),
    });
  }

  function clearLongPress() {
    if (longPressTimer.current != null) {
      window.clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  }

  return (
    <aside className="client-rail" aria-label="Клиенты" data-tour="tour-client-rail">
      <div className="client-rail-list">
        {visibleLinks.map((link) => {
          const p = link.client_portal;
          const active = clientNavActive && activeId === p.id;
          const entering = enteringPortalId === p.id;
          const isFavorite = favoriteSet.has(p.id);
          return (
            <NavLink
              key={link.id}
              to={`/portals/${p.id}`}
              className={`client-avatar${active ? " active" : ""}${entering ? " is-entering" : ""}${isFavorite ? " is-favorite" : ""}`}
              title={p.name || p.domain}
              style={{ ["--avatar-bg" as string]: hueFromId(p.id) }}
              data-tour={links[0]?.id === link.id ? "tour-first-client" : undefined}
              onContextMenu={(e) => openMenu(e, p)}
              onPointerDown={(e) => {
                if (e.pointerType === "touch" || e.pointerType === "pen") {
                  longPressFired.current = false;
                  clearLongPress();
                  longPressTimer.current = window.setTimeout(() => {
                    longPressFired.current = true;
                    openMenu(
                      {
                        clientX: e.clientX,
                        clientY: e.clientY,
                        preventDefault: () => undefined,
                      },
                      p
                    );
                  }, 480);
                }
              }}
              onPointerUp={clearLongPress}
              onPointerLeave={clearLongPress}
              onPointerCancel={clearLongPress}
              onClick={(e) => {
                if (longPressFired.current) {
                  e.preventDefault();
                  longPressFired.current = false;
                }
              }}
            >
              <span className="client-avatar-face">{initials(p)}</span>
              {isFavorite ? (
                <span className="client-favorite-mark" aria-label="Избранный">
                  <StarIcon filled />
                </span>
              ) : null}
            </NavLink>
          );
        })}

        {sortedLinks.length > 0 ? (
          <button
            type="button"
            className={`client-avatar rail-collapse${collapsed ? " is-collapsed" : ""}`}
            title={
              collapsed
                ? hiddenCount > 0
                  ? `Развернуть (+${hiddenCount})`
                  : "Развернуть"
                : "Свернуть список"
            }
            aria-expanded={!collapsed}
            onClick={toggleCollapsed}
          >
            <span className="client-avatar-face">
              <CollapseIcon collapsed={collapsed} />
            </span>
          </button>
        ) : null}

        {visibleLinks.length > 0 || sortedLinks.length > 0 ? (
          <div className="client-rail-sep" aria-hidden />
        ) : null}

        <NavLink
          to="/"
          end
          className={`client-avatar add${addActive && clientNavActive ? " active" : ""}`}
          title="Новый клиент"
          data-tour="tour-add-client"
        >
          <span className="client-avatar-face">+</span>
        </NavLink>
        <NavLink
          to="/dashboard"
          className={`client-avatar dashboard${dashboardActive ? " active" : ""}`}
          title="Рабочее пространство"
          data-tour="tour-agency-dashboard"
        >
          <span className="client-avatar-face">
            <DashboardIcon />
          </span>
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

      {menu ? (
        <div
          className="client-rail-menu"
          style={{ left: menu.x, top: menu.y }}
          role="menu"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="client-rail-menu-label" title={menu.label}>
            {menu.label}
          </div>
          <button
            type="button"
            className="client-rail-menu-item"
            role="menuitem"
            onClick={() => toggleFavorite(menu.portalId)}
          >
            <StarIcon filled={menu.isFavorite} />
            {menu.isFavorite ? "Убрать из избранного" : "В избранное"}
          </button>
        </div>
      ) : null}
    </aside>
  );
}
