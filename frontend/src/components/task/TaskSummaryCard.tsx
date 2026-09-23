import { Link } from "react-router-dom";
import type { Task, TaskStatus } from "../../api/types";
import { DueDatePicker } from "../DueDatePicker";
import { formatDateTime, formatDueFull } from "../../lib/format";
import { formatRuDateTime } from "../../lib/dates";
import type { DueTone } from "../../lib/dates";
import { AutoGrowTextarea } from "./AutoGrowTextarea";
import { TaskTimer } from "./TaskTimer";

type Props = {
  task: Task;
  creator: string;
  due: { label: string; tone: DueTone; detail?: string };
  canManage: boolean;
  canChangeStatus: boolean;
  canEditDueDate: boolean;
  saveBusy: boolean;
  onSetStatus: (status: TaskStatus) => void;
  onRequestComplete: () => void;
  onSetDueDate: (iso: string) => void;
  onToggleAwaitingClient?: () => void;
  draftOutcome?: string;
  onDraftOutcome?: (value: string) => void;
  onCommitOutcome?: () => void;
  canAddTime?: boolean;
  onSetTime?: (hours: number, minutes: number) => Promise<void> | void;
  /** IANA zone for due display / picker (defaults to the viewer's timezone). */
  dueTimeZone?: string;
  onDelete?: () => void;
};

export function TaskSummaryCard({
  task,
  creator,
  due,
  canManage,
  canChangeStatus,
  canEditDueDate,
  saveBusy,
  onSetStatus,
  onRequestComplete,
  onSetDueDate,
  onToggleAwaitingClient,
  draftOutcome = "",
  onDraftOutcome,
  onCommitOutcome,
  canAddTime = false,
  onSetTime,
  dueTimeZone,
  onDelete,
}: Props) {
  const awaitingClient = Boolean(task.awaiting_client);
  return (
    <aside
      className={`task-meta-pane${task.status === "done" ? " is-done" : ""}`}
    >
      <div className="task-details-heading">
        <strong>Детали</strong>
        <span>#{task.id}</span>
      </div>

      <dl className="task-meta-fields">
        <div className="task-meta-row">
          <dt>Постановщик</dt>
          <dd>
            <span className="user-mark">{creator}</span>
          </dd>
        </div>
        <div className="task-meta-row task-meta-row-due">
          <dt>Срок</dt>
          <dd>
            {canEditDueDate ? (
              <DueDatePicker
                value={task.due_date || ""}
                onChange={onSetDueDate}
                status={task.status}
                variant="inline"
                timeZone={dueTimeZone}
              />
            ) : (
              <span className={`task-due-inline ${due.tone}`}>
                {formatDueFull(task.due_date, dueTimeZone)}
                {task.due_date ? ` · ${due.label}` : ""}
              </span>
            )}
          </dd>
        </div>
        {task.status === "done" ? (
          <div className="task-meta-row">
            <dt>Реализовали</dt>
            <dd>
              {task.completed_at
                ? formatRuDateTime(task.completed_at, dueTimeZone)
                : "—"}
            </dd>
          </div>
        ) : null}
        <div className="task-meta-row task-meta-row-timer">
          <dt>Время</dt>
          <dd className="task-meta-timer">
            <TaskTimer
              totalSeconds={task.total_tracked_seconds || 0}
              canEdit={Boolean(canAddTime && onSetTime)}
              busy={saveBusy}
              onSetTime={onSetTime || (async () => undefined)}
            />
          </dd>
        </div>
        <div className="task-meta-row">
          <dt>Проект</dt>
          <dd>
            <Link to={`/projects/${task.project}`} className="task-meta-link">
              {task.project_name}
            </Link>
          </dd>
        </div>
        <div className="task-meta-row">
          <dt>Создана</dt>
          <dd>
            {formatDateTime(task.created_at)}
            <span className="task-meta-id muted"> · #{task.id}</span>
          </dd>
        </div>
      </dl>

      {task.status === "done" || draftOutcome.trim() || task.outcome?.trim() ? (
        <div className="task-meta-section">
          <div className="task-meta-section-label">Итог</div>
          {canManage && onDraftOutcome && onCommitOutcome ? (
            <AutoGrowTextarea
              className={`task-meta-desc-input${!draftOutcome.trim() ? " is-empty" : ""}`}
              value={draftOutcome}
              onChange={(e) => onDraftOutcome(e.target.value)}
              onBlur={() => onCommitOutcome()}
              minRows={2}
              maxHeight={180}
              placeholder="Что сделано по задаче…"
              disabled={saveBusy}
              aria-label="Итог работы"
            />
          ) : task.outcome?.trim() ? (
            <p className="task-meta-desc">{task.outcome}</p>
          ) : (
            <p className="task-meta-desc is-empty">Итог пока не указан</p>
          )}
        </div>
      ) : null}

      {canChangeStatus ? (
        <div className="task-meta-actions" role="group" aria-label="Действия со статусом">
          {task.status === "todo" && (
            <>
              <button
                type="button"
                className="btn btn-accent"
                disabled={saveBusy}
                onClick={() => onSetStatus("in_progress")}
              >
                Начать
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={saveBusy}
                onClick={onRequestComplete}
              >
                Завершить
              </button>
            </>
          )}
          {task.status === "in_progress" && (
            <>
              <button
                type="button"
                className="btn btn-accent"
                disabled={saveBusy}
                onClick={() => onSetStatus("todo")}
              >
                Пауза
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={saveBusy}
                onClick={onRequestComplete}
              >
                Завершить
              </button>
            </>
          )}
          {task.status !== "done" && onToggleAwaitingClient ? (
            <button
              type="button"
              className={`btn task-awaiting-btn${awaitingClient ? " btn-accent" : " btn-ghost"}`}
              disabled={saveBusy}
              onClick={onToggleAwaitingClient}
              aria-pressed={awaitingClient}
            >
              {awaitingClient ? "Отменить ожидание ответа" : "Ожидаем ответ от клиента"}
            </button>
          ) : null}
          {task.can_delete && onDelete ? (
            <button
              type="button"
              className="btn btn-ghost task-delete-btn"
              disabled={saveBusy}
              onClick={onDelete}
            >
              Удалить задачу
            </button>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}
