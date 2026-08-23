import { personHue, personInitials } from "../lib/person";

export function BoardAvatar({ name }: { name: string }) {
  return (
    <span className="board-avatar" style={{ background: personHue(name) }} aria-hidden>
      {personInitials(name)}
    </span>
  );
}
