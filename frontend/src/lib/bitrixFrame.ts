/** Bitrix24 iframe helpers — expand placement without resize loops. */

type Bx24Api = {
  init: (cb: () => void) => void;
  fitWindow: (cb?: () => void) => void;
  resizeWindow: (width: number, height: number, cb?: () => void) => void;
  getScrollSize?: () => { scrollWidth: number; scrollHeight: number };
};

declare global {
  interface Window {
    BX24?: Bx24Api;
  }
}

let started = false;
let busy = false;
let fitTimer: number | undefined;

function inBitrixFrame(): boolean {
  try {
    if (window !== window.parent) return true;
  } catch {
    return true; // cross-origin parent ⇒ embedded
  }
  const params = new URLSearchParams(window.location.search);
  return Boolean(
    params.get("AUTH_ID") ||
      params.get("auth_id") ||
      params.get("DOMAIN") ||
      params.get("domain") ||
      params.get("APP_SID")
  );
}

function markEmbedded() {
  document.documentElement.classList.add("bx-frame");
}

/**
 * Ask Bitrix to size the iframe to content.
 * No ResizeObserver — that loops with resizeWindow and freezes the UI.
 */
export function resizeBitrixFrame() {
  const bx = window.BX24;
  if (!bx || busy) return;
  busy = true;
  try {
    markEmbedded();
    if (typeof bx.fitWindow === "function") {
      bx.fitWindow();
    }
    // Only rescue a collapsed placement (~file picker broken under ~400px)
    const h = window.innerHeight || 0;
    if (h > 0 && h < 420 && typeof bx.resizeWindow === "function") {
      const w = Math.max(window.innerWidth || 0, 960);
      bx.resizeWindow(w, 680);
    }
  } catch {
    // opened outside Bitrix
  } finally {
    window.setTimeout(() => {
      busy = false;
    }, 250);
  }
}

export function scheduleBitrixFit(delayMs = 120) {
  if (fitTimer != null) window.clearTimeout(fitTimer);
  fitTimer = window.setTimeout(() => resizeBitrixFrame(), delayMs);
}

function loadBx24Script(): Promise<Bx24Api | null> {
  if (window.BX24) return Promise.resolve(window.BX24);
  return new Promise((resolve) => {
    const existing = document.querySelector<HTMLScriptElement>("script[data-bx24]");
    if (existing) {
      const done = () => resolve(window.BX24 || null);
      existing.addEventListener("load", done, { once: true });
      existing.addEventListener("error", () => resolve(null), { once: true });
      if (window.BX24) resolve(window.BX24);
      return;
    }
    const s = document.createElement("script");
    s.src = "https://api.bitrix24.com/api/v1/";
    s.async = true;
    s.dataset.bx24 = "1";
    s.onload = () => resolve(window.BX24 || null);
    s.onerror = () => resolve(null);
    document.head.appendChild(s);
  });
}

/** Fire-and-forget; never blocks React mount. */
export function initBitrixFrame(): void {
  if (started) {
    scheduleBitrixFit();
    return;
  }
  started = true;

  if (!inBitrixFrame()) {
    return;
  }

  markEmbedded();

  void loadBx24Script().then((bx) => {
    if (!bx || typeof bx.init !== "function") return;
    bx.init(() => {
      scheduleBitrixFit(50);
      scheduleBitrixFit(400);
    });
  });
}
