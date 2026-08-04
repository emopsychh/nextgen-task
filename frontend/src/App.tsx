import { Fragment, useEffect, type ReactNode } from "react";
import { Navigate, Outlet, Route, Routes, useLocation, useParams } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { Brand } from "./components/Brand";
import { ClientRail } from "./components/ClientRail";
import { OnboardingTour } from "./components/OnboardingTour";
import { ProjectSidebarNav } from "./components/ProjectSidebar";
import { ClientSupportWidget } from "./components/support/ClientSupportWidget";
import {
  SupportWidgetProvider,
  useSupportWidget,
} from "./components/support/SupportWidgetContext";
import { LoginPage } from "./pages/LoginPage";
import { AgencyHome } from "./pages/agency/AgencyHome";
import { ClientBacklog } from "./pages/agency/ClientBacklog";
import { ClientProjects } from "./pages/client/ClientProjects";
import { ProjectReports } from "./pages/shared/ProjectReports";
import { ProjectsList } from "./pages/shared/ProjectsList";
import { ReportDetail } from "./pages/shared/ReportDetail";
import { ProjectTasks } from "./pages/shared/ProjectTasks";
import { SupportTickets } from "./pages/shared/SupportTickets";
import { TaskDetail } from "./pages/shared/TaskDetail";

function RouteDataBoundary({ children }: { children: ReactNode }) {
  const params = useParams();
  const key = [
    params.portalId,
    params.projectId,
    params.reportId,
    params.ticketId,
    params.taskId,
  ]
    .map((value) => value || "")
    .join(":");
  return <Fragment key={key}>{children}</Fragment>;
}

function ClientSidebarLogout() {
  const { logout } = useAuth();
  return (
    <button type="button" className="sidebar-logout" onClick={logout}>
      Выйти
    </button>
  );
}

/** Client deep-links /tickets → stay on work, open corner widget. */
function ClientTicketsRedirect() {
  const { ticketId } = useParams();
  const { open } = useSupportWidget();
  useEffect(() => {
    open(ticketId ? Number(ticketId) : null);
  }, [open, ticketId]);
  return <Navigate to="/" replace />;
}

function AppLayout() {
  const { portal, error } = useAuth();
  const location = useLocation();
  const isAgency = portal?.role === "agency";
  // Task detail: hide the Обзор/projects sidebar — back via «К задачам».
  const taskFocus = /^\/tasks\/[^/]+\/?$/.test(location.pathname);

  return (
    <SupportWidgetProvider>
      <div
        className={`app-shell${isAgency ? " with-client-rail" : ""}${taskFocus ? " task-focus" : ""}`}
      >
        {isAgency ? <ClientRail /> : null}
        {!taskFocus ? (
          <aside className="sidebar">
            <Brand subtitle={isAgency ? "Кабинет агентства" : "Кабинет клиента"} />
            <ProjectSidebarNav />
            {!isAgency ? <ClientSidebarLogout /> : null}
          </aside>
        ) : null}
        <main className="main">
          {error && <div className="error-banner">{error}</div>}
          <Outlet />
        </main>
        <OnboardingTour />
        {!isAgency ? <ClientSupportWidget /> : null}
      </div>
    </SupportWidgetProvider>
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
        <Route
          path="portals/:portalId"
          element={<RouteDataBoundary><ClientProjects /></RouteDataBoundary>}
        />
        <Route
          path="portals/:portalId/projects"
          element={<RouteDataBoundary><ProjectsList /></RouteDataBoundary>}
        />
        <Route
          path="portals/:portalId/backlog"
          element={
            isAgency ? (
              <RouteDataBoundary>
                <ClientBacklog />
              </RouteDataBoundary>
            ) : (
              <Navigate to="/" replace />
            )
          }
        />
        <Route path="projects" element={isAgency ? <Navigate to="/" replace /> : <ProjectsList />} />
        <Route
          path="portals/:portalId/reports"
          element={<RouteDataBoundary><ProjectReports /></RouteDataBoundary>}
        />
        <Route
          path="portals/:portalId/reports/:reportId"
          element={<RouteDataBoundary><ReportDetail /></RouteDataBoundary>}
        />
        <Route
          path="portals/:portalId/tickets"
          element={<RouteDataBoundary><SupportTickets /></RouteDataBoundary>}
        />
        <Route
          path="portals/:portalId/tickets/:ticketId"
          element={<RouteDataBoundary><SupportTickets /></RouteDataBoundary>}
        />
        <Route path="reports" element={<ProjectReports />} />
        <Route
          path="reports/:reportId"
          element={<RouteDataBoundary><ReportDetail /></RouteDataBoundary>}
        />
        <Route
          path="tickets"
          element={isAgency ? <SupportTickets /> : <ClientTicketsRedirect />}
        />
        <Route
          path="tickets/:ticketId"
          element={isAgency ? <SupportTickets /> : <ClientTicketsRedirect />}
        />
        <Route
          path="projects/:projectId"
          element={<RouteDataBoundary><ProjectTasks /></RouteDataBoundary>}
        />
        <Route
          path="projects/:projectId/reports"
          element={<RouteDataBoundary><ProjectReports /></RouteDataBoundary>}
        />
        <Route
          path="tasks/:taskId"
          element={<RouteDataBoundary><TaskDetail /></RouteDataBoundary>}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
