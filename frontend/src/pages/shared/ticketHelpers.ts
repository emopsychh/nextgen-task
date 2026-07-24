export type TicketBucket = "open" | "closed";
export type TicketAwaitingParty = "agency" | "client";

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
export function ticketsApiQuery(
  portalId: number | null,
  bucket: TicketBucket,
  awaiting?: TicketAwaitingParty | null
): string {
  const params = new URLSearchParams({ bucket });
  if (portalId) params.set("portal", String(portalId));
  if (awaiting) params.set("awaiting", awaiting);
  return `/api/tickets/?${params.toString()}`;
}

export function ticketStatusLabel(
  status: string,
  opts?: {
    isAgency?: boolean;
    awaitingParty?: TicketAwaitingParty | null;
  }
): string {
  if (status === "closed") return "Закрыт";
  const awaiting = opts?.awaitingParty;
  const isAgency = Boolean(opts?.isAgency);
  if (awaiting === "client") {
    return isAgency ? "Ожидает клиента" : "Ожидает вашего ответа";
  }
  if (awaiting === "agency") {
    return isAgency ? "Ожидает ответа" : "Ожидает ответа поддержки";
  }
  return "Открыт";
}

/** Open the client's Bitrix24 portal in a new tab (not the in-app cabinet). */
export function bitrixPortalUrl(domain: string | null | undefined): string | null {
  const raw = (domain || "").trim();
  if (!raw) return null;
  if (/^https?:\/\//i.test(raw)) return raw;
  return `https://${raw.replace(/^\/+/, "")}`;
}
