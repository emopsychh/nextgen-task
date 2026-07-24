export type TicketBucket = "open" | "closed";

export const TICKET_BUCKETS: { id: TicketBucket; label: string }[] = [
  { id: "open", label: "Актуальные" },
  { id: "closed", label: "Архив" },
];

export function ticketsListPath(portalId: number | null, isAgency: boolean): string {
  if (isAgency && portalId) return `/portals/${portalId}/tickets`;
  return "/tickets";
}

export function ticketDetailPath(
  portalId: number | null,
  isAgency: boolean,
  ticketId: number
): string {
  if (isAgency && portalId) return `/portals/${portalId}/tickets/${ticketId}`;
  return `/tickets/${ticketId}`;
}

export function ticketsApiQuery(portalId: number, bucket: TicketBucket): string {
  return `/api/tickets/?portal=${portalId}&bucket=${bucket}`;
}

export function ticketStatusLabel(status: string): string {
  if (status === "closed") return "Закрыт";
  return "Открыт";
}
