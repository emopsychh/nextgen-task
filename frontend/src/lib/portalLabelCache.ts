/** Lightweight in-memory portal display labels for instant Overview paint. */

export const PORTAL_LABEL_EVENT = "portal-label-updated";

const labels = new Map<number, string>();

export function setPortalLabel(portalId: number, label: string): void {
  const trimmed = (label || "").trim();
  if (!portalId || !trimmed) return;
  if (labels.get(portalId) === trimmed) return;
  labels.set(portalId, trimmed);
  window.dispatchEvent(
    new CustomEvent(PORTAL_LABEL_EVENT, { detail: { portalId, label: trimmed } })
  );
}

export function getPortalLabel(portalId: number | null | undefined): string {
  if (!portalId) return "";
  return labels.get(portalId) || "";
}

export function portalDisplayName(portal: {
  id?: number;
  name?: string;
  domain?: string;
} | null | undefined): string {
  if (!portal) return "";
  return (portal.name || portal.domain || "").trim();
}
