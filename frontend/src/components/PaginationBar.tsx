import { LIST_PAGE_SIZE, totalPages } from "../lib/pagination";

type Props = {
  page: number;
  total: number;
  pageSize?: number;
  disabled?: boolean;
  onChange: (page: number) => void;
};

export function PaginationBar({
  page,
  total,
  pageSize = LIST_PAGE_SIZE,
  disabled,
  onChange,
}: Props) {
  const pages = totalPages(total, pageSize);
  if (total <= pageSize) return null;
  const safePage = Math.min(Math.max(1, page), pages);
  const from = (safePage - 1) * pageSize + 1;
  const to = Math.min(safePage * pageSize, total);

  return (
    <nav className="pagination-bar" aria-label="Страницы списка">
      <p className="pagination-bar-range muted">
        {from}–{to} из {total}
      </p>
      <div className="pagination-bar-pages">
        <button
          type="button"
          className="btn btn-ghost"
          disabled={disabled || safePage <= 1}
          onClick={() => onChange(safePage - 1)}
        >
          Назад
        </button>
        <span className="pagination-bar-status">
          {safePage} из {pages}
        </span>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={disabled || safePage >= pages}
          onClick={() => onChange(safePage + 1)}
        >
          Вперёд
        </button>
      </div>
    </nav>
  );
}
