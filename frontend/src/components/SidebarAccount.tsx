import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { ChangePasswordModal } from "./ChangePasswordModal";

/** Logout + change-password controls for agency and client sidebars. */
export function SidebarAccount() {
  const { logout, user } = useAuth();
  const [pwdOpen, setPwdOpen] = useState(false);
  const label = user?.display_name || user?.name || "Аккаунт";

  return (
    <>
      <div className="sidebar-account">
        <div className="sidebar-account-name muted" title={label}>
          {label}
        </div>
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
