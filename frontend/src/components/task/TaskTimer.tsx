import { useEffect, useState, type FormEvent } from "react";
import { formatDuration } from "../../lib/format";

type Props = {
  totalSeconds: number;
  canAdd: boolean;
  busy?: boolean;
  onAddTime: (hours: number, minutes: number) => Promise<void> | void;
};

export function TaskTimer({ totalSeconds, canAdd, busy, onAddTime }: Props) {
  const [open, setOpen] = useState(false);
  const [hours, setHours] = useState("0");
  const [minutes, setMinutes] = useState("30");
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setHours("0");
      setMinutes("30");
      setLocalError(null);
    }
  }, [open]);

  const clock = formatDuration(totalSeconds);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const h = Math.max(0, Math.floor(Number(hours) || 0));
    const m = Math.max(0, Math.floor(Number(minutes) || 0));
    if (!Number.isFinite(h) || !Number.isFinite(m)) {
      setLocalError("Введите числа");
      return;
    }
    if (m >= 60) {
      setLocalError("Минуты — от 0 до 59");
      return;
    }
    if (h * 60 + m <= 0) {
      setLocalError("Укажите время больше нуля");
      return;
    }
    setLocalError(null);
    await onAddTime(h, m);
    setOpen(false);
  }

  return (
    <div className="task-timer-scale" title={`Затрачено на задачу: ${clock}`}>
      <div className="task-timer-scale-line">
        <span className="task-timer-scale-clock">{clock}</span>
        {canAdd ? (
          <button
            type="button"
            className="task-timer-add-btn"
            disabled={busy}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Отмена" : "Указать время"}
          </button>
        ) : null}
      </div>

      {open ? (
        <form className="task-time-form" onSubmit={(e) => void submit(e)}>
          <div className="task-time-form-row">
            <label className="task-time-field">
              <span>Часы</span>
              <input
                type="number"
                min={0}
                max={168}
                step={1}
                inputMode="numeric"
                value={hours}
                disabled={busy}
                onChange={(e) => setHours(e.target.value)}
                aria-label="Часы"
              />
            </label>
            <label className="task-time-field">
              <span>Минуты</span>
              <input
                type="number"
                min={0}
                max={59}
                step={1}
                inputMode="numeric"
                value={minutes}
                disabled={busy}
                onChange={(e) => setMinutes(e.target.value)}
                aria-label="Минуты"
              />
            </label>
            <button type="submit" className="btn btn-accent task-time-submit" disabled={busy}>
              Добавить
            </button>
          </div>
          {localError ? <p className="task-time-form-error">{localError}</p> : null}
        </form>
      ) : null}
    </div>
  );
}
