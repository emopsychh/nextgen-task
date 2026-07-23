import { useEffect, useId } from "react";
import { createPortal } from "react-dom";

export type LightboxImage = {
  url: string;
  name: string;
};

type Props = {
  images: LightboxImage[];
  index: number;
  onClose: () => void;
  onIndexChange: (index: number) => void;
};

async function downloadImage(url: string, name: string) {
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error("fetch failed");
    const blob = await res.blob();
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = name || "image";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objectUrl);
  } catch {
    // Same-origin fallback / open in new tab if blob download fails
    const a = document.createElement("a");
    a.href = url;
    a.download = name || "image";
    a.target = "_blank";
    a.rel = "noreferrer";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
}

export function ImageLightbox({ images, index, onClose, onIndexChange }: Props) {
  const titleId = useId();
  const current = images[index];
  const hasMany = images.length > 1;

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (!hasMany) return;
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        onIndexChange((index - 1 + images.length) % images.length);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        onIndexChange((index + 1) % images.length);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasMany, images.length, index, onClose, onIndexChange]);

  if (!current?.url) return null;

  return createPortal(
    <div
      className="img-lightbox"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onClick={onClose}
    >
      <div className="img-lightbox-top" onClick={(e) => e.stopPropagation()}>
        <span id={titleId} className="img-lightbox-name" title={current.name}>
          {current.name}
        </span>
        <div className="img-lightbox-actions">
          <button
            type="button"
            className="img-lightbox-btn"
            title="Скачать"
            aria-label="Скачать фото"
            onClick={() => void downloadImage(current.url, current.name)}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M12 4v10m0 0 4-4m-4 4-4-4M5 18h14"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          <button
            type="button"
            className="img-lightbox-btn"
            title="Закрыть"
            aria-label="Закрыть"
            onClick={onClose}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M6 6l12 12M18 6 6 18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </div>

      <div className="img-lightbox-stage" onClick={(e) => e.stopPropagation()}>
        {hasMany && (
          <button
            type="button"
            className="img-lightbox-nav prev"
            aria-label="Предыдущее фото"
            onClick={() => onIndexChange((index - 1 + images.length) % images.length)}
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M15 6 9 12l6 6"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        )}

        <div className="img-lightbox-frame">
          <img
            className="img-lightbox-photo"
            src={current.url}
            alt={current.name}
            draggable={false}
          />
          <button
            type="button"
            className="img-lightbox-dl"
            title="Скачать"
            aria-label="Скачать фото"
            onClick={() => void downloadImage(current.url, current.name)}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M12 4v10m0 0 4-4m-4 4-4-4M5 18h14"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Скачать
          </button>
        </div>

        {hasMany && (
          <button
            type="button"
            className="img-lightbox-nav next"
            aria-label="Следующее фото"
            onClick={() => onIndexChange((index + 1) % images.length)}
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path
                d="M9 6l6 6-6 6"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        )}
      </div>

      {hasMany && (
        <div className="img-lightbox-counter" onClick={(e) => e.stopPropagation()}>
          {index + 1} / {images.length}
        </div>
      )}
    </div>,
    document.body
  );
}
