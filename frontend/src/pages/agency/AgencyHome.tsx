import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, isAbortError, unwrapList, type DealBinding, type Portal } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { DealHoursCard } from "../../components/DealHoursCard";
import { DealPickModal, type DealCandidate } from "../../components/DealPickModal";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { hueFromId, initialsFromLabel } from "../../lib/portalUi";
import { readPortalCache, writePortalCache } from "../../lib/portalSessionCache";

const CACHE_AGENCY_LINKS = "agency-links";
const CACHE_AGENCY_PORTALS = "agency-portals";
const CACHE_AGENCY_BINDINGS = "agency-bindings";

type LinkRow = {
  id: number;
  client_portal: Portal;
};

type PendingUnlink = {
  linkId: number;
  name: string;
};

type DealPickerState = {
  portalId: number;
  portalName: string;
  portalDomain: string;
  currentDealId?: string | null;
};

function initials(portal: Portal): string {
  return initialsFromLabel(portal.name || portal.domain || "?");
}

export function AgencyHome() {
  const { token, portal } = useAuth();
  const agencyId = portal?.id || 0;
  const toast = useFlashToast();
  const [links, setLinks] = useState<LinkRow[]>(
    () => readPortalCache<LinkRow[]>(CACHE_AGENCY_LINKS, agencyId) || []
  );
  const [portals, setPortals] = useState<Portal[]>(
    () => readPortalCache<Portal[]>(CACHE_AGENCY_PORTALS, agencyId) || []
  );
  const [bindings, setBindings] = useState<DealBinding[]>(
    () => readPortalCache<DealBinding[]>(CACHE_AGENCY_BINDINGS, agencyId) || []
  );
  const [linksLoading, setLinksLoading] = useState(links.length === 0);
  const [portalsLoading, setPortalsLoading] = useState(portals.length === 0);
  const [clientId, setClientId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [enteringPortalId, setEnteringPortalId] = useState<number | null>(null);
  const [pendingUnlink, setPendingUnlink] = useState<PendingUnlink | null>(null);
  const [unlinking, setUnlinking] = useState(false);
  const [dealBusyId, setDealBusyId] = useState<number | null>(null);
  const [dealPicker, setDealPicker] = useState<DealPickerState | null>(null);
  const [dealCandidates, setDealCandidates] = useState<DealCandidate[]>([]);
  const [dealPickLoading, setDealPickLoading] = useState(false);
  const [dealPickError, setDealPickError] = useState<string | null>(null);
  const [dealConfirmId, setDealConfirmId] = useState<string | null>(null);
  const bindingsRef = useRef(bindings);
  bindingsRef.current = bindings;

  const available = useMemo(
    () => portals.filter((p) => !links.some((l) => l.client_portal.id === p.id)),
    [portals, links]
  );

  const bindingByPortal = useMemo(() => {
    const map = new Map<number, DealBinding>();
    for (const b of bindings) {
      if (b.is_active) map.set(b.client_portal.id, b);
    }
    return map;
  }, [bindings]);

  const load = useCallback(async () => {
    if (!token || !agencyId) return;
    setLinksLoading(true);
    setPortalsLoading(true);

    // These endpoints are independent. Apply each response immediately so a
    // slower deal query cannot hold the client list at "0".
    const linksRequest = api<LinkRow[] | { results: LinkRow[] }>(
      "/api/portal-links/",
      {},
      token
    ).then((data) => {
      const list = unwrapList(data);
      setLinks(list);
      writePortalCache(CACHE_AGENCY_LINKS, agencyId, list);
      setLinksLoading(false);
    });
    const portalsRequest = api<Portal[] | { results: Portal[] }>(
      "/api/portals/?linkable=1",
      {},
      token
    ).then((data) => {
      const list = unwrapList(data).filter((p) => p.role === "client");
      setPortals(list);
      writePortalCache(CACHE_AGENCY_PORTALS, agencyId, list);
      setPortalsLoading(false);
    });
    const bindingsRequest = api<DealBinding[] | { results: DealBinding[] }>(
      "/api/deal-bindings/?is_active=true",
      {},
      token
    ).then((data) => {
      const list = unwrapList(data).filter(
        (binding) => binding.agency_portal === agencyId && binding.is_active
      );
      setBindings(list);
      writePortalCache(CACHE_AGENCY_BINDINGS, agencyId, list);
    });

    const results = await Promise.allSettled([
      linksRequest,
      portalsRequest,
      bindingsRequest,
    ]);
    setLinksLoading(false);
    setPortalsLoading(false);
    const failed = results.find(
      (result): result is PromiseRejectedResult => result.status === "rejected"
    );
    if (failed) throw failed.reason;
  }, [token, agencyId]);

  useEffect(() => {
    if (!agencyId) return;
    setLinks(readPortalCache<LinkRow[]>(CACHE_AGENCY_LINKS, agencyId) || []);
    setPortals(readPortalCache<Portal[]>(CACHE_AGENCY_PORTALS, agencyId) || []);
    setBindings(readPortalCache<DealBinding[]>(CACHE_AGENCY_BINDINGS, agencyId) || []);
  }, [agencyId]);

  const refreshAllDealHours = useCallback(async (signal?: AbortSignal) => {
    if (!token) return;
    const active = bindingsRef.current.filter((b) => b.is_active);
    if (!active.length) return;
    // Stagger CRM calls so the home page does not storm Bitrix.
    for (const b of active) {
      if (signal?.aborted) return;
      try {
        await api(
          `/api/deal-bindings/${b.id}/refresh-hours/`,
          { method: "POST", signal },
          token
        );
      } catch (e) {
        if (isAbortError(e)) return;
        // ignore per-binding errors
      }
      if (signal?.aborted) return;
      await new Promise<void>((resolve) => {
        if (signal?.aborted) {
          resolve();
          return;
        }
        const t = window.setTimeout(resolve, 450);
        signal?.addEventListener(
          "abort",
          () => {
            window.clearTimeout(t);
            resolve();
          },
          { once: true }
        );
      });
    }
    if (signal?.aborted) return;
    try {
      const dealData = await api<DealBinding[] | { results: DealBinding[] }>(
        "/api/deal-bindings/?is_active=true",
        { signal },
        token
      );
      if (!signal?.aborted) {
        const list = unwrapList(dealData).filter(
          (binding) => binding.agency_portal === agencyId && binding.is_active
        );
        setBindings(list);
        writePortalCache(CACHE_AGENCY_BINDINGS, agencyId, list);
      }
    } catch (e) {
      if (!isAbortError(e)) undefined;
    }
  }, [token, agencyId]);

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : "Ошибка загрузки"));
  }, [load]);

  useEffect(() => {
    if (!token) return;
    const ac = new AbortController();
    let lastRefreshAt = 0;
    const COOLDOWN_MS = 20000;

    async function tick(force = false) {
      if (ac.signal.aborted || document.visibilityState === "hidden") return;
      if (!bindingsRef.current.some((b) => b.is_active)) return;
      const now = Date.now();
      if (!force && now - lastRefreshAt < COOLDOWN_MS) return;
      lastRefreshAt = now;
      try {
        await refreshAllDealHours(ac.signal);
      } catch (e) {
        if (!isAbortError(e)) undefined;
      }
    }

    const interval = window.setInterval(() => void tick(true), 45000);
    const onFocus = () => void tick(false);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    // First refresh after paint — not immediately on mount.
    const first = window.setTimeout(() => void tick(true), 6000);

    return () => {
      ac.abort();
      window.clearInterval(interval);
      window.clearTimeout(first);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [token, refreshAllDealHours]);

  async function linkClient(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !clientId) return;
    const portalId = Number(clientId);
    setBusy(true);
    setError(null);
    try {
      await api(
        "/api/portal-links/",
        { method: "POST", body: JSON.stringify({ client_portal_id: portalId }) },
        token
      );
      setClientId("");
      setEnteringPortalId(portalId);
      toast.show("Он появился в панели слева", "Клиент добавлен");
      await load();
      window.dispatchEvent(
        new CustomEvent("clients-updated", { detail: { addedPortalId: portalId } })
      );
      window.setTimeout(() => setEnteringPortalId(null), 900);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось привязать");
    } finally {
      setBusy(false);
    }
  }

  async function confirmUnlink() {
    if (!token || !pendingUnlink) return;
    setUnlinking(true);
    setError(null);
    try {
      await api(`/api/portal-links/${pendingUnlink.linkId}/`, { method: "DELETE" }, token);
      setPendingUnlink(null);
      await load();
      window.dispatchEvent(new Event("clients-updated"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отключить");
    } finally {
      setUnlinking(false);
    }
  }

  async function openDealPicker(portal: Portal, currentDealId?: string | null) {
    if (!token) return;
    const portalName = portal.name || portal.domain;
    setError(null);
    setDealPickError(null);
    setDealCandidates([]);
    setDealConfirmId(null);
    setDealBusyId(portal.id);
    setDealPicker({
      portalId: portal.id,
      portalName,
      portalDomain: portal.domain,
      currentDealId: currentDealId || null,
    });
    setDealPickLoading(true);
    try {
      const data = await api<{ results: DealCandidate[] }>(
        `/api/deal-bindings/candidates/?client_portal_id=${portal.id}`,
        {},
        token
      );
      setDealCandidates(data.results || []);
    } catch (err) {
      setDealPickError(err instanceof Error ? err.message : "Не удалось загрузить сделки");
    } finally {
      setDealPickLoading(false);
      setDealBusyId(null);
    }
  }

  function closeDealPicker() {
    if (dealConfirmId) return;
    setDealPicker(null);
    setDealCandidates([]);
    setDealPickError(null);
  }

  async function bindSelectedDeal(dealId: string) {
    if (!token || !dealPicker) return;
    setDealConfirmId(dealId);
    setDealPickError(null);
    try {
      await api(
        "/api/deal-bindings/",
        {
          method: "POST",
          body: JSON.stringify({
            client_portal_id: dealPicker.portalId,
            deal_id: dealId,
          }),
        },
        token
      );
      toast.show("Пакет часов этого клиента обновлён", "Сделка привязана");
      setDealPicker(null);
      setDealCandidates([]);
      await load();
    } catch (err) {
      setDealPickError(err instanceof Error ? err.message : "Не удалось привязать сделку");
    } finally {
      setDealConfirmId(null);
    }
  }

  return (
    <div className="clients-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Клиенты</h1>
          <p className="page-sub">Порталы Bitrix и сделки сопровождения</p>
        </div>
        <div className="stat-pill">
          <span className="stat-pill-value">
            {linksLoading && links.length === 0 ? "…" : links.length}
          </span>
          <span className="stat-pill-label">подключено</span>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      <section className="how-it-works" aria-label="Как это работает">
        <div className="how-step">
          <span className="how-num">1</span>
          <div>
            <strong>Установите приложение</strong>
            <p>На портале клиента в Битрикс24</p>
          </div>
        </div>
        <div className="how-step">
          <span className="how-num">2</span>
          <div>
            <strong>Подключите клиента здесь</strong>
            <p>Выберите портал из списка ниже</p>
          </div>
        </div>
        <div className="how-step">
          <span className="how-num">3</span>
          <div>
            <strong>Выберите сделку</strong>
            <p>По ссылке на портал в компании CRM</p>
          </div>
        </div>
      </section>

      <section className="connect-card" data-tour="tour-connect-client">
        <div className="connect-head">
          <h2 className="section-title">Подключить клиента</h2>
        </div>
        {available.length > 0 ? (
          <form className="connect-form" onSubmit={linkClient}>
            <div className="portal-pick-list" role="listbox" aria-label="Клиентские порталы">
              {available.map((p) => {
                const selected = clientId === String(p.id);
                return (
                  <button
                    key={p.id}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    className={`portal-pick${selected ? " is-selected" : ""}`}
                    onClick={() => setClientId(String(p.id))}
                  >
                    <span
                      className="portal-pick-avatar"
                      style={{ background: hueFromId(p.id) }}
                    >
                      {initials(p)}
                    </span>
                    <span className="portal-pick-meta">
                      <strong>{p.name || p.domain}</strong>
                      <span className="muted">{p.domain}</span>
                    </span>
                    <span className={`portal-pick-check${selected ? " on" : ""}`} aria-hidden>
                      {selected ? "✓" : ""}
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="connect-actions">
              <button className="btn btn-primary" disabled={busy || !clientId}>
                {busy ? "Подключаем…" : "Подключить"}
              </button>
            </div>
          </form>
        ) : portalsLoading ? (
          <p className="connect-empty muted">Загружаем доступные порталы…</p>
        ) : (
          <p className="connect-empty muted">Клиентов для подключения нет</p>
        )}
      </section>

      <section className="linked-section">
        <div className="linked-head">
          <h2 className="section-title">Ваши клиенты</h2>
        </div>

        {linksLoading && links.length === 0 ? (
          <div className="empty-linked">
            <p className="muted">Загружаем клиентов…</p>
          </div>
        ) : links.length === 0 ? (
          <div className="empty-linked">
            <p className="muted">Пока никого нет</p>
          </div>
        ) : (
          <div className="linked-grid">
            {links.map((link) => {
              const p = link.client_portal;
              const title = p.name || p.domain;
              const binding = bindingByPortal.get(p.id);
              const dealBusy = dealBusyId === p.id;
              const hasDeal = Boolean(binding);
              return (
                <article
                  key={link.id}
                  className={`linked-card${enteringPortalId === p.id ? " is-entering" : ""}${hasDeal ? " has-deal" : ""}`}
                >
                  <header className="linked-card-top">
                    <Link to={`/portals/${p.id}`} className="linked-card-main">
                      <span
                        className="linked-avatar"
                        style={{ background: hueFromId(p.id) }}
                      >
                        {initials(p)}
                      </span>
                      <div className="linked-meta">
                        <strong>{title}</strong>
                        <span className="muted">{p.domain}</span>
                      </div>
                    </Link>
                    <button
                      type="button"
                      className="linked-unlink"
                      title="Отключить клиента"
                      onClick={() => setPendingUnlink({ linkId: link.id, name: title })}
                    >
                      Отключить
                    </button>
                  </header>

                  <div className="deal-bind">
                    {hasDeal && binding ? (
                      <div className="deal-bind-status">
                        <div className="deal-bind-status-text">
                          <span className="deal-bind-kicker">Сделка сопровождения</span>
                          <strong className="deal-bind-deal-name">
                            {binding.deal_title || `Сделка #${binding.deal_id}`}
                          </strong>
                          {binding.deal_id ? (
                            <span className="deal-bind-deal-id">#{binding.deal_id}</span>
                          ) : null}
                        </div>
                        <DealHoursCard binding={binding} audience="agency" />
                        <button
                          type="button"
                          className="btn btn-ghost deal-bind-change"
                          disabled={dealBusy}
                          onClick={() => void openDealPicker(p, binding.deal_id)}
                        >
                          {dealBusy ? "Загрузка…" : "Сменить сделку"}
                        </button>
                      </div>
                    ) : (
                      <>
                        <p className="deal-bind-hint muted">
                          Покажем сделки компании, где в CRM указана ссылка на этот портал
                        </p>
                        <button
                          type="button"
                          className="btn btn-accent"
                          disabled={dealBusy}
                          onClick={() => void openDealPicker(p)}
                        >
                          {dealBusy ? "Загрузка…" : "Выбрать сделку"}
                        </button>
                      </>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={Boolean(pendingUnlink)}
        danger
        title={pendingUnlink ? `Отключить «${pendingUnlink.name}»?` : "Отключить клиента?"}
        description="Проекты и задачи останутся в базе, но портал исчезнет из левой панели. Подключить снова можно в любой момент."
        confirmLabel={unlinking ? "Отключаем…" : "Отключить"}
        cancelLabel="Оставить"
        onCancel={() => {
          if (!unlinking) setPendingUnlink(null);
        }}
        onConfirm={() => void confirmUnlink()}
      />

      <DealPickModal
        open={Boolean(dealPicker)}
        portalName={dealPicker?.portalName || ""}
        portalDomain={dealPicker?.portalDomain || ""}
        loading={dealPickLoading}
        error={dealPickError}
        deals={dealCandidates}
        currentDealId={dealPicker?.currentDealId}
        confirmingId={dealConfirmId}
        onClose={closeDealPicker}
        onSelect={(dealId) => void bindSelectedDeal(dealId)}
      />
    </div>
  );
}
