import { useEffect, useState, type FormEvent } from "react";
import { formatDuration } from "../../lib/format";

type Props = {
  totalSeconds: number;
  canEdit: boolean;
  busy?: boolean;
  onSetTime: (hours: number, minutes: number) => Promise<void> | void;
};

function splitSeconds(totalSeconds: number): { hours: string; minutes: string } {
  const s = Math.max(0, Math.floor(totalSeconds || 0));
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  return { hours: String(hours), minutes: String(minutes) };
}

export function TaskTimer({ totalSeconds, canEdit, busy, onSetTime }: Props) {
  const [open, setOpen] = useState(false);
  const initial = splitSeconds(totalSeconds);
  const [hours, setHours] = useState(initial.hours);
  const [minutes, setMinutes] = useState(initial.minutes);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      const next = splitSeconds(totalSeconds);
      setHours(next.hours);
      setMinutes(next.minutes);
      setLocalError(null);
    }
  }, [open, totalSeconds]);

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
    setLocalError(null);
    await onSetTime(h, m);
    setOpen(false);
  }

  return (
    <div className="task-timer-scale" title={`Затрачено на задачу: ${clock}`}>
      <div className="task-timer-scale-line">
        <span className="task-timer-scale-clock">{clock}</span>
        {canEdit ? (
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
              Сохранить
            </button>
          </div>
          {localError ? <p className="task-time-form-error">{localError}</p> : null}
        </form>
      ) : null}
    </div>
  );
}
