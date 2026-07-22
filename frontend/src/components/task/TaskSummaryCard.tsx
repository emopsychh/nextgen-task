import { forwardRef } from "react";
import type { Task, TaskStatus } from "../../api/types";
import { DueDatePicker } from "../DueDatePicker";
import { TaskGlyph } from "../icons";
import { formatClock, formatDateTime, formatDueFull } from "../../lib/format";
import { STATUS_LABEL, STATUS_TONE, SYNC_LABEL } from "../../lib/status";
import type { DueTone } from "../../lib/dates";
import { TaskTimer } from "./TaskTimer";

type Props = {
  task: Task;
  creator: string;
  overdue: boolean;
  due: { label: string; tone: DueTone; detail?: string };
  canManage: boolean;
  canChangeStatus: boolean;
  canTrackTime: boolean;
  saveBusy: boolean;
  timerBusy: boolean;
  draftTitle: string;
  draftDescription: string;
  onDraftTitle: (value: string) => void;
  onDraftDescription: (value: string) => void;
  onCommitTitle: () => void;
  onCommitDescription: () => void;
  onSetStatus: (status: TaskStatus) => void;
  onSetDueDate: (iso: string) => void;
  onTimerStart: () => void;
  onTimerStop: () => void;
};

export const TaskSummaryCard = forwardRef<HTMLElement, Props>(function TaskSummaryCard(
  {
    task,
    creator,
    overdue,
    due,
    canManage,
    canChangeStatus,
    canTrackTime,
    saveBusy,
    timerBusy,
    draftTitle,
    draftDescription,
    onDraftTitle,
    onDraftDescription,
    onCommitTitle,
    onCommitDescription,
    onSetStatus,
    onSetDueDate,
    onTimerStart,
    onTimerStop,
  },
  ref
) {
  return (
    <article className={`task-summary-card${canManage ? " is-editable" : ""}`} ref={ref}>
      <div className="task-summary-intro muted">
        <span className="user-mark">{creator}</span> создал задачу
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

      <dl className="task-summary-fields">
        <div className="task-summary-row">
          <dt>Статус</dt>
          <dd className="task-status-group">
            <span className={`task-status-pill ${STATUS_TONE[task.status]}`}>
              {STATUS_LABEL[task.status]}
            </span>
            {overdue ? <span className="task-status-pill status-overdue">Опаздывает</span> : null}
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
          </dd>
        </div>
        <div className="task-summary-row">
          <dt>Время</dt>
          <dd>
            <TaskTimer
              closedSeconds={task.total_tracked_seconds || 0}
              activeStartedAt={task.active_timer?.started_at || null}
              canTrack={canTrackTime}
              busy={timerBusy || saveBusy}
              onStart={onTimerStart}
              onStop={onTimerStop}
            />
          </dd>
        </div>
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
            {canManage ? (
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
        {canManage && (
          <div className="task-summary-row">
            <dt>Bitrix</dt>
            <dd>
              {SYNC_LABEL[task.sync_status] || task.sync_status}
              {task.bitrix_task_id ? ` · клиент #${task.bitrix_task_id}` : ""}
              {task.agency_bitrix_task_id ? ` · агентство #${task.agency_bitrix_task_id}` : ""}
              {task.sync_error ? (
                <div className="muted" style={{ marginTop: 4, color: "#b42318" }}>
                  {task.sync_error}
                </div>
              ) : null}
            </dd>
          </div>
        )}
      </dl>

      <time className="task-summary-time">{formatClock(task.created_at)}</time>
    </article>
  );
});
