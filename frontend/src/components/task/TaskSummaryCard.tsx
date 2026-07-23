import { forwardRef } from "react";
import type { Task, TaskStatus } from "../../api/types";
import { DueDatePicker } from "../DueDatePicker";
import { TaskGlyph } from "../icons";
import { formatClock, formatDateTime, formatDueFull } from "../../lib/format";
import { STATUS_LABEL, STATUS_TONE } from "../../lib/status";
import type { DueTone } from "../../lib/dates";
import { TaskTimer } from "./TaskTimer";

type Props = {
  task: Task;
  creator: string;
  overdue: boolean;
  due: { label: string; tone: DueTone; detail?: string };
  canManage: boolean;
  canChangeStatus: boolean;
  canEditDueDate: boolean;
  saveBusy: boolean;
  draftTitle: string;
  draftDescription: string;
  onDraftTitle: (value: string) => void;
  onDraftDescription: (value: string) => void;
  onCommitTitle: () => void;
  onCommitDescription: () => void;
  onSetStatus: (status: TaskStatus) => void;
  onSetDueDate: (iso: string) => void;
};

export const TaskSummaryCard = forwardRef<HTMLElement, Props>(function TaskSummaryCard(
  {
    task,
    creator,
    overdue,
    due,
    canManage,
    canChangeStatus,
    canEditDueDate,
    saveBusy,
    draftTitle,
    draftDescription,
    onDraftTitle,
    onDraftDescription,
    onCommitTitle,
    onCommitDescription,
    onSetStatus,
    onSetDueDate,
  },
  ref
) {
  return (
    <article
      className={`task-summary-card${canManage ? " is-editable" : ""}${task.status === "done" ? " is-done" : ""}`}
      ref={ref}
    >
      <div className="task-summary-intro">
        <span className="user-mark">{creator}</span>
        <span className="task-summary-intro-rest"> создал задачу</span>
      </div>

      <div className="task-summary-title-row">
        <span className="task-summary-icon" aria-hidden>
          <TaskGlyph />
        </span>
        {canManage ? (
          <input
            className="task-summary-title-input"
            value={draftTitle}
            onChange={(e) => onDraftTitle(e.target.value)}
            onBlur={() => onCommitTitle()}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                (e.target as HTMLInputElement).blur();
              }
            }}
            disabled={saveBusy}
            aria-label="Название задачи"
          />
        ) : (
          <h1 className="task-summary-title">{task.title}</h1>
        )}
      </div>

      {canManage ? (
        <textarea
          className={`task-summary-desc-input${!draftDescription.trim() ? " is-empty" : ""}`}
          value={draftDescription}
          onChange={(e) => onDraftDescription(e.target.value)}
          onBlur={() => onCommitDescription()}
          rows={3}
          placeholder="Добавить описание…"
          disabled={saveBusy}
          aria-label="Описание задачи"
        />
      ) : task.description?.trim() ? (
        <p className="task-summary-desc">{task.description}</p>
      ) : (
        <p className="task-summary-desc is-empty">Описание пока не добавлено</p>
      )}

      <div className="task-summary-status-block">
        <div className="task-status-group">
          <span className={`task-status-pill ${STATUS_TONE[task.status]}`}>
            {STATUS_LABEL[task.status]}
          </span>
          {overdue ? <span className="task-status-pill status-overdue">Опаздывает</span> : null}
        </div>
        {canChangeStatus && (
          <div
            className="task-status-actions"
            role="group"
            aria-label="Действия со статусом"
            data-tour="tour-status-actions"
          >
            {task.status === "todo" && (
              <button
                type="button"
                className="btn btn-accent"
                disabled={saveBusy}
                onClick={() => onSetStatus("in_progress")}
              >
                Начать
              </button>
            )}
            {task.status === "in_progress" && (
              <>
                <button
                  type="button"
                  className="btn btn-accent"
                  disabled={saveBusy}
                  onClick={() => onSetStatus("done")}
                >
                  Завершить
                </button>
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={saveBusy}
                  onClick={() => onSetStatus("todo")}
                >
                  Пауза
                </button>
              </>
            )}
            {task.status === "done" && (
              <button
                type="button"
                className="btn btn-ghost"
                disabled={saveBusy}
                onClick={() => onSetStatus("todo")}
              >
                Возобновить
              </button>
            )}
          </div>
        )}
      </div>

      <div className="task-summary-timer-block">
        <TaskTimer
          closedSeconds={task.total_tracked_seconds || 0}
          activeStartedAt={task.active_timer?.started_at || null}
          isWorking={task.status === "in_progress"}
          paidHours={task.deal_paid_hours}
          remainingHours={task.deal_remaining_hours}
        />
      </div>

      <dl className="task-summary-fields">
        <div className="task-summary-row">
          <dt>Проект</dt>
          <dd>{task.project_name}</dd>
        </div>
        <div className="task-summary-row">
          <dt>Постановщик</dt>
          <dd>
            <span className="user-mark">{creator}</span>
          </dd>
        </div>
        <div className="task-summary-row task-summary-row-due">
          <dt>Крайний срок</dt>
          <dd>
            {canEditDueDate ? (
              <DueDatePicker
                value={task.due_date || ""}
                onChange={onSetDueDate}
                status={task.status}
                variant="inline"
              />
            ) : (
              <span className={`task-due-inline ${due.tone}`}>
                {formatDueFull(task.due_date)}
                {task.due_date ? ` · ${due.label}` : ""}
              </span>
            )}
          </dd>
        </div>
        <div className="task-summary-row">
          <dt>Поставлена</dt>
          <dd>{formatDateTime(task.created_at)}</dd>
        </div>
      </dl>

      <time className="task-summary-time">{formatClock(task.created_at)}</time>
    </article>
  );
});
