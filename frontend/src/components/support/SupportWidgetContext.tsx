import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type SupportWidgetApi = {
  isOpen: boolean;
  open: (ticketId?: number | null) => void;
  close: () => void;
  toggle: () => void;
  initialTicketId: number | null;
};

const SupportWidgetContext = createContext<SupportWidgetApi | null>(null);

export function SupportWidgetProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [initialTicketId, setInitialTicketId] = useState<number | null>(null);

  const open = useCallback((ticketId?: number | null) => {
    setInitialTicketId(ticketId ?? null);
    setIsOpen(true);
  }, []);

  const close = useCallback(() => {
    setIsOpen(false);
    setInitialTicketId(null);
  }, []);

  const toggle = useCallback(() => {
    setIsOpen((prev) => {
      if (prev) setInitialTicketId(null);
      return !prev;
    });
  }, []);

  const value = useMemo(
    () => ({ isOpen, open, close, toggle, initialTicketId }),
    [isOpen, open, close, toggle, initialTicketId]
  );

  return (
    <SupportWidgetContext.Provider value={value}>{children}</SupportWidgetContext.Provider>
  );
}

export function useSupportWidget(): SupportWidgetApi {
  const ctx = useContext(SupportWidgetContext);
  if (!ctx) {
    return {
      isOpen: false,
      open: () => undefined,
      close: () => undefined,
      toggle: () => undefined,
      initialTicketId: null,
    };
  }
  return ctx;
}
