import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrapList, type DealBinding, type Portal } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
import { asPackageHours, formatPackageHours } from "../../lib/format";
import { hueFromId, initialsFromLabel } from "../../lib/portalUi";

type LinkRow = {
  id: number;
  client_portal: Portal;
};

type PendingUnlink = {
  linkId: number;
  name: string;
};

function initials(portal: Portal): string {
  return initialsFromLabel(portal.name || portal.domain || "?");
}

function DealHoursBlock({ binding }: { binding: DealBinding }) {
  const paid = asPackageHours(binding.paid_hours);
  const remaining = asPackageHours(binding.remaining_hours);
  const credit = asPackageHours(binding.hours_credit);
  const won = Boolean(binding.is_won);
  if (paid == null && remaining == null && !(credit != null && credit > 0)) return null;

  const packageSize = paid != null && paid > 0 ? paid : null;
  const remainPct =
    packageSize != null && remaining != null
      ? Math.max(0, Math.min(100, (remaining / packageSize) * 100))
      : null;
  const over = remaining != null && remaining <= 0 && !won;

  return (
    <div
      className={`deal-hours-scale${over ? " is-over" : ""}${won ? " is-won" : ""}`}
      aria-label="Часы по сделке сопровождения"
    >
      {won ? (
        <p className="deal-hours-won-label">Сделка завершена успешно</p>
      ) : null}

      {paid != null && !won ? (
        <p className="deal-hours-paid">
          Оплачено <strong>{formatPackageHours(paid)}</strong>
        </p>
      ) : null}

      {remaining != null && !won ? (
        <div className="deal-hours-remain-row">
          <span className="deal-hours-remain-label">Осталось</span>
          <strong className="deal-hours-remain-value">{formatPackageHours(remaining)}</strong>
        </div>
      ) : null}

      {remainPct != null && !won ? (
        <div
          className="deal-hours-track"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(remainPct)}
          aria-label="Оставшиеся часы пакета"
        >
          <div
            className="deal-hours-fill"
            style={{
              width: `${Math.max(remainPct, remaining != null && remaining > 0 ? 1.5 : 0)}%`,
            }}
          />
        </div>
      ) : null}

      {credit != null && credit > 0 ? (
        <div className="deal-hours-credit">
          <span className="deal-hours-credit-kicker">Неиспользованный остаток</span>
          <strong className="deal-hours-credit-value">{formatPackageHours(credit)}</strong>
          <p className="deal-hours-credit-hint">
            {binding.hours_credit_source_title
              ? `После «${binding.hours_credit_source_title}». `
              : ""}
            Перейдут на следующую сделку сопровождения этого клиента.
          </p>
        </div>
      ) : null}
    </div>
  );
}

export function AgencyHome() {
  const { token } = useAuth();
  const toast = useFlashToast();
  const [links, setLinks] = useState<LinkRow[]>([]);
  const [portals, setPortals] = useState<Portal[]>([]);
  const [bindings, setBindings] = useState<DealBinding[]>([]);
  const [clientId, setClientId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [enteringPortalId, setEnteringPortalId] = useState<number | null>(null);
  const [pendingUnlink, setPendingUnlink] = useState<PendingUnlink | null>(null);
  const [unlinking, setUnlinking] = useState(false);
  const [dealBusyId, setDealBusyId] = useState<number | null>(null);
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
    if (!token) return;
    const [linkData, portalData, dealData] = await Promise.all([
      api<LinkRow[] | { results: LinkRow[] }>("/api/portal-links/", {}, token),
      api<Portal[] | { results: Portal[] }>("/api/portals/", {}, token),
      api<DealBinding[] | { results: DealBinding[] }>("/api/deal-bindings/", {}, token),
    ]);
    setLinks(unwrapList(linkData));
    setPortals(unwrapList(portalData).filter((p) => p.role === "client"));
    setBindings(unwrapList(dealData));
  }, [token]);

  const refreshAllDealHours = useCallback(async () => {
    if (!token) return;
    const active = bindingsRef.current.filter((b) => b.is_active);
    if (!active.length) return;
    await Promise.allSettled(
      active.map((b) =>
        api(`/api/deal-bindings/${b.id}/refresh-hours/`, { method: "POST" }, token)
      )
    );
    const dealData = await api<DealBinding[] | { results: DealBinding[] }>(
      "/api/deal-bindings/",
      {},
      token
    );
    setBindings(unwrapList(dealData));
  }, [token]);

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : "Ошибка загрузки"));
  }, [load]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;

    async function tick() {
      if (cancelled || document.visibilityState === "hidden") return;
      if (!bindingsRef.current.some((b) => b.is_active)) return;
      try {
        await refreshAllDealHours();
      } catch {
        // ignore background refresh errors
      }
    }

    const interval = window.setInterval(() => void tick(), 45000);
    const onFocus = () => void tick();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    const first = window.setTimeout(() => void tick(), 2500);

    return () => {
      cancelled = true;
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

  async function findDealByPortal(portalId: number) {
    if (!token) return;
    setDealBusyId(portalId);
    setError(null);
    try {
      await api(
        "/api/deal-bindings/",
        {
          method: "POST",
          body: JSON.stringify({ client_portal_id: portalId }),
        },
        token
      );
      toast.show("Сделка найдена по ссылке на портал в CRM", "Сделка привязана");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось найти сделку");
    } finally {
      setDealBusyId(null);
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
          <span className="stat-pill-value">{links.length}</span>
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
            <strong>Сделка подтянется сама</strong>
            <p>По полю «Ссылка на портал» в CRM</p>
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
        ) : (
          <p className="connect-empty muted">Клиентов для подключения нет</p>
        )}
      </section>

      <section className="linked-section">
        <div className="linked-head">
          <h2 className="section-title">Ваши клиенты</h2>
        </div>

        {links.length === 0 ? (
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
                    <Link to={`/portals/${p.id}/projects`} className="linked-card-main">
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
                        <DealHoursBlock binding={binding} />
                      </div>
                    ) : (
                      <>
                        <p className="deal-bind-hint muted">
                          Сделка ищется по полю «Ссылка на портал» в CRM
                        </p>
                        <button
                          type="button"
                          className="btn btn-accent"
                          disabled={dealBusy}
                          onClick={() => void findDealByPortal(p.id)}
                        >
                          {dealBusy ? "Ищем…" : "Найти сделку"}
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
    </div>
  );
}
