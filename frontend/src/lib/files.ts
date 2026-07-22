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

export function fileExtension(name: string | null | undefined): string {
  const raw = (name || "").trim();
  const i = raw.lastIndexOf(".");
  if (i < 0) return "";
  return raw.slice(i + 1).toLowerCase();
}

export function isImageName(name: string | null | undefined): boolean {
  return IMAGE_EXT.has(fileExtension(name));
}

export function isImageFile(file: { name?: string; type?: string } | null | undefined): boolean {
  if (!file) return false;
  if (file.type && file.type.startsWith("image/")) return true;
  return isImageName(file.name);
}
