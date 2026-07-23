import { useEffect, useState } from "react";
import {
  asPackageHours,
  formatDuration,
  formatPackageHours,
  formatTimerClock,
} from "../../lib/format";

type Props = {
  /** Sum of finished time entries (seconds), without the active run. */
  closedSeconds: number;
  activeStartedAt: string | null;
  isWorking: boolean;
  /** Paid hours on the accompaniment deal (package size). */
  paidHours?: number | null;
  /** Remaining hours on the deal. */
  remainingHours?: number | null;
};

export function TaskTimer({
  closedSeconds,
  activeStartedAt,
  isWorking,
  paidHours,
  remainingHours,
}: Props) {
  const [now, setNow] = useState(() => Date.now());
  const isRunning = Boolean(activeStartedAt);

  useEffect(() => {
    if (!isRunning) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isRunning]);

  const startedMs = activeStartedAt ? new Date(activeStartedAt).getTime() : NaN;
  const live =
    Number.isFinite(startedMs)
      ? Math.max(0, Math.floor((now - startedMs) / 1000))
      : 0;
  const displaySec = Math.max(0, closedSeconds + live);
  const clock = isRunning ? formatTimerClock(displaySec) : formatDuration(displaySec);

  const paid = asPackageHours(paidHours);
  const remaining = asPackageHours(remainingHours);
  const hasPackage = paid != null && paid > 0;
  // Package consumption across all billed work (not this task alone)
  const usedHours =
    hasPackage && remaining != null ? Math.max(0, paid - remaining) : null;
  const pct =
    hasPackage && usedHours != null ? Math.min(100, (usedHours / paid) * 100) : 0;
  const overBudget = hasPackage && remaining != null && remaining <= 0;

  let status = "ещё не учитывалось";
  if (isRunning) status = "идёт учёт";
  else if (displaySec > 0) status = "по этой задаче";

  return (
    <div
      className={`task-timer-scale${isRunning ? " is-running" : ""}${overBudget ? " is-over" : ""}`}
      data-working={isWorking || undefined}
    >
      <div className="task-timer-scale-head">
        <div className="task-timer-scale-main">
          <span className="task-timer-scale-clock">{clock}</span>
          <span className="task-timer-scale-status">{status}</span>
        </div>
        {hasPackage ? (
          <div className="task-timer-scale-meta">
            <span>
              пакет: {usedHours != null ? formatPackageHours(usedHours) : "—"} /{" "}
              {formatPackageHours(paid)}
            </span>
            {remaining != null ? (
              <span className="task-timer-scale-remain">
                остаток {formatPackageHours(remaining)}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>

      {hasPackage ? (
        <div
          className="task-timer-track"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct)}
          aria-label="Использование оплаченного пакета часов"
        >
          <div
            className="task-timer-fill"
            style={{ width: `${Math.max(pct, usedHours && usedHours > 0 ? 1.5 : 0)}%` }}
          />
        </div>
      ) : (
        <div className="task-timer-track is-empty" aria-hidden>
          <div className="task-timer-fill is-soft" style={{ width: displaySec > 0 ? "8%" : "0%" }} />
        </div>
      )}
    </div>
  );
}
