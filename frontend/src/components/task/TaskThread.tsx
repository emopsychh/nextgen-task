import { forwardRef } from "react";
import type { Attachment, Comment } from "../../api/types";
import { FileGlyph } from "../icons";
import { formatClock } from "../../lib/format";
import { initialsFromLabel } from "../../lib/portalUi";

export type ThreadItem =
  | { kind: "comment"; at: string; comment: Comment }
  | { kind: "file"; at: string; file: Attachment };

export type ThreadRow =
  | { type: "day"; label: string }
  | { type: "item"; item: ThreadItem };

type Props = {
  rows: ThreadRow[];
};

export const TaskThread = forwardRef<HTMLDivElement, Props>(function TaskThread(
  { rows },
  ref
) {
  return (
    <>
      {rows.map((row, idx) => {
        if (row.type === "day") {
          return (
            <div key={`day-${idx}-${row.label}`} className="chat-day-pill">
              {row.label}
            </div>
          );
        }

        const item = row.item;
        if (item.kind === "file") {
          return (
            <div key={`file-${item.file.id}`} className="msg-file-alone">
              <a
                href={item.file.url || "#"}
                target="_blank"
                rel="noreferrer"
                className="msg-file"
              >
                <span className="msg-file-icon">
                  <FileGlyph />
                </span>
                <span className="msg-file-meta">
                  <strong>{item.file.original_name || "Файл"}</strong>
                  <span className="muted">{formatClock(item.file.created_at)}</span>
                </span>
              </a>
            </div>
          );
        }

        const c = item.comment;
        const author = c.author_display || c.author_name || "Участник";
        if (c.is_system) {
          return (
            <article key={`c-${c.id}`} className="msg-system">
              <div className="msg-system-bubble">
                <span className="user-mark">{author}</span> {c.text}
                <time className="msg-time">{formatClock(c.created_at)}</time>
              </div>
            </article>
          );
        }
        return (
          <article key={`c-${c.id}`} className="msg-row">
            <div className="comment-avatar" aria-hidden>
              {initialsFromLabel(author)}
            </div>
            <div className="msg-bubble">
              <div className="comment-top">
                <strong className="user-mark">{author}</strong>
              </div>
              {c.text ? <p className="comment-text">{c.text}</p> : null}
              {(c.attachments || []).length > 0 && (
                <div className="msg-files">
                  {(c.attachments || []).map((a) => (
                    <a
                      key={a.id}
                      href={a.url || "#"}
                      target="_blank"
                      rel="noreferrer"
                      className="msg-file compact"
                    >
                      <span className="msg-file-icon">
                        <FileGlyph />
                      </span>
                      <span className="msg-file-meta">
                        <strong>{a.original_name || "Файл"}</strong>
                      </span>
                    </a>
                  ))}
                </div>
              )}
              <time className="msg-time">{formatClock(c.created_at)}</time>
            </div>
          </article>
        );
      })}
      <div ref={ref} />
    </>
  );
});
