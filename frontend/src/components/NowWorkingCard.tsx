import { Link, useLocation } from "react-router-dom";
import type { Task } from "../api/types";
import { writePortalCache } from "../lib/portalSessionCache";
import { linkStateFrom } from "../lib/smartBack";

type Props = {
  tasks: Task[];
  loading?: boolean;
  emptyText?: string;
};

function WorkingTaskRow({
  task,
  fromState,
}: {
  task: Task;
  fromState: ReturnType<typeof linkStateFrom>;
}) {
  return (
    <Link
      to={`/tasks/${task.id}`}
      state={fromState}
      onClick={() => writePortalCache("task-detail", task.id, task)}
      className="now-working-row"
    >
      <div className="now-working-copy">
        <strong>{task.title}</strong>
        <span className="muted">{task.project_name || "Проект"}</span>
      </div>
    </Link>
  );
}

export function NowWorkingCard({ tasks, loading, emptyText }: Props) {
  const location = useLocation();
  const fromState = linkStateFrom(location);
  const count = tasks.length;
  const preview = tasks.slice(0, 2);
  const extra = Math.max(0, count - preview.length);

  return (
    <section className="now-working-card" aria-label="Задачи, над которыми работают прямо сейчас">
      <div className="now-working-head">
        <h2 className="section-title">В работе сейчас</h2>
        {count > 0 ? <span className="now-working-count">{count} задач</span> : null}
      </div>
      {loading && count === 0 ? (
        <div className="now-working-empty">
          <span className="data-loading-spinner" aria-hidden />
          <p className="muted">Смотрим, кто работает…</p>
        </div>
      ) : count === 0 ? (
        <div className="now-working-empty">
          <p className="muted">{emptyText || "Сейчас у команды нет активных задач."}</p>
        </div>
      ) : preview.length > 0 ? (
        <ul className="now-working-list">
          {preview.map((task) => (
            <li key={task.id}>
              <WorkingTaskRow task={task} fromState={fromState} />
            </li>
          ))}
          {extra > 0 ? <li className="now-working-extra">Ещё {extra} задач в работе</li> : null}
        </ul>
      ) : null}
    </section>
  );
}
