import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api, unwrapList, type Portal } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { AGENCY_STEPS, CLIENT_STEPS, type TourPlacement, type TourStep } from "../onboarding/steps";

type LinkRow = { id: number; client_portal: Portal };

function storageKey(portalId: number, role: string) {
  return `nextgen_onboarding_v2_${portalId}_${role}`;
}

function isDone(portalId: number, role: string) {
  try {
    return localStorage.getItem(storageKey(portalId, role)) === "1";
  } catch {
    return true;
  }
}

function markDone(portalId: number, role: string) {
  try {
    localStorage.setItem(storageKey(portalId, role), "1");
  } catch {
    /* ignore */
  }
}

function findTarget(name: string | null): HTMLElement | null {
  if (!name) return null;
  return document.querySelector(`[data-tour="${name}"]`);
}

function pickPlacement(rect: DOMRect, preferred: TourPlacement = "bottom"): TourPlacement {
  const space = {
    top: rect.top,
    bottom: window.innerHeight - rect.bottom,
    left: rect.left,
    right: window.innerWidth - rect.right,
  };
  const order: TourPlacement[] = [preferred, "bottom", "top", "right", "left"];
  for (const p of order) {
    if (space[p] > 140) return p;
  }
  return preferred;
}

type TipPos = { top: number; left: number; placement: TourPlacement };

function tipPosition(rect: DOMRect, tipW: number, tipH: number, preferred: TourPlacement): TipPos {
  const gap = 14;
  const placement = pickPlacement(rect, preferred);
  let top = 0;
  let left = 0;
  if (placement === "bottom") {
    top = rect.bottom + gap;
    left = rect.left + rect.width / 2 - tipW / 2;
  } else if (placement === "top") {
    top = rect.top - tipH - gap;
    left = rect.left + rect.width / 2 - tipW / 2;
  } else if (placement === "right") {
    top = rect.top + rect.height / 2 - tipH / 2;
    left = rect.right + gap;
  } else {
    top = rect.top + rect.height / 2 - tipH / 2;
    left = rect.left - tipW - gap;
  }
  left = Math.min(Math.max(12, left), window.innerWidth - tipW - 12);
  top = Math.min(Math.max(12, top), window.innerHeight - tipH - 12);
  return { top, left, placement };
}

async function resolveClientWorkspace(token: string): Promise<string | null> {
  try {
    const data = await api<LinkRow[] | { results: LinkRow[] }>("/api/portal-links/", {}, token);
    const first = unwrapList(data)[0];
    if (first?.client_portal?.id) return `/portals/${first.client_portal.id}/projects`;
  } catch {
    /* ignore */
  }
  return null;
}

export function OnboardingTour() {
  const { portal, token } = useAuth();
  const navigate = useNavigate();
  const role = portal?.role === "agency" || portal?.role === "client" ? portal.role : null;
  const steps = useMemo(() => (role === "agency" ? AGENCY_STEPS : CLIENT_STEPS), [role]);

  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);
  const [leaving, setLeaving] = useState(false);
  const [hole, setHole] = useState<DOMRect | null>(null);
  const [tip, setTip] = useState<TipPos | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!portal || !role) {
      setOpen(false);
      return;
    }
    if (isDone(portal.id, role)) {
      setOpen(false);
      return;
    }
    setIndex(0);
    setLeaving(false);
    setOpen(true);
  }, [portal?.id, role]);

  const step: TourStep | undefined = steps[index];

  const measure = useCallback(() => {
    if (!step) return;
    const el = findTarget(step.target);
    const tipEl = document.querySelector(".onboard-tip") as HTMLElement | null;
    const tipW = tipEl?.offsetWidth || 340;
    const tipH = tipEl?.offsetHeight || 200;

    if (!el) {
      setHole(null);
      setTip({
        top: Math.max(24, window.innerHeight / 2 - tipH / 2),
        left: Math.max(12, window.innerWidth / 2 - tipW / 2),
        placement: "bottom",
      });
      setReady(true);
      return;
    }

    el.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    const rect = el.getBoundingClientRect();
    const pad = 8;
    const padded = new DOMRect(
      rect.left - pad,
      rect.top - pad,
      rect.width + pad * 2,
      rect.height + pad * 2
    );
    setHole(padded);
    setTip(tipPosition(padded, tipW, tipH, step.placement || "bottom"));
    setReady(true);
  }, [step]);

  useLayoutEffect(() => {
    if (!open || !step || !token) return;
    let cancelled = false;
    setReady(false);

    async function prepare() {
      if (!step) return;
      if (step.route === "home") {
        navigate("/");
      } else if (step.route === "client-workspace" && role === "agency") {
        const path = await resolveClientWorkspace(token!);
        if (path) navigate(path);
        else navigate("/");
      } else if (step.route && step.route !== "home" && step.route !== "client-workspace") {
        navigate(step.route);
      }

      for (let i = 0; i < 12; i += 1) {
        if (cancelled) return;
        await new Promise((r) => window.setTimeout(r, 60 + i * 30));
        if (cancelled) return;
        if (!step.target || findTarget(step.target)) {
          measure();
          return;
        }
      }
      measure();
    }

    void prepare();

    function onResize() {
      measure();
    }
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onResize, true);
    return () => {
      cancelled = true;
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onResize, true);
    };
  }, [open, step, token, role, navigate, measure, index]);

  if (!open || !portal || !role || !step) return null;

  const isLast = index >= steps.length - 1;
  const missingTarget = Boolean(step.target && !hole);

  function finish() {
    markDone(portal!.id, role!);
    setLeaving(true);
    window.setTimeout(() => setOpen(false), 200);
  }

  function next() {
    if (isLast) {
      finish();
      return;
    }
    setIndex((i) => i + 1);
  }

  function back() {
    setIndex((i) => Math.max(0, i - 1));
  }

  return createPortal(
    <div
      className={`onboard-root spotlight${leaving ? " is-leaving" : ""}${ready ? " is-ready" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby="onboard-title"
    >
      <div className="onboard-scrim" style={{ opacity: hole ? 0 : 1 }} aria-hidden />

      {hole && (
        <div
          className="onboard-hole"
          style={{
            top: hole.top,
            left: hole.left,
            width: hole.width,
            height: hole.height,
          }}
          aria-hidden
        />
      )}

      {tip && (
        <div
          className={`onboard-tip placement-${tip.placement}`}
          style={{ top: tip.top, left: tip.left }}
        >
          <div className="onboard-tip-progress">
            Шаг {index + 1} из {steps.length}
          </div>
          <h2 id="onboard-title" className="onboard-tip-title">
            {step.title}
          </h2>
          <p className="onboard-tip-body">
            {missingTarget && step.route === "client-workspace"
              ? "Сначала привяжите клиента (шаг с «Подключить») — тогда откроется его кабинет с этой кнопкой. Пока можно продолжить тур."
              : step.body}
          </p>

          <div className="onboard-tip-actions">
            <button type="button" className="btn btn-ghost onboard-skip" onClick={finish}>
              Мне не нужно обучение
            </button>
            <div className="onboard-nav">
              {index > 0 && (
                <button type="button" className="btn btn-ghost" onClick={back}>
                  Назад
                </button>
              )}
              <button type="button" className="btn btn-accent onboard-next" onClick={next}>
                {isLast ? "Начать работу" : "Далее"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>,
    document.body
  );
}
