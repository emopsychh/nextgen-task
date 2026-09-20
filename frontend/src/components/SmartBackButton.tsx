import type { ReactNode, MouseEvent } from "react";
import { useSmartBack } from "../lib/smartBack";

type Props = {
  fallback: string;
  className?: string;
  title?: string;
  children: ReactNode;
};

/** In-app back control that respects navigation history. */
export function SmartBackButton({ fallback, className, title, children }: Props) {
  const goBack = useSmartBack(fallback);

  function onClick(e: MouseEvent) {
    e.preventDefault();
    goBack();
  }

  return (
    <button type="button" className={className} title={title} onClick={onClick}>
      {children}
    </button>
  );
}
