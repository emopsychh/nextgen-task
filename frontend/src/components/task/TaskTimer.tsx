import { useEffect, useState } from "react";
import { formatDuration, formatTimerClock } from "../../lib/format";

type Props = {
  /** Sum of finished time entries (seconds), without the active run. */
  closedSeconds: number;
  activeStartedAt: string | null;
  canTrack: boolean;
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
};

export function TaskTimer({
  closedSeconds,
  activeStartedAt,
  canTrack,
  busy,
  onStart,
  onStop,
}: Props) {
  const [now, setNow] = useState(() => Date.now());
  const isRunning = Boolean(activeStartedAt);

  useEffect(() => {
    if (!isRunning) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isRunning]);

  const live = activeStartedAt
    ? Math.max(0, Math.floor((now - new Date(activeStartedAt).getTime()) / 1000))
    : 0;
  const display = closedSeconds + live;

  return (
    <div className="task-timer">
      <div className="task-timer-readout">
        <span className={`task-timer-clock${isRunning ? " is-running" : ""}`}>
          {isRunning ? formatTimerClock(display) : formatDuration(display)}
        </span>
        <span className="muted task-timer-label">
          {isRunning ? "идёт учёт" : display > 0 ? "учтено" : "время не учтено"}
        </span>
      </div>
      {canTrack && (
        <div className="task-timer-actions">
          {isRunning ? (
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={onStop}>
              Стоп
            </button>
          ) : (
            <button type="button" className="btn btn-accent" disabled={busy} onClick={onStart}>
              Старт
            </button>
          )}
        </div>
      )}
    </div>
  );
}
