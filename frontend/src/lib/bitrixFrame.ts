/** Bitrix24 iframe helpers — expand placement frame so UI/file picker work. */

type Bx24Api = {
  init: (cb: () => void) => void;
  fitWindow: (cb?: () => void) => void;
  resizeWindow: (width: number, height: number, cb?: () => void) => void;
  getScrollSize?: () => { scrollWidth: number; scrollHeight: number };
  isAdmin?: () => boolean;
};

declare global {
  interface Window {
    BX24?: Bx24Api;
  }
}

let started = false;
let resizeTimer: number | undefined;

const MIN_FRAME_W = 1100;
const MIN_FRAME_H = 920;

function measuredSize(): { w: number; h: number } {
  const bx = window.BX24;
  try {
    if (bx?.getScrollSize) {
      const s = bx.getScrollSize();
      return {
        w: Number(s.scrollWidth) || MIN_FRAME_W,
        h: Number(s.scrollHeight) || MIN_FRAME_H,
      };
    }
  } catch {
    // ignore
  }
  const doc = document.documentElement;
  const body = document.body;
  return {
    w: Math.max(doc?.scrollWidth || 0, body?.scrollWidth || 0, window.innerWidth || 0, MIN_FRAME_W),
    h: Math.max(doc?.scrollHeight || 0, body?.scrollHeight || 0, MIN_FRAME_H),
  };
}

export function resizeBitrixFrame() {
  const bx = window.BX24;
  if (!bx || typeof bx.resizeWindow !== "function") return;
  try {
    const { w, h } = measuredSize();
    // Always request a usable frame — height:100% layouts report tiny scrollHeight
    // inside a collapsed Bitrix placement iframe (breaks file picker).
    bx.resizeWindow(Math.max(w, MIN_FRAME_W), Math.max(h, MIN_FRAME_H));
  } catch {
    // Outside Bitrix iframe — ignore
  }
}

function scheduleResize() {
  if (resizeTimer != null) window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => resizeBitrixFrame(), 80);
}

function loadBx24Script(): Promise<void> {
  if (window.BX24) return Promise.resolve();
  return new Promise((resolve) => {
    const existing = document.querySelector<HTMLScriptElement>("script[data-bx24]");
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      if (window.BX24) resolve();
      return;
    }
    const s = document.createElement("script");
    s.src = "https://api.bitrix24.com/api/v1/";
    s.async = true;
    s.dataset.bx24 = "1";
    s.onload = () => resolve();
    s.onerror = () => resolve();
    document.head.appendChild(s);
  });
}

/**
 * Init BX24 and keep iframe tall enough for the app (and file dialogs).
 * Safe no-op when opened outside Bitrix.
 */
export async function initBitrixFrame(): Promise<void> {
  if (started) {
    scheduleResize();
    return;
  }
  started = true;

  await loadBx24Script();
  const bx = window.BX24;
  if (!bx || typeof bx.init !== "function") {
    return;
  }

  bx.init(() => {
    resizeBitrixFrame();
    try {
      const ro = new ResizeObserver(() => scheduleResize());
      if (document.body) ro.observe(document.body);
      const root = document.getElementById("root");
      if (root) ro.observe(root);
    } catch {
      // older browsers
    }
    window.addEventListener("resize", scheduleResize);
    window.setTimeout(resizeBitrixFrame, 300);
    window.setTimeout(resizeBitrixFrame, 1200);
  });
}
