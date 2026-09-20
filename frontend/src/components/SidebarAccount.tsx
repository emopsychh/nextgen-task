import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

/** Compact link to the personal account page. */
export function SidebarAccount() {
  const { user, portal } = useAuth();
  const isClient = portal?.role === "client";
  const label = isClient
    ? "Личный кабинет"
    : (user?.display_name || user?.name || "Личный кабинет").trim() || "Личный кабинет";

  return (
    <div className="sidebar-account">
      <NavLink
        to="/account"
        className={({ isActive }) => `sidebar-account-link${isActive ? " active" : ""}`}
        title="Личный кабинет"
      >
        <span className="sidebar-account-link-label">{label}</span>
        <span className="sidebar-account-link-hint muted">Профиль и пароль</span>
      </NavLink>
    </div>
  );
}
