import { useCallback, useEffect, useRef, useState } from "react";

type FlashToastState = {
  message: string | null;
  title?: string;
  leaving: boolean;
  show: (message: string, title?: string) => void;
  clear: () => void;
};

/** Success toast with leave animation — shared across pages */
export function useFlashToast(holdMs = 2000, leaveMs = 400): FlashToastState {
  const [message, setMessage] = useState<string | null>(null);
  const [title, setTitle] = useState<string | undefined>();
  const [leaving, setLeaving] = useState(false);
  const timers = useRef<number[]>([]);

  const clearTimers = useCallback(() => {
    for (const id of timers.current) window.clearTimeout(id);
    timers.current = [];
  }, []);

  const clear = useCallback(() => {
    clearTimers();
    setMessage(null);
    setTitle(undefined);
    setLeaving(false);
  }, [clearTimers]);

  const show = useCallback(
    (next: string, nextTitle?: string) => {
      clearTimers();
      setLeaving(false);
      setMessage(next);
      setTitle(nextTitle);
      timers.current.push(
        window.setTimeout(() => setLeaving(true), holdMs),
        window.setTimeout(() => {
          setMessage(null);
          setTitle(undefined);
          setLeaving(false);
        }, holdMs + leaveMs)
      );
    },
    [clearTimers, holdMs, leaveMs]
  );

  useEffect(() => () => clearTimers(), [clearTimers]);

  return { message, title, leaving, show, clear };
}
