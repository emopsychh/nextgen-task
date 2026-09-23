import { useEffect, useRef, useState } from "react";

type FilterOption<T extends string> = {
  id: T;
  label: string;
  count: number;
  tone?: string;
};

type Props<T extends string> = {
  label: string;
  value: T;
  options: FilterOption<T>[];
  onChange: (value: T) => void;
};

export function StatusFilterMenu<T extends string>({ label, value, options, onChange }: Props<T>) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const active = options.find((option) => option.id === value) || options[0];

  useEffect(() => {
    if (!open) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className="status-filter-menu" ref={rootRef}>
      <button
        type="button"
        className={`status-filter-trigger${open ? " is-open" : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <svg className="status-filter-icon" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path d="M4 7h16M7 12h10M10 17h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
        <span>{active.label}</span>
        <strong>{active.count}</strong>
        <svg className="status-filter-chevron" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path d="m8 10 4 4 4-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open ? (
        <div className="status-filter-popover" role="menu" aria-label={label}>
          <span className="status-filter-popover-label">{label}</span>
          {options.map((option) => (
            <button
              key={option.id}
              type="button"
              role="menuitemradio"
              aria-checked={option.id === value}
              className={`status-filter-option${option.id === value ? " is-active" : ""}${option.tone ? ` ${option.tone}` : ""}`}
              onClick={() => {
                onChange(option.id);
                setOpen(false);
              }}
            >
              <span className="status-filter-option-dot" aria-hidden />
              <span>{option.label}</span>
              <strong>{option.count}</strong>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
