import { useEffect, useState } from "react";
import { formatDuration, formatTimerClock } from "../../lib/format";

type Props = {
  /** Sum of finished time entries (seconds), without the active run. */
  closedSeconds: number;
  activeStartedAt: string | null;
  isWorking: boolean;
};

export function TaskTimer({ closedSeconds, activeStartedAt, isWorking }: Props) {
  const [now, setNow] = useState(() => Date.now());
  const isRunning = Boolean(activeStartedAt);

  useEffect(() => {
    if (!isRunning) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isRunning]);

  const startedMs = activeStartedAt ? new Date(activeStartedAt).getTime() : NaN;
  const live = Number.isFinite(startedMs)
    ? Math.max(0, Math.floor((now - startedMs) / 1000))
    : 0;
  const displaySec = Math.max(0, closedSeconds + live);
  const clock = isRunning ? formatTimerClock(displaySec) : formatDuration(displaySec);

  // Soft fill for visual scale — capped so long tasks don't look "full".
  const softPct =
    displaySec <= 0 && !isRunning
      ? 0
      : Math.min(70, Math.max(8, Math.round((displaySec / 3600) * 12)));

  return (
    <div
      className={`task-timer-scale${isRunning ? " is-running" : ""}`}
      data-working={isWorking || undefined}
      title={`Затрачено на задачу: ${clock}`}
    >
      <div className="task-timer-scale-line">
        <span className="task-timer-scale-clock">{clock}</span>
        {isRunning ? <span className="task-timer-scale-meta is-live">идёт учёт</span> : null}
      </div>

      <div
        className={`task-timer-track${displaySec === 0 && !isRunning ? " is-empty" : ""}`}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={softPct}
        aria-label="Время по задаче"
      >
        <div className="task-timer-fill" style={{ width: `${softPct}%` }} />
      </div>
    </div>
  );
}
