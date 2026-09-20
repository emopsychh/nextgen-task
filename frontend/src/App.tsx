import { Fragment, useCallback, useEffect, useState, type ReactNode } from "react";
import { Navigate, Outlet, Route, Routes, useLocation, useParams } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { Brand } from "./components/Brand";
import { ClientRail } from "./components/ClientRail";
import { OnboardingTour } from "./components/OnboardingTour";
import { ProjectSidebarNav } from "./components/ProjectSidebar";
import { SidebarAccount } from "./components/SidebarAccount";
import { LoginPage } from "./pages/LoginPage";
import { AgencyDashboard } from "./pages/agency/AgencyDashboard";
import { AgencyHome } from "./pages/agency/AgencyHome";
import { ClientBacklog } from "./pages/agency/ClientBacklog";
import { ClientProjects } from "./pages/client/ClientProjects";
import { ClientTaskRequests } from "./pages/client/ClientTaskRequests";
import { ProjectReports } from "./pages/shared/ProjectReports";
import { ProjectsList } from "./pages/shared/ProjectsList";
import { ReportDetail } from "./pages/shared/ReportDetail";
import { ProjectTasks } from "./pages/shared/ProjectTasks";
import { AccountPage } from "./pages/shared/AccountPage";
import { TaskDetail } from "./pages/shared/TaskDetail";

function RouteDataBoundary({ children }: { children: ReactNode }) {
  const params = useParams();
  const key = [
    params.portalId,
    params.projectId,
    params.reportId,
    params.taskId,
  ]
    .map((value) => value || "")
    .join(":");
  return <Fragment key={key}>{children}</Fragment>;
}

function sidebarCollapseKey(portalId: number, userId: number): string {
  return `nextgen_sidebar_collapsed_${portalId}_${userId}`;
}

function readSidebarCollapsed(portalId: number, userId: number): boolean {
  try {
    return localStorage.getItem(sidebarCollapseKey(portalId, userId)) === "1";
  } catch {
    return false;
  }
}

function writeSidebarCollapsed(portalId: number, userId: number, value: boolean): void {
  try {
    localStorage.setItem(sidebarCollapseKey(portalId, userId), value ? "1" : "0");
  } catch {
    // ignore
  }
}

function SidebarRailChevron({ collapsed }: { collapsed: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d={collapsed ? "M10 6l6 6-6 6" : "M14 6l-6 6 6 6"}
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function AppLayout() {
  const { portal, user, error } = useAuth();
  const location = useLocation();
  const isAgency = portal?.role === "agency";
  const portalId = portal?.id || 0;
  const userId = user?.id || 0;
  // Task detail: hide the Обзор/projects sidebar — back via SmartBack.
  const taskFocus = /^\/tasks\/[^/]+\/?$/.test(location.pathname);
  const [collapsed, setCollapsed] = useState(() =>
    portalId && userId ? readSidebarCollapsed(portalId, userId) : false
  );

  useEffect(() => {
    if (portalId && userId) {
      setCollapsed(readSidebarCollapsed(portalId, userId));
    }
  }, [portalId, userId]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      if (portalId && userId) writeSidebarCollapsed(portalId, userId, next);
      return next;
    });
  }, [portalId, userId]);

  return (
    <div
      className={`app-shell${isAgency ? " with-client-rail" : ""}${
        taskFocus ? " task-focus" : ""
      }${collapsed && !taskFocus ? " sidebar-collapsed" : ""}`}
    >
      {isAgency ? <ClientRail /> : null}
      {!taskFocus ? (
        <aside className={`sidebar${collapsed ? " is-collapsed" : ""}`}>
          <Brand
            compact={collapsed}
            subtitle={isAgency ? "Кабинет агентства" : "Кабинет клиента"}
          />
          <ProjectSidebarNav collapsed={collapsed} />
          <div className="sidebar-footer">
            <SidebarAccount collapsed={collapsed} />
          </div>
          <button
            type="button"
            className="sidebar-rail-toggle"
            onClick={toggleCollapsed}
            title={collapsed ? "Развернуть меню" : "Свернуть меню"}
            aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
            aria-expanded={!collapsed}
          >
            <SidebarRailChevron collapsed={collapsed} />
          </button>
        </aside>
      ) : null}
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
        <Route
          path="dashboard"
          element={isAgency ? <AgencyDashboard /> : <Navigate to="/" replace />}
        />
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
          path="requests"
          element={isAgency ? <Navigate to="/" replace /> : <ClientTaskRequests />}
        />
        <Route
          path="portals/:portalId/reports"
          element={<RouteDataBoundary><ProjectReports /></RouteDataBoundary>}
        />
        <Route
          path="portals/:portalId/reports/new"
          element={<Navigate to=".." relative="path" replace />}
        />
        <Route
          path="portals/:portalId/reports/:reportId"
          element={<RouteDataBoundary><ReportDetail /></RouteDataBoundary>}
        />
        <Route path="reports" element={<ProjectReports />} />
        <Route
          path="reports/:reportId"
          element={<RouteDataBoundary><ReportDetail /></RouteDataBoundary>}
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
        <Route path="account" element={<AccountPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
