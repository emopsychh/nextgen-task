import type { WorkReport, WorkReportStatus } from "../../api/types";

export type ReportBucket = "all" | "current" | "review" | "paid";

export const STATUS_LABEL_RU: Record<WorkReportStatus, string> = {
  draft: "На рассмотрении руководителя",
  pending_client: "Требует рассмотрения",
  disputed: "Связь с менеджером",
  accepted: "Согласован",
  paid: "Оплачен",
};

export const EVENT_LABEL: Record<string, string> = {
  created: "Создан",
  sent: "Отправлен клиенту",
  accepted: "Клиент согласился",
  disputed: "Клиент связался с менеджером",
  paid: "Отмечен оплаченным",
  reopened: "Вернут на рассмотрение руководителя",
};

export const REPORT_BUCKETS: { id: ReportBucket; label: string }[] = [
  { id: "all", label: "Все" },
  { id: "current", label: "Актуальные" },
  { id: "review", label: "У клиента" },
  { id: "paid", label: "Оплаченные" },
];

/** Mirrors backend board.reports.BUCKET_STATUSES for client-side badge fallback. */
export const BUCKET_STATUSES: Record<Exclude<ReportBucket, "all">, WorkReportStatus[]> = {
  current: ["draft", "disputed", "accepted"],
  review: ["pending_client"],
  paid: ["paid"],
};

export function countsFromReports(
  reports: Pick<WorkReport, "status">[]
): Record<ReportBucket, number> {
  const next: Record<ReportBucket, number> = {
    all: reports.length,
    current: 0,
    review: 0,
    paid: 0,
  };
  for (const r of reports) {
    if (BUCKET_STATUSES.current.includes(r.status)) next.current += 1;
    else if (BUCKET_STATUSES.review.includes(r.status)) next.review += 1;
    else if (BUCKET_STATUSES.paid.includes(r.status)) next.paid += 1;
  }
  return next;
}

export function reportTitle(
  r: Pick<WorkReport, "id" | "project_names" | "projects_count">
): string {
  const names = r.project_names || [];
  if (names.length === 0) return `Отчёт №${r.id}`;
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} и ${names[1]}`;
  return `${names[0]} и ещё ${names.length - 1}`;
}

export function reportSubtitle(
  r: Pick<WorkReport, "project_names" | "projects_count">
): string {
  const n = r.projects_count || r.project_names?.length || 0;
  if (n <= 1) return "1 проект";
  if (n >= 2 && n <= 4) return `${n} проекта`;
  return `${n} проектов`;
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
