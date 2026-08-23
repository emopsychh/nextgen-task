import type { ReactNode } from "react";

type Props<T> = {
  items: T[];
  /** When false, render a single list (status filter already isolates done items). */
  split: boolean;
  isDone: (item: T) => boolean;
  doneLabel: string;
  as?: "ul" | "div";
  className?: string;
  renderItem: (item: T) => ReactNode;
};

function BoardDoneHeading({ label }: { label: string }) {
  return (
    <div className="board-done-head" role="separator">
      <span className="board-done-rule" aria-hidden />
      <h2 className="board-done-title">{label}</h2>
      <span className="board-done-rule" aria-hidden />
    </div>
  );
}

export function BoardDoneSplit<T>({
  items,
  split,
  isDone,
  doneLabel,
  as = "ul",
  className = "board-list",
  renderItem,
}: Props<T>) {
  const Group = as === "ul" ? "ul" : "div";
  if (!split) {
    return <Group className={className}>{items.map(renderItem)}</Group>;
  }
  const active: T[] = [];
  const done: T[] = [];
  for (const item of items) (isDone(item) ? done : active).push(item);
  return (
    <div className="board-split">
      {active.length > 0 ? (
        <Group className={className}>{active.map(renderItem)}</Group>
      ) : null}
      {done.length > 0 ? (
        <>
          <BoardDoneHeading label={doneLabel} />
          <Group className={`${className} is-done-group`}>{done.map(renderItem)}</Group>
        </>
      ) : null}
    </div>
  );
}
