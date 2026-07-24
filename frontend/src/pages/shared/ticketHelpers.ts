export type TicketBucket = "open" | "closed";

export const TICKET_BUCKETS: { id: TicketBucket; label: string }[] = [
  { id: "open", label: "Актуальные" },
  { id: "closed", label: "Архив" },
];

/** Agency always uses the global hub; client stays on /tickets. */
export function ticketsListPath(_portalId: number | null, isAgency: boolean): string {
  void isAgency;
  return "/tickets";
}

export function ticketDetailPath(
  _portalId: number | null,
  _isAgency: boolean,
  ticketId: number
): string {
  return `/tickets/${ticketId}`;
}

/** Omit portal for agency-wide list; pass portal for client (or filtered) views. */
export function ticketsApiQuery(portalId: number | null, bucket: TicketBucket): string {
  const params = new URLSearchParams({ bucket });
  if (portalId) params.set("portal", String(portalId));
  return `/api/tickets/?${params.toString()}`;
}

export function ticketStatusLabel(status: string): string {
  if (status === "closed") return "Закрыт";
  return "Открыт";
}
