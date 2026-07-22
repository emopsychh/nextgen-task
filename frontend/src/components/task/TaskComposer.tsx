import { forwardRef, useEffect, useRef, useState } from "react";
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
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  function openFilePicker() {
    const input = fileInputRef.current;
    if (!input) return;
    // Programmatic click from a user gesture — more reliable in Bitrix iframes
    // than relying on <label> alone (slider / openApplication / mobile).
    input.click();
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    const list = e.dataTransfer?.files;
    if (!list?.length) return;
    onAddFiles(Array.from(list));
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
          Do not use input[hidden]/display:none — Bitrix iframe often blocks
          the native file dialog for fully hidden inputs. Overlay + explicit
          button click works across slider / fullscreen / openApplication.
        */}
        <button
          type="button"
          className="msg-attach"
          title="Прикрепить файл или фото"
          aria-label="Прикрепить файл или фото"
          onClick={openFilePicker}
        >
          <span className="msg-attach-icon" aria-hidden>
            <PaperclipIcon />
          </span>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="msg-attach-input"
            tabIndex={-1}
            onChange={onPickFiles}
          />
        </button>

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
