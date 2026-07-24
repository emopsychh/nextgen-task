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
  const live = Number.isFinite(startedMs)
    ? Math.max(0, Math.floor((now - startedMs) / 1000))
    : 0;
  const displaySec = Math.max(0, closedSeconds + live);
  const clock = isRunning ? formatTimerClock(displaySec) : formatDuration(displaySec);

  const paid = asPackageHours(paidHours);
  const remaining = asPackageHours(remainingHours);
  const hasPackage = paid != null && paid > 0;
  const usedHours =
    hasPackage && remaining != null ? Math.max(0, paid - remaining) : null;
  const pct =
    hasPackage && usedHours != null ? Math.min(100, (usedHours / paid) * 100) : 0;
  const overBudget = hasPackage && remaining != null && remaining <= 0;

  const ariaLabel = hasPackage
    ? `По задаче ${clock}, пакет ${usedHours != null ? formatPackageHours(usedHours) : "—"} из ${formatPackageHours(paid)}`
    : `По задаче ${clock}`;

  return (
    <div
      className={`task-timer-scale${isRunning ? " is-running" : ""}${overBudget ? " is-over" : ""}`}
      data-working={isWorking || undefined}
      title={ariaLabel}
    >
      <div className="task-timer-scale-line">
        <span className="task-timer-scale-clock">{clock}</span>
        {hasPackage ? (
          <span className="task-timer-scale-meta">
            {usedHours != null ? formatPackageHours(usedHours) : "—"}
            <span className="task-timer-scale-sep">/</span>
            {formatPackageHours(paid)}
            {remaining != null ? (
              <span className="task-timer-scale-remain"> · ост. {formatPackageHours(remaining)}</span>
            ) : null}
          </span>
        ) : displaySec === 0 && !isRunning ? (
          <span className="task-timer-scale-meta is-muted">не учитывалось</span>
        ) : isRunning ? (
          <span className="task-timer-scale-meta is-live">идёт учёт</span>
        ) : null}
      </div>

      <div
        className={`task-timer-track${!hasPackage ? " is-empty" : ""}`}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={hasPackage ? Math.round(pct) : displaySec > 0 ? 8 : 0}
        aria-label={hasPackage ? "Использование пакета часов" : "Учёт времени по задаче"}
      >
        <div
          className={`task-timer-fill${!hasPackage ? " is-soft" : ""}`}
          style={{
            width: hasPackage
              ? `${Math.max(pct, usedHours && usedHours > 0 ? 1.5 : 0)}%`
              : displaySec > 0 || isRunning
                ? "12%"
                : "0%",
          }}
        />
      </div>
    </div>
  );
}
