/** Subtle “catching up with Bitrix” cue while a background pull runs. */
export function SyncHint({ children }: { children: string }) {
  return (
    <span className="sync-hint" aria-live="polite">
      <span className="sync-hint-dot" aria-hidden />
      {children}
    </span>
  );
}
