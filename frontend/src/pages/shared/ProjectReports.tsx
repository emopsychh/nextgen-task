import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  isAbortError,
  unwrapList,
  type Paginated,
  type Project,
  type WorkReport,
} from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { usePortalLiveSync } from "../../hooks/usePortalLiveSync";
import { PaginationBar } from "../../components/PaginationBar";
import { formatPackageHours } from "../../lib/format";
import { LIST_PAGE_SIZE, pageTotal, withPage } from "../../lib/pagination";
import { readPortalCache, writePortalCache } from "../../lib/portalSessionCache";
import {
  type ReportBucket,
  countsFromReports,
  reportBucketsForRole,
  reportDetailPath,
  reportPackageFill,
  reportsApiQuery,
  reportSubtitle,
  reportTitle,
  STATUS_LABEL_RU,
} from "./reportHelpers";

function reportStatusTone(status: WorkReport["status"]): string {
  if (status === "accepted" || status === "paid" || status === "dismissed") return "status-done";
  if (status === "pending_client") return "status-progress";
  if (status === "disputed") return "status-overdue";
  return "status-todo";
}

export function ReportsLocked() {
  return (
    <div className="tasks-page reports-locked">
      <section className="reports-locked-card">
        <h1>Отчёты</h1>
        <p>Скоро заработает</p>
      </section>
    </div>
  );
}

const EMPTY_REPORT_COUNTS: Record<ReportBucket, number> = {
  all: 0,
  current: 0,
  review: 0,
  accepted: 0,
};
const CACHE_REPORT_COUNTS = "report-counts";

