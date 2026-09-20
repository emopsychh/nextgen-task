import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { ChangePasswordModal } from "./ChangePasswordModal";

/** Logout + change-password controls for agency and client sidebars. */
export function SidebarAccount() {
  const { logout, user, portal } = useAuth();
  const [pwdOpen, setPwdOpen] = useState(false);
  const isClient = portal?.role === "client";
  // Agency staff: show person name. Clients don't need a redundant portal label here.
  const label = isClient
    ? null
    : (user?.display_name || user?.name || "Аккаунт").trim() || "Аккаунт";

  return (
    <>
      <div className="sidebar-account">
        {label ? (
          <div className="sidebar-account-name muted" title={label}>
            {label}
          </div>
        ) : null}
        <button
          type="button"
          className="sidebar-logout"
          onClick={() => setPwdOpen(true)}
        >
          Сменить пароль
        </button>
        <button type="button" className="sidebar-logout" onClick={logout}>
          Выйти
        </button>
      </div>
      <ChangePasswordModal open={pwdOpen} onClose={() => setPwdOpen(false)} />
    </>
  );
}
