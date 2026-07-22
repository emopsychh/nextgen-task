import { forwardRef, useState } from "react";
import type { Attachment, Comment } from "../../api/types";
import { FileGlyph } from "../icons";
import { isImageAttachment } from "../../lib/files";
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

function FileChip({
  url,
  name,
  compact,
  time,
}: {
  url: string;
  name: string;
  compact?: boolean;
  time?: string;
}) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className={`msg-file${compact ? " compact" : ""}`}
    >
      <span className="msg-file-icon">
        <FileGlyph />
      </span>
      <span className="msg-file-meta">
        <strong>{name}</strong>
        {time ? <span className="muted">{time}</span> : null}
      </span>
    </a>
  );
}

function AttachmentView({
  attachment,
  compact = false,
  time,
}: {
  attachment: Attachment;
  compact?: boolean;
  time?: string;
}) {
  const url = attachment.url || "#";
  const name = attachment.original_name || "Файл";
  const looksLikeImage = isImageAttachment(attachment.original_name, attachment.url);
  const [imgFailed, setImgFailed] = useState(false);

  if (looksLikeImage && attachment.url && !imgFailed) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className={`msg-image${compact ? " compact" : ""}`}
        title={name}
      >
        <img
          src={url}
          alt={name}
          loading="lazy"
          onError={() => setImgFailed(true)}
        />
        {time ? <span className="msg-image-time muted">{time}</span> : null}
      </a>
    );
  }

  return <FileChip url={url} name={name} compact={compact} time={time} />;
}

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
              <AttachmentView
                attachment={item.file}
                time={formatClock(item.file.created_at)}
              />
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
                    <AttachmentView key={a.id} attachment={a} compact />
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
