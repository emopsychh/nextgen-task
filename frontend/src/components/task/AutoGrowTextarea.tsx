import {
  useEffect,
  useRef,
  type TextareaHTMLAttributes,
} from "react";

type Props = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "rows"> & {
  /** Min visible lines (approx). */
  minRows?: number;
  /** Max height in px before internal scroll. */
  maxHeight?: number;
};

/** Textarea that grows with content — no manual resize handle. */
export function AutoGrowTextarea({
  value,
  minRows = 2,
  maxHeight = 220,
  className,
  onChange,
  ...rest
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const line = 1.4; // matches CSS line-height
  const minPx = Math.round(16 * line * minRows + 16); // + padding approx

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    const next = Math.min(Math.max(el.scrollHeight, minPx), maxHeight);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > maxHeight ? "auto" : "hidden";
  }, [value, minPx, maxHeight]);

  return (
    <textarea
      {...rest}
      ref={ref}
      className={`auto-grow-textarea${className ? ` ${className}` : ""}`}
      value={value}
      rows={minRows}
      onChange={onChange}
    />
  );
}
