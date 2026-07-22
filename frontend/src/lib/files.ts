/** File / image helpers for chat attachments. */

const IMAGE_EXT = new Set([
  "jpg",
  "jpeg",
  "png",
  "gif",
  "webp",
  "bmp",
  "svg",
  "heic",
  "heif",
  "avif",
]);

/** Last path segment of a name or URL (strips query/hash). */
export function fileBasename(nameOrUrl: string | null | undefined): string {
  const raw = (nameOrUrl || "").trim();
  if (!raw) return "";
  try {
    const u = new URL(raw, "https://local.invalid");
    const parts = u.pathname.split("/").filter(Boolean);
    return decodeURIComponent(parts[parts.length - 1] || "");
  } catch {
    const clean = raw.split("?")[0].split("#")[0];
    const parts = clean.split("/").filter(Boolean);
    return parts[parts.length - 1] || clean;
  }
}

export function fileExtension(name: string | null | undefined): string {
  const base = fileBasename(name);
  const i = base.lastIndexOf(".");
  if (i < 0) return "";
  return base.slice(i + 1).toLowerCase();
}

export function isImageName(name: string | null | undefined): boolean {
  return IMAGE_EXT.has(fileExtension(name));
}

export function isImageFile(file: { name?: string; type?: string } | null | undefined): boolean {
  if (!file) return false;
  if (file.type && file.type.startsWith("image/")) return true;
  return isImageName(file.name);
}

/** Prefer original filename; URL only as fallback (avoids false positives on domain TLDs). */
export function isImageAttachment(originalName?: string | null, url?: string | null): boolean {
  if (originalName && isImageName(originalName)) return true;
  if (url && isImageName(url)) return true;
  return false;
}
