import { forwardRef, useEffect, useMemo, useRef, useState } from "react";
import { FileGlyph, PaperclipIcon, SendIcon } from "../icons";
import { isImageFile } from "../../lib/files";

type Props = {
  comment: string;
  pendingFiles: File[];
  canSend: boolean;
  onCommentChange: (value: string) => void;
  onPickFiles: (e: React.ChangeEvent<HTMLInputElement>) => void;
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
    onRemovePending,
    onSend,
    onKeyDown,
  },
  ref
) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const accept = useMemo(
    () => "image/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.zip,.rar,.7z",
    []
  );

  return (
    <form
      className="msg-composer messenger-composer"
      onSubmit={(e) => void onSend(e)}
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
        <label className="msg-attach" title="Прикрепить файл или фото">
          <PaperclipIcon />
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={accept}
            hidden
            onChange={onPickFiles}
          />
        </label>

        <textarea
          ref={ref}
          value={comment}
          onChange={(e) => onCommentChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Написать сообщение…"
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
