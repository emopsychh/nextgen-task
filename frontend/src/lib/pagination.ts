import type { Paginated } from "../api/types";

export const LIST_PAGE_SIZE = 20;
export const PICKER_PAGE_SIZE = 50;

export function withPage(
  path: string,
  page: number,
  pageSize: number = LIST_PAGE_SIZE
): string {
  const hashIndex = path.indexOf("#");
  const withoutHash = hashIndex >= 0 ? path.slice(0, hashIndex) : path;
  const qIndex = withoutHash.indexOf("?");
  const pathname = qIndex >= 0 ? withoutHash.slice(0, qIndex) : withoutHash;
  const search = qIndex >= 0 ? withoutHash.slice(qIndex + 1) : "";
  const params = new URLSearchParams(search);
  params.set("page", String(Math.max(1, page)));
  params.set("page_size", String(pageSize));
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
}

export function pageTotal(data: Paginated<unknown> | unknown[] | null | undefined): number {
  if (!data) return 0;
  if (Array.isArray(data)) return data.length;
  return Number(data.count) || (data.results || []).length;
}

export function totalPages(total: number, pageSize: number = LIST_PAGE_SIZE): number {
  if (total <= 0) return 1;
  return Math.max(1, Math.ceil(total / pageSize));
}
