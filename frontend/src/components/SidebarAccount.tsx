import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

type Props = {
  collapsed?: boolean;
};

function AccountGlyph() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="8" r="3.5" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M5 19.5c1.6-3.2 4-4.8 7-4.8s5.4 1.6 7 4.8"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Compact link to the personal account page. */
export function SidebarAccount({ collapsed = false }: Props) {
  const { user, portal } = useAuth();
  const isClient = portal?.role === "client";
  const label = isClient
    ? "Личный кабинет"
    : (user?.display_name || user?.name || "Личный кабинет").trim() || "Личный кабинет";

  return (
    <NavLink
      to="/account"
      className={({ isActive }) =>
        `sidebar-account-link${isActive ? " active" : ""}${collapsed ? " is-collapsed" : ""}`
      }
      title={collapsed ? "Личный кабинет" : undefined}
      data-tip="Личный кабинет"
    >
      <span className="feed-nav-icon sidebar-account-glyph" aria-hidden>
        <AccountGlyph />
      </span>
      <span className="sidebar-account-copy">
        <span className="sidebar-account-link-label">{label}</span>
        <span className="sidebar-account-link-hint muted">Профиль и пароль</span>
      </span>
    </NavLink>
  );
}
