import { forwardRef, useEffect, useState } from "react";
import { FileGlyph, PaperclipIcon, SendIcon } from "../icons";
import { isImageFile } from "../../lib/files";

type Props = {
  comment: string;
  pendingFiles: File[];
  canSend: boolean;
  onCommentChange: (value: string) => void;
  onPickFiles: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onAddFiles: (files: File[]) => void;
  onRemovePending: (index: number) => void;
  onSend: (e?: React.FormEvent) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
};

function PendingThumb({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!isImageFile(file)) return;
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  if (url) {
    return <img src={url} alt={file.name} className="msg-pending-thumb" />;
  }
  return <FileGlyph />;
}

export const TaskComposer = forwardRef<HTMLTextAreaElement, Props>(function TaskComposer(
  {
    comment,
    pendingFiles,
    canSend,
    onCommentChange,
    onPickFiles,
    onAddFiles,
    onRemovePending,
    onSend,
    onKeyDown,
  },
  ref
) {
  const [dragOver, setDragOver] = useState(false);

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    const list = e.dataTransfer?.files;
    if (!list?.length) {
      console.info("[nextgen-attach] drop: empty FileList");
      return;
    }
    const files = Array.from(list);
    console.info("[nextgen-attach] drop", {
      count: files.length,
      names: files.map((f) => f.name),
      sizes: files.map((f) => f.size),
    });
    onAddFiles(files);
  }

  return (
    <form
      className={`msg-composer messenger-composer${dragOver ? " is-dragover" : ""}`}
      onSubmit={(e) => void onSend(e)}
      onDragEnter={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        setDragOver(true);
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
        setDragOver(false);
      }}
      onDrop={onDrop}
      data-tour="tour-task-composer"
    >
      {pendingFiles.length > 0 && (
        <div className="msg-pending">
          {pendingFiles.map((f, i) => (
            <span
              key={`${f.name}-${i}`}
              className={`msg-pending-chip${isImageFile(f) ? " is-image" : ""}`}
            >
              <PendingThumb file={f} />
              <span>{f.name}</span>
              <button
                type="button"
                className="msg-pending-remove"
                onClick={() => onRemovePending(i)}
                aria-label="Убрать файл"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="msg-composer-bar">
        {/*
          Direct user click on a full-size transparent <input type="file">.
          Do NOT nest file input inside <button> (invalid HTML — browsers may
          drop change events). Do NOT use display:none / 1px clip + input.click()
          (Bitrix iframe often opens the dialog then never fires onChange).
        */}
        <label
          className="msg-attach"
          title="Прикрепить файл или фото"
          aria-label="Прикрепить файл или фото"
        >
          <span className="msg-attach-icon" aria-hidden>
            <PaperclipIcon />
          </span>
          <input
            type="file"
            multiple
            className="msg-attach-input"
            onClick={(e) => {
              // Reset so selecting the same file again still fires change
              e.currentTarget.value = "";
              console.info("[nextgen-attach] picker opened");
            }}
            onChange={(e) => {
              const list = e.target.files;
              console.info("[nextgen-attach] input change", {
                count: list?.length ?? 0,
                names: list ? Array.from(list).map((f) => f.name) : [],
              });
              onPickFiles(e);
            }}
          />
        </label>

        <textarea
          ref={ref}
          value={comment}
          onChange={(e) => onCommentChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={dragOver ? "Отпустите файлы сюда…" : "Написать сообщение…"}
          rows={1}
          className="msg-input"
        />

        <button
          type="submit"
          className="msg-send"
          disabled={!canSend}
          aria-label="Отправить"
          title="Отправить"
        >
          <SendIcon />
        </button>
      </div>
    </form>
  );
});
