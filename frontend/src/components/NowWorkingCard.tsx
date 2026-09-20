import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type { Task } from "../api/types";
import { formatTimerClock } from "../lib/format";
import { linkStateFrom } from "../lib/smartBack";
import { BoardAvatar } from "./BoardAvatar";

type Props = {
  tasks: Task[];
  loading?: boolean;
};

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

function elapsedLabel(startedAt: string | null | undefined, now: number): string {
  if (!startedAt) return "";
  const started = new Date(startedAt).getTime();
  if (!Number.isFinite(started)) return "";
  return formatTimerClock(Math.max(0, Math.floor((now - started) / 1000)));
}

function WorkingTaskRow({
  task,
  now,
  onOpen,
  fromState,
}: {
  task: Task;
  now: number;
  onOpen?: () => void;
  fromState: ReturnType<typeof linkStateFrom>;
}) {
  const person = task.working_by_name || "";
  const elapsed = elapsedLabel(task.working_started_at, now);
  return (
    <Link
      to={`/tasks/${task.id}`}
      state={fromState}
      className="now-working-row"
      onClick={onOpen}
    >
      <div className="now-working-copy">
        <strong>{task.title}</strong>
        <span className="muted">{task.project_name || "Проект"}</span>
      </div>
      <div className="now-working-meta">
        {person ? (
          <span className="now-working-person">
            <BoardAvatar name={person} />
            <span>{person}</span>
          </span>
        ) : null}
        {elapsed ? (
          <span className="now-working-elapsed" title="Сколько уже работают">
            {elapsed}
          </span>
        ) : null}
      </div>
    </Link>
  );
}

export function NowWorkingCard({ tasks, loading }: Props) {
  const location = useLocation();
  const fromState = linkStateFrom(location);
  const [allOpen, setAllOpen] = useState(false);
  const now = useNow(tasks.length > 0);
  const count = tasks.length;
  const preview = tasks[0];
  const title = count > 1 ? `Сейчас в работе · ${count}` : "Сейчас в работе";

  useEffect(() => {
    if (!allOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAllOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [allOpen]);

  useEffect(() => {
    if (count <= 1 && allOpen) setAllOpen(false);
  }, [count, allOpen]);

  return (
    <section className="now-working-card" aria-label="Задачи, над которыми работают прямо сейчас">
      <div className="now-working-head">
        <h2 className="section-title">{title}</h2>
        {count > 1 ? (
          <button
            type="button"
            className="overview-text-link now-working-all"
            onClick={() => setAllOpen(true)}
          >
            Посмотреть все
          </button>
        ) : null}
      </div>
      {loading && count === 0 ? (
        <div className="now-working-empty">
          <span className="data-loading-spinner" aria-hidden />
          <p className="muted">Смотрим, кто работает…</p>
        </div>
      ) : count === 0 ? (
        <div className="now-working-empty">
          <p className="muted">Сейчас никто не работает над задачами.</p>
        </div>
      ) : preview ? (
        <ul className="now-working-list">
          <li>
            <WorkingTaskRow task={preview} now={now} fromState={fromState} />
          </li>
        </ul>
      ) : null}

      {allOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onClick={() => setAllOpen(false)}
        >
          <div
            className="modal-card modal-card-wide now-working-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="now-working-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="now-working-modal-head">
              <div>
                <h3 id="now-working-modal-title" className="modal-title">
                  Сейчас в работе
                </h3>
                <p className="muted now-working-modal-sub">
                  {count} {count === 1 ? "задача" : count < 5 ? "задачи" : "задач"}
                </p>
              </div>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setAllOpen(false)}
              >
                Закрыть
              </button>
            </header>
            <ul className="now-working-list now-working-modal-list">
              {tasks.map((t) => (
                <li key={t.id}>
                  <WorkingTaskRow
                    task={t}
                    now={now}
                    fromState={fromState}
                    onOpen={() => setAllOpen(false)}
                  />
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </section>
  );
}
