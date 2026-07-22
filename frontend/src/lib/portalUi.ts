/** Shared display helpers for people / portal labels */

export function initialsFromLabel(raw: string): string {
  const parts = raw.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (parts[0] || "?").slice(0, 2).toUpperCase();
}

const AVATAR_HUES = [168, 200, 32, 280, 12, 145, 220];

export function hueFromId(id: number): string {
  return `hsl(${AVATAR_HUES[id % AVATAR_HUES.length]} 48% 46%)`;
}
