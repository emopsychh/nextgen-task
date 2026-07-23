import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  api,
  unwrapList,
  type Paginated,
  type Project,
  type WorkReport,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { formatDateTime, formatDuration } from "../../lib/format";
import {
  REPORT_BUCKETS,
  type ReportBucket,
  reportDetailPath,
  reportsApiQuery,
  reportSubtitle,
  reportTitle,
  STATUS_LABEL_RU,
} from "./reportHelpers";

export function ProjectReports() {
  const { portalId: routePortalId, projectId: routeProjectId } = useParams();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const navigate = useNavigate();
  const toast = useFlashToast();

  const [resolvedPortalId, setResolvedPortalId] = useState<number | null>(null);

  const portalId = useMemo(() => {
    if (routePortalId) return Number(routePortalId);
    if (resolvedPortalId) return resolvedPortalId;
    if (!isAgency && portal?.id) return portal.id;
    return null;
  }, [routePortalId, resolvedPortalId, isAgency, portal?.id]);

  useEffect(() => {
    if (!token || !routeProjectId || routePortalId) return;
    let cancelled = false;
    void api<Project>(`/api/projects/${routeProjectId}/`, {}, token)
      .then((p) => {
        if (!cancelled) setResolvedPortalId(p.portal);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [token, routeProjectId, routePortalId]);

  const [bucket, setBucket] = useState<ReportBucket>("all");
  const [reports, setReports] = useState<WorkReport[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [pickedProjects, setPickedProjects] = useState<Set<number>>(new Set());
  const [counts, setCounts] = useState<Record<ReportBucket, number>>({
    all: 0,
    current: 0,
    review: 0,
    paid: 0,
  });

  const loadCounts = useCallback(async () => {
    if (!token || !portalId) return;
    const next: Record<ReportBucket, number> = {
      all: 0,
      current: 0,
      review: 0,
      paid: 0,
    };
    await Promise.all(
      (["all", "current", "review", "paid"] as ReportBucket[]).map(async (b) => {
        const data = await api<WorkReport[] | Paginated<WorkReport>>(
          reportsApiQuery(portalId, b),
          {},
          token
        );
        next[b] = unwrapList(data).length;
      })
    );
    setCounts(next);
  }, [token, portalId]);

  const loadList = useCallback(async () => {
    if (!token || !portalId) return;
    const data = await api<WorkReport[] | Paginated<WorkReport>>(
      reportsApiQuery(portalId, bucket),
      {},
      token
    );
    setReports(unwrapList(data));
    void loadCounts();
  }, [token, portalId, bucket, loadCounts]);

  const loadProjects = useCallback(async () => {
    if (!token || !portalId) return;
    const data = await api<Project[] | Paginated<Project>>(
      `/api/projects/?portal=${portalId}`,
      {},
      token
    );
    setProjects(unwrapList(data));
  }, [token, portalId]);

  useEffect(() => {
    if (!token || !portalId) return;
    void loadList().catch((e) => setError(e instanceof Error ? e.message : "Ошибка"));
    void loadProjects().catch(() => undefined);
  }, [token, portalId, loadList, loadProjects]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: !!portalId,
    onEvent: (payload) => {
      if (payload?.kind?.startsWith("report_") || !payload?.kind) {
        void loadList().catch(() => undefined);
      }
    },
  });

  async function createReport() {
    if (!token || !portalId || pickedProjects.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api<WorkReport>(
        "/api/reports/",
        {
          method: "POST",
          body: JSON.stringify({
            portal: portalId,
            project_ids: Array.from(pickedProjects),
          }),
        },
        token
      );
      toast.show("Итоги задач подтянутся автоматически", "Отчёт создан");
      setShowCreate(false);
      setPickedProjects(new Set());
      setBucket("all");
      navigate(reportDetailPath(portalId, isAgency, created.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать отчёт");
    } finally {
      setBusy(false);
    }
  }

  function toggleProjectPick(id: number) {
    setPickedProjects((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function openReport(id: number) {
    navigate(reportDetailPath(portalId, isAgency, id));
  }

  if (!portalId) {
    return (
      <div className="tasks-page">
        <p className="muted">Выберите клиента, чтобы открыть отчёты.</p>
      </div>
    );
  }

  return (
    <div className="tasks-page report-hub">
      <div className="page-header">
        <div>
          <h1 className="page-title">Отчёты</h1>
          <p className="page-sub">
            Согласование выполненных работ по проектам клиента
          </p>
        </div>
        {isAgency ? (
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={() => {
              setShowCreate(true);
              void loadProjects();
            }}
          >
            Создать отчёт
          </button>
        ) : null}
      </div>

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <div className="task-filters report-filter-row">
        {REPORT_BUCKETS.map((b) => (
          <button
            key={b.id}
            type="button"
            className={`task-filter-chip${bucket === b.id ? " active" : ""}`}
            onClick={() => setBucket(b.id)}
          >
            {b.label}
            <span className="task-filter-count">{counts[b.id]}</span>
          </button>
        ))}
      </div>

      {showCreate && isAgency ? (
        <div className="connect-panel stack report-create-panel">
          <div>
            <h2 className="section-title">Новый отчёт</h2>
            <p className="muted">Выберите один или несколько проектов.</p>
          </div>
          <ul className="report-project-pick">
            {projects.map((p) => (
              <li key={p.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={pickedProjects.has(p.id)}
                    onChange={() => toggleProjectPick(p.id)}
                  />
                  <span>{p.name}</span>
                  <span className="muted">
                    {p.done_count}/{p.tasks_count} задач
                  </span>
                </label>
              </li>
            ))}
            {projects.length === 0 ? (
              <li className="muted">Пока нет проектов у этого клиента</li>
            ) : null}
          </ul>
          <div className="report-create-actions">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setShowCreate(false);
                setPickedProjects(new Set());
              }}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn btn-accent"
              disabled={busy || pickedProjects.size === 0}
              onClick={() => void createReport()}
            >
              Создать
            </button>
          </div>
        </div>
      ) : null}

      {reports.length === 0 ? (
        <div className="report-list-empty-card">
          <p>В этой вкладке пока пусто.</p>
          {isAgency && (bucket === "all" || bucket === "current") ? (
            <p className="muted">Нажмите «Создать отчёт», чтобы собрать проекты.</p>
          ) : null}
        </div>
      ) : (
        <ul className="report-card-grid">
          {reports.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                className="report-list-item report-card"
                onClick={() => openReport(r.id)}
              >
                <div className="report-list-item-top">
                  <span className={`report-status-pill status-${r.status}`}>
                    {STATUS_LABEL_RU[r.status]}
                  </span>
                  <span className="report-list-hours">
                    {formatDuration(r.total_tracked_seconds)}
                  </span>
                </div>
                <strong className="report-list-title">{reportTitle(r)}</strong>
                <span className="report-list-sub">
                  {reportSubtitle(r)} · {formatDateTime(r.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
