import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { Brand } from "./components/Brand";
import { ClientRail } from "./components/ClientRail";
import { OnboardingTour } from "./components/OnboardingTour";
import { ProjectSidebarNav } from "./components/ProjectSidebar";
import { LoginPage } from "./pages/LoginPage";
import { AgencyHome } from "./pages/agency/AgencyHome";
import { ClientProjects } from "./pages/client/ClientProjects";
import { ProjectReports } from "./pages/shared/ProjectReports";
import { ProjectTasks } from "./pages/shared/ProjectTasks";
import { TaskDetail } from "./pages/shared/TaskDetail";

function LogoutRail() {
  const { logout } = useAuth();
  return (
    <aside className="client-rail client-rail-logout-only" aria-label="Выход">
      <button type="button" className="client-avatar logout" title="Выйти" onClick={logout}>
        <span className="client-avatar-face">
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
        </span>
      </button>
    </aside>
  );
}

function AppLayout() {
  const { portal, error } = useAuth();
  const isAgency = portal?.role === "agency";

  return (
    <div className={`app-shell${isAgency ? " with-client-rail" : " with-logout-rail"}`}>
      {isAgency ? <ClientRail /> : <LogoutRail />}
      <aside className="sidebar">
        <Brand subtitle={isAgency ? "Кабинет агентства" : "Кабинет клиента"} />
        <ProjectSidebarNav />
      </aside>
      <main className="main">
        {error && <div className="error-banner">{error}</div>}
        <Outlet />
      </main>
      <OnboardingTour />
    </div>
  );
}

export default function App() {
  const { token, portal, loading, error } = useAuth();

  if (loading) {
    return (
      <div className="login-screen">
        <div className="muted">Загрузка…</div>
      </div>
    );
  }

  if (!token || !portal) {
    return <LoginPage bootError={error} />;
  }

  const isAgency = portal.role === "agency";

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={isAgency ? <AgencyHome /> : <ClientProjects />} />
        <Route path="portals/:portalId/projects" element={<ClientProjects />} />
        <Route path="portals/:portalId/reports/:reportId?" element={<ProjectReports />} />
        <Route path="reports/:reportId?" element={<ProjectReports />} />
        <Route path="projects/:projectId" element={<ProjectTasks />} />
        <Route path="projects/:projectId/reports" element={<ProjectReports />} />
        <Route path="tasks/:taskId" element={<TaskDetail />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
