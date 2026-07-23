import { forwardRef, useMemo, useState } from "react";
import type { Attachment, ThreadItem } from "../../api/types";
import { ImageLightbox, type LightboxImage } from "../ImageLightbox";
import { FileGlyph } from "../icons";
import { isImageAttachment } from "../../lib/files";
import { formatClock } from "../../lib/format";
import { initialsFromLabel } from "../../lib/portalUi";

export type { ThreadItem };

export type ThreadRow =
  | { type: "day"; label: string }
  | { type: "item"; item: ThreadItem };

type Props = {
  rows: ThreadRow[];
};

function toLightboxImage(a: Attachment): LightboxImage | null {
  if (!a.url || !isImageAttachment(a.original_name, a.url)) return null;
  return { url: a.url, name: a.original_name || "Фото" };
}

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
      download={name}
      target="_blank"
      rel="noreferrer"
      className={`msg-file${compact ? " compact" : ""}`}
      title={name}
    >
      <span className="msg-file-icon" aria-hidden>
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
  cover = false,
  onOpenImage,
}: {
  attachment: Attachment;
  compact?: boolean;
  time?: string;
  cover?: boolean;
  onOpenImage?: () => void;
}) {
  const url = attachment.url || "#";
  const name = attachment.original_name || "Файл";
  const looksLikeImage = isImageAttachment(attachment.original_name, attachment.url);
  const [imgFailed, setImgFailed] = useState(false);

  if (looksLikeImage && attachment.url && !imgFailed) {
    return (
      <button
        type="button"
        className={`msg-image${compact ? " compact" : ""}${cover ? " is-cover" : ""}`}
        title={name}
        onClick={onOpenImage}
      >
        <img
          src={url}
          alt={name}
          loading="lazy"
          onError={() => setImgFailed(true)}
        />
        {time ? <span className="msg-image-time muted">{time}</span> : null}
      </button>
    );
  }

  return <FileChip url={url} name={name} compact={compact} time={time} />;
}

function AttachmentGroup({
  attachments,
  compact = false,
  onOpenImage,
}: {
  attachments: Attachment[];
  compact?: boolean;
  onOpenImage: (images: LightboxImage[], index: number) => void;
}) {
  const images: Attachment[] = [];
  const files: Attachment[] = [];
  for (const a of attachments) {
    if (isImageAttachment(a.original_name, a.url)) images.push(a);
    else files.push(a);
  }

  const lightboxImages = images
    .map(toLightboxImage)
    .filter((x): x is LightboxImage => Boolean(x));

  const gridMod =
    images.length <= 1 ? "is-single" : images.length === 2 ? "is-duo" : "is-many";

  return (
    <div
      className={`msg-attachments${images.length ? " has-images" : ""}${
        files.length ? " has-files" : ""
      }${!images.length && files.length ? " files-only" : ""}`}
    >
      {images.length > 0 && (
        <div className={`msg-image-grid ${gridMod}`}>
          {images.map((a, i) => (
            <AttachmentView
              key={a.id}
              attachment={a}
              compact={compact}
              cover={images.length > 1}
              onOpenImage={() => onOpenImage(lightboxImages, Math.min(i, lightboxImages.length - 1))}
            />
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="msg-files">
          {files.map((a) => (
            <AttachmentView key={a.id} attachment={a} compact={compact} />
          ))}
        </div>
      )}
    </div>
  );
}

export const TaskThread = forwardRef<HTMLDivElement, Props>(function TaskThread(
  { rows },
  ref
) {
  const [viewer, setViewer] = useState<{ images: LightboxImage[]; index: number } | null>(
    null
  );

  const aloneImages = useMemo(() => {
    const map = new Map<number, LightboxImage>();
    for (const row of rows) {
      if (row.type !== "item" || row.item.kind !== "file") continue;
      const img = toLightboxImage(row.item.file);
      if (img) map.set(row.item.file.id, img);
    }
    return map;
  }, [rows]);

  function openViewer(images: LightboxImage[], index: number) {
    if (!images.length) return;
    setViewer({ images, index: Math.max(0, Math.min(index, images.length - 1)) });
  }

  return (
    <>
      {rows.map((row) => {
        if (row.type === "day") {
          return (
            <div key={`day-${row.label}`} className="chat-day-pill">
              {row.label}
            </div>
          );
        }

        const item = row.item;
        if (item.kind === "file") {
          const alone = aloneImages.get(item.file.id);
          return (
            <div key={`file-${item.file.id}`} className="msg-file-alone">
              <AttachmentView
                attachment={item.file}
                time={formatClock(item.file.created_at)}
                onOpenImage={
                  alone ? () => openViewer([alone], 0) : undefined
                }
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

        const hasText = Boolean(c.text?.trim());
        const hasAttach = (c.attachments || []).length > 0;
        const bubbleClass = [
          "msg-bubble",
          hasAttach ? "has-attach" : "",
          hasAttach && !hasText ? "is-media" : "",
        ]
          .filter(Boolean)
          .join(" ");

        return (
          <article key={`c-${c.id}`} className="msg-row">
            <div className="comment-avatar" aria-hidden>
              {initialsFromLabel(author)}
            </div>
            <div className={bubbleClass}>
              <div className="comment-top">
                <strong className="user-mark">{author}</strong>
              </div>
              {hasText ? <p className="comment-text">{c.text}</p> : null}
              {hasAttach && (
                <AttachmentGroup
                  attachments={c.attachments || []}
                  compact
                  onOpenImage={openViewer}
                />
              )}
              <time className="msg-time">{formatClock(c.created_at)}</time>
            </div>
          </article>
        );
      })}
      <div ref={ref} />

      {viewer && (
        <ImageLightbox
          images={viewer.images}
          index={viewer.index}
          onClose={() => setViewer(null)}
          onIndexChange={(index) => setViewer((v) => (v ? { ...v, index } : v))}
        />
      )}
    </>
  );
});
