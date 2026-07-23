import { useEffect, useState } from "react";

type Props = {
  open: boolean;
  taskTitle: string;
  initialOutcome?: string;
  busy?: boolean;
  onConfirm: (outcome: string) => void;
  onCancel: () => void;
};

export function TaskCompleteModal({
  open,
  taskTitle,
  initialOutcome = "",
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  const [outcome, setOutcome] = useState(initialOutcome);

  useEffect(() => {
    if (open) setOutcome(initialOutcome);
  }, [open, initialOutcome]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  const trimmed = outcome.trim();

  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onCancel()}>
      <div
        className="modal-card modal-card-wide complete-outcome-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="complete-outcome-title"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="complete-outcome-kicker">Завершение задачи</p>
        <h3 id="complete-outcome-title" className="modal-title">
          Что сделано?
        </h3>
        <p className="modal-desc">
          Краткий итог по «{taskTitle}». Он сохранится в задаче и попадёт в отчёт.
        </p>
        <div className="field">
          <label htmlFor="complete-outcome-text">Итог</label>
          <textarea
            id="complete-outcome-text"
            rows={5}
            value={outcome}
            onChange={(e) => setOutcome(e.target.value)}
            placeholder="Например: настроили оплату, проверили на стенде, отдали клиенту…"
            autoFocus
            disabled={busy}
          />
        </div>
        <div className="modal-actions">
          <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            Отмена
          </button>
          <button
            type="button"
            className="btn btn-accent"
            disabled={busy || !trimmed}
            onClick={() => onConfirm(trimmed)}
          >
            {busy ? "Сохраняем…" : "Завершить"}
          </button>
        </div>
      </div>
    </div>
  );
}
