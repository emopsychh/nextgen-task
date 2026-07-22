import { useEffect, useState } from "react";
import { formatDuration, formatTimerClock } from "../../lib/format";

type Props = {
  /** Sum of finished time entries (seconds), without the active run. */
  closedSeconds: number;
  activeStartedAt: string | null;
  /** Task is in progress — shows live “recording” state. */
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

  const live = activeStartedAt
    ? Math.max(0, Math.floor((now - new Date(activeStartedAt).getTime()) / 1000))
    : 0;
  const display = closedSeconds + live;

  let label = "ещё не учитывалось";
  if (isRunning) label = "сейчас идёт учёт";
  else if (display > 0) label = "всего затрачено";

  return (
    <div className={`task-timer${isRunning ? " is-running" : ""}`} data-working={isWorking || undefined}>
      <span className="task-timer-dot" aria-hidden />
      <div className="task-timer-readout">
        <span className="task-timer-clock">
          {isRunning ? formatTimerClock(display) : formatDuration(display)}
        </span>
        <span className="task-timer-label">{label}</span>
      </div>
    </div>
  );
}
