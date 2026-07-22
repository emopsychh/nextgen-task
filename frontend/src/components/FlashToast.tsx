type Props = {
  message: string | null;
  title?: string;
  leaving?: boolean;
};

export function FlashToast({ message, title, leaving }: Props) {
  if (!message) return null;
  return (
    <div className={`toast toast-success${leaving ? " leaving" : ""}`} role="status">
      <span className="toast-icon" aria-hidden>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <path
            d="M20 6 9 17l-5-5"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <div className="toast-body">
        <strong>{title || message}</strong>
        {title ? <span>{message}</span> : null}
      </div>
    </div>
  );
}
