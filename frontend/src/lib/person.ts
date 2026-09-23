const AVATAR_COLORS = ["#171717", "#555552", "#d68d48", "#343432", "#8b6546", "#b97837"];

export function personInitials(name: string | null | undefined): string {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] || ""}${parts[parts.length - 1][0] || ""}`.toUpperCase();
}

export function personHue(name: string | null | undefined): string {
  const raw = (name || "?").trim();
  let hash = 0;
  for (let i = 0; i < raw.length; i += 1) {
    hash = (hash * 31 + raw.charCodeAt(i)) >>> 0;
  }
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}
