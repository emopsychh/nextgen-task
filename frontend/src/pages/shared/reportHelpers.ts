import type { WorkReport, WorkReportStatus } from "../../api/types";
import { asPackageHours } from "../../lib/format";

/** Report fill comes from closed tasks in the report, not CRM remaining hours. */
export function reportPackageFill(
  report: Pick<
    WorkReport,
    "deal_hours" | "total_tracked_seconds" | "carried_overage_seconds"
  >
) {
  const paid = asPackageHours(report.deal_hours?.paid_hours);
  const used = (report.total_tracked_seconds || 0) / 3600;
  const carried = (report.carried_overage_seconds || 0) / 3600;
  const leftover = paid != null ? Math.max(0, paid - used) : null;
  const overage = paid != null ? Math.max(0, used - paid) : 0;
  const usedPct = paid && paid > 0 ? Math.min(100, (used / paid) * 100) : null;
  const isFull = leftover != null && leftover <= 1 / 60;
  return { paid, used, leftover, overage, carried, usedPct, isFull };
}

export type ReportBucket = "all" | "current" | "review" | "accepted";

export const STATUS_LABEL_RU: Record<WorkReportStatus, string> = {
  draft: "Черновик",
  pending_client: "На согласовании",
  disputed: "Замечания",
  accepted: "Согласован",
  paid: "Согласован",
  dismissed: "Снято",
};

export const EVENT_LABEL: Record<string, string> = {
  created: "Создан",
  sent: "Отправлен клиенту",
  accepted: "Клиент согласился",
  disputed: "Клиент связался с менеджером",
  paid: "Отмечен согласованным",
  reopened: "Вернут на рассмотрение руководителя",
  dismissed: "Снято с контроля",
};

export const REPORT_BUCKETS: { id: ReportBucket; label: string }[] = [
  { id: "all", label: "Все" },
  { id: "current", label: "Актуальные" },
  { id: "review", label: "У клиента" },
  { id: "accepted", label: "Согласованные" },
];

export const CLIENT_REPORT_BUCKETS: { id: ReportBucket; label: string }[] = [
  { id: "all", label: "Все" },
  { id: "review", label: "На согласовании" },
  { id: "accepted", label: "Согласованные" },
];

export function reportBucketsForRole(isAgency: boolean): { id: ReportBucket; label: string }[] {
  return isAgency ? REPORT_BUCKETS : CLIENT_REPORT_BUCKETS;
}

/** Mirrors backend board.reports.BUCKET_STATUSES for client-side badge fallback. */
export const BUCKET_STATUSES: Record<Exclude<ReportBucket, "all">, WorkReportStatus[]> = {
  current: ["draft", "disputed"],
  review: ["pending_client"],
  accepted: ["accepted", "paid"],
};

export function countsFromReports(
  reports: Pick<WorkReport, "status">[]
): Record<ReportBucket, number> {
  const next: Record<ReportBucket, number> = {
    all: reports.length,
    current: 0,
    review: 0,
    accepted: 0,
  };
  for (const r of reports) {
    if (BUCKET_STATUSES.current.includes(r.status)) next.current += 1;
    else if (BUCKET_STATUSES.review.includes(r.status)) next.review += 1;
    else if (BUCKET_STATUSES.accepted.includes(r.status)) next.accepted += 1;
  }
  return next;
}

export function reportTitle(
  r: Pick<WorkReport, "id" | "deal_title" | "deal_id">
): string {
  return r.deal_title?.trim() || `Сделка №${r.deal_id || r.id}`;
}

export function reportSheetTitle(
  r: Pick<WorkReport, "id" | "deal_title" | "deal_id">
): string {
  const title = reportTitle(r);
  return `${title} · сделка №${r.deal_id || r.id}`;
}

export function reportSubtitle(
  r: Pick<WorkReport, "tasks_count">
): string {
  const n = r.tasks_count || 0;
  if (n % 10 === 1 && n % 100 !== 11) return `${n} задача`;
  if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14)) {
    return `${n} задачи`;
  }
  return `${n} задач`;
}

export function reportsListPath(portalId: number | null, isAgency: boolean): string {
  if (isAgency && portalId) return `/portals/${portalId}/reports`;
  return "/reports";
}

export function reportDetailPath(
  portalId: number | null,
  isAgency: boolean,
  reportId: number
): string {
  if (isAgency && portalId) return `/portals/${portalId}/reports/${reportId}`;
  return `/reports/${reportId}`;
}

export function reportsApiQuery(portalId: number, bucket: ReportBucket): string {
  const base = `/api/reports/?portal=${portalId}`;
  if (bucket === "all") return base;
  return `${base}&bucket=${bucket}`;
}