export function ProjectReports() {
  const { portalId: routePortalId, projectId: routeProjectId } = useParams();
  const { token, portal } = useAuth();
  const isAgency = portal?.role === "agency";
  const buckets = reportBucketsForRole(isAgency);

  const [resolvedPortalId, setResolvedPortalId] = useState<number | null>(null);

  const portalId = useMemo(() => {
    if (routePortalId) return Number(routePortalId);
    if (resolvedPortalId) return resolvedPortalId;
    if (!isAgency && portal?.id) return portal.id;
    return null;
  }, [routePortalId, resolvedPortalId, isAgency, portal?.id]);

  useEffect(() => {
    if (!token || !routeProjectId || routePortalId) return;
    const ac = new AbortController();
    void api<Project>(`/api/projects/${routeProjectId}/`, { signal: ac.signal }, token)
      .then((p) => setResolvedPortalId(p.portal))
      .catch((e) => {
        if (!isAbortError(e)) undefined;
      });
    return () => ac.abort();
  }, [token, routeProjectId, routePortalId]);

  const [bucket, setBucket] = useState<ReportBucket>("all");
  const [reports, setReports] = useState<WorkReport[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [listLoaded, setListLoaded] = useState(false);
  const listGenRef = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [counts, setCounts] = useState<Record<ReportBucket, number>>(
    () =>
      (portalId
        ? readPortalCache<Record<ReportBucket, number>>(
            CACHE_REPORT_COUNTS,
            portalId
          )
        : null) || EMPTY_REPORT_COUNTS
  );

  const applyCounts = useCallback((next: Record<string, number>) => {
    const accepted = next.accepted ?? next.paid ?? 0;
    const normalized: Record<ReportBucket, number> = {
      all: next.all ?? 0,
      current: next.current ?? 0,
      review: next.review ?? 0,
      accepted,
    };
    setCounts(normalized);
    if (portalId) {
      writePortalCache(CACHE_REPORT_COUNTS, portalId, normalized);
    }
  }, [portalId]);

  const loadCounts = useCallback(
    async (signal?: AbortSignal, seed?: WorkReport[]) => {
      if (!token || !portalId) return;
      // Paint badges immediately from the list we already have (tab «Все»).
      if (seed?.length) applyCounts(countsFromReports(seed));
      try {
        const next = await api<Record<ReportBucket, number>>(
          `/api/reports/counts/?portal=${portalId}`,
          { signal },
          token
        );
        if (signal?.aborted) return;
        applyCounts(next);
      } catch (e) {
        if (isAbortError(e) || signal?.aborted) return;
        // Endpoint missing/failed — count from full list.
        try {
          const data = await api<WorkReport[] | Paginated<WorkReport>>(
            reportsApiQuery(portalId, "all"),
            { signal },
            token
          );
          if (signal?.aborted) return;
          applyCounts(countsFromReports(unwrapList(data)));
        } catch (err) {
          if (!isAbortError(err) && seed?.length) applyCounts(countsFromReports(seed));
        }
      }
    },
    [token, portalId, applyCounts]
  );

  const loadList = useCallback(
    async (signal?: AbortSignal) => {
      if (!token || !portalId) return;
      const gen = ++listGenRef.current;
      const data = await api<WorkReport[] | Paginated<WorkReport>>(
        withPage(reportsApiQuery(portalId, bucket), page, LIST_PAGE_SIZE),
        { signal },
        token
      );
      if (signal?.aborted || gen !== listGenRef.current) return;
      const list = unwrapList(data);
      setReports(list);
      setTotal(pageTotal(data));
      writePortalCache(`reports:${bucket}:p${page}`, portalId, list);
      setListLoaded(true);
      void loadCounts(signal, bucket === "all" ? list : undefined);
    },
    [token, portalId, bucket, page, loadCounts]
  );

  useEffect(() => {
    if (!token || !portalId) return;
    const cached = readPortalCache<WorkReport[]>(`reports:${bucket}:p${page}`, portalId);
    setReports(cached || []);
    setListLoaded(cached !== null);
    setListLoading(true);
    setError(null);
    const ac = new AbortController();
    void loadList(ac.signal).catch((e) => {
      if (!isAbortError(e)) setError(e instanceof Error ? e.message : "Ошибка");
    }).finally(() => {
      if (!ac.signal.aborted) setListLoading(false);
    });
    return () => ac.abort();
  }, [token, portalId, bucket, page, loadList]);

  useEffect(() => {
    if (!portalId) {
      setCounts(EMPTY_REPORT_COUNTS);
      return;
    }
    const cached = readPortalCache<Record<ReportBucket, number>>(
      CACHE_REPORT_COUNTS,
      portalId
    );
    setCounts(cached || EMPTY_REPORT_COUNTS);
  }, [portalId]);

  usePortalLiveSync({
    token,
    portalId,
    enabled: !!portalId,
    onEvent: (payload) => {
      if (payload?.kind?.startsWith("report_")) {
        void loadList().catch(() => undefined);
      }
    },
  });

  if (!portalId) {
    return (
      <div className="tasks-page">
        <p className="muted">Выберите клиента, чтобы открыть отчёты.</p>
      </div>
    );
  }

  return (
    <div className="tasks-page report-hub">
      {error && <div className="error-banner">{error}</div>}

      <section className="package-reports">
        <div className="report-filter-row" role="tablist" aria-label="Статус отчётов">
          {buckets.map((b) => (
            <button
              key={b.id}
              type="button"
              className={`report-filter-chip${bucket === b.id ? " active" : ""}`}
              onClick={() => {
                if (b.id === bucket) return;
                listGenRef.current += 1;
                const cached = readPortalCache<WorkReport[]>(
                  `reports:${b.id}:p1`,
                  portalId
                );
                setReports(cached || []);
                setListLoaded(cached !== null);
                setListLoading(true);
                setPage(1);
                setBucket(b.id);
              }}
            >
              {b.label}
              <span className="report-filter-count">{counts[b.id]}</span>
            </button>
          ))}
        </div>

        {listLoading && !listLoaded ? (
          <div className="empty-linked workspace-empty data-loading-state">
            <span className="data-loading-spinner" aria-hidden />
            <p className="muted">Загружаем отчёты…</p>
          </div>
        ) : !listLoaded && error ? null : reports.length === 0 ? (
          <div className="empty-linked workspace-empty">
            <p className="muted">
              {isAgency
                ? "В этой вкладке пока нет отчётов по сделкам."
                : bucket === "review"
                  ? "Сейчас нет отчётов, которые нужно согласовать."
                  : bucket === "accepted"
                    ? "Пока нет согласованных отчётов."
                    : "Пока нет отчётов по пакетам часов."}
            </p>
          </div>
        ) : (
          <div className="task-list-group">
            {reports.map((r) => {
              const fill = reportPackageFill(r);
              const hoursLabel =
                fill.paid != null
                  ? `${formatPackageHours(fill.used)} из ${formatPackageHours(fill.paid)}`
                  : formatPackageHours(fill.used);
              return (
                <Link
                  key={r.id}
                  to={reportDetailPath(portalId, isAgency, r.id)}
                  className="board-row task-card"
                >
                  <div className="board-row-main">
                    <div className="task-compact-heading">
                      <strong className="board-row-title task-card-title">{reportTitle(r)}</strong>
                      <span className={`task-status-pill ${reportStatusTone(r.status)}`}>
                        {STATUS_LABEL_RU[r.status]}
                      </span>
                    </div>
                    <span className="board-row-note muted">
                      Сделка №{r.deal_id} · {reportSubtitle(r)}
                    </span>
                  </div>
                  <div className="task-compact-side">
                    <span className="board-meta-due">
                      <strong>{hoursLabel}</strong>
                    </span>
                    <span className="task-compact-arrow" aria-hidden>›</span>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
        <PaginationBar
          page={page}
          total={total}
          disabled={listLoading}
          onChange={setPage}
        />
      </section>
    </div>
  );
}
