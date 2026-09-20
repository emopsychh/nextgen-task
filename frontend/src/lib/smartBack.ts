import { useCallback } from "react";
import { useNavigate, useLocation } from "react-router-dom";

type LocationFromState = {
  from?: string;
};

/** True when React Router has a previous entry in this tab's history stack. */
export function canGoBackInApp(): boolean {
  const idx = (window.history.state as { idx?: number } | null)?.idx;
  return typeof idx === "number" && idx > 0;
}

/**
 * Prefer in-app history, then explicit `location.state.from`, then fallback.
 * Fixes «Требует внимания» → задача → «Назад» jumping to the project board.
 */
export function useSmartBack(fallback: string) {
  const navigate = useNavigate();
  const location = useLocation();

  return useCallback(() => {
    if (canGoBackInApp()) {
      navigate(-1);
      return;
    }
    const from = (location.state as LocationFromState | null)?.from;
    if (from && from !== location.pathname + location.search) {
      navigate(from);
      return;
    }
    navigate(fallback);
  }, [fallback, location.pathname, location.search, location.state, navigate]);
}

/** Attach to Link `state` so detail screens can return to the exact origin. */
export function linkStateFrom(location: { pathname: string; search: string }) {
  return { from: `${location.pathname}${location.search}` };
}
