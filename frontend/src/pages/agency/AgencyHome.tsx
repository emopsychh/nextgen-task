import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrapList, type DealBinding, type Portal } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { FlashToast } from "../../components/FlashToast";
import { useFlashToast } from "../../hooks/useFlashToast";
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
  const [dealDrafts, setDealDrafts] = useState<Record<number, string>>({});
  const [dealBusyId, setDealBusyId] = useState<number | null>(null);

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

  async function load() {
    if (!token) return;
    const [linkData, portalData, dealData] = await Promise.all([
      api<LinkRow[] | { results: LinkRow[] }>("/api/portal-links/", {}, token),
      api<Portal[] | { results: Portal[] }>("/api/portals/", {}, token),
      api<DealBinding[] | { results: DealBinding[] }>("/api/deal-bindings/", {}, token),
    ]);
    setLinks(unwrapList(linkData));
    setPortals(unwrapList(portalData).filter((p) => p.role === "client"));
    setBindings(unwrapList(dealData));
  }

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : "Ошибка загрузки"));
  }, [token]);

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

  async function saveDealBinding(portalId: number) {
    if (!token) return;
    const dealId = (dealDrafts[portalId] ?? bindingByPortal.get(portalId)?.deal_id ?? "").trim();
    if (!dealId) {
      setError("Укажите ID сделки Bitrix");
      return;
    }
    setDealBusyId(portalId);
    setError(null);
    try {
      await api(
        "/api/deal-bindings/",
        {
          method: "POST",
          body: JSON.stringify({ client_portal_id: portalId, deal_id: dealId }),
        },
        token
      );
      toast.show("Сообщения о закрытых задачах пойдут в эту сделку", "Сделка привязана");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось привязать сделку");
    } finally {
      setDealBusyId(null);
    }
  }

  async function clearDealBinding(portalId: number) {
    if (!token) return;
    const binding = bindingByPortal.get(portalId);
    if (!binding) return;
    setDealBusyId(portalId);
    setError(null);
    try {
      await api(`/api/deal-bindings/${binding.id}/`, { method: "DELETE" }, token);
      setDealDrafts((prev) => {
        const next = { ...prev };
        delete next[portalId];
        return next;
      });
      toast.show("Привязка снята");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось снять привязку");
    } finally {
      setDealBusyId(null);
    }
  }

  async function refreshDealHours(portalId: number) {
    if (!token) return;
    const binding = bindingByPortal.get(portalId);
    if (!binding) return;
    setDealBusyId(portalId);
    setError(null);
    try {
      await api(`/api/deal-bindings/${binding.id}/refresh-hours/`, { method: "POST" }, token);
      toast.show("Часы обновлены из сделки");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось обновить часы");
    } finally {
      setDealBusyId(null);
    }
  }

  return (
    <div className="clients-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Клиенты</h1>
          <p className="page-sub">Подключите портал — и ведите проекты прямо из Nextgen manager.</p>
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
            <strong>Клиент ставит приложение</strong>
            <p>Локальное приложение на своём портале Bitrix</p>
          </div>
        </div>
        <div className="how-step">
          <span className="how-num">3</span>
          <div>
            <strong>Привяжите здесь</strong>
            <p>Клиент появится в панели слева</p>
          </div>
        </div>
      </section>

      <section className="connect-panel" data-tour="tour-connect-client">
        <div className="connect-panel-head">
          <div>
            <h2 className="section-title">Добавить клиента</h2>
            <p className="muted">Выберите портал из доступных и подтвердите.</p>
          </div>
        </div>

        {available.length === 0 ? (
          <div className="empty-connect">
            <div className="empty-connect-icon" aria-hidden>
              +
            </div>
            <h3>Пока нечего привязывать</h3>
            <p className="muted">
              Когда клиент установит приложение и выберет роль «Клиент», его портал появится здесь.
            </p>
          </div>
        ) : (
          <form onSubmit={linkClient} className="connect-form">
            <div className="portal-pick-grid">
              {available.map((p) => {
                const selected = clientId === String(p.id);
                return (
                  <button
                    key={p.id}
                    type="button"
                    className={`portal-pick${selected ? " selected" : ""}`}
                    onClick={() =>
                      setClientId((prev) => (prev === String(p.id) ? "" : String(p.id)))
                    }
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
                {busy ? "Подключаем…" : "Подключить клиента"}
              </button>
            </div>
          </form>
        )}
      </section>

      <section className="linked-section">
        <div className="linked-head">
          <h2 className="section-title">Ваши клиенты</h2>
          <p className="muted">
            Откройте проекты или укажите ID сделки «Сопровождение» — туда уйдут сообщения о закрытых
            задачах.
          </p>
        </div>

        {links.length === 0 ? (
          <div className="empty-linked">
            <p className="muted">Ещё никого нет — добавьте первого клиента выше.</p>
          </div>
        ) : (
          <div className="linked-grid">
            {links.map((link) => {
              const p = link.client_portal;
              const title = p.name || p.domain;
              const binding = bindingByPortal.get(p.id);
              const dealValue = dealDrafts[p.id] ?? binding?.deal_id ?? "";
              const dealBusy = dealBusyId === p.id;
              return (
                <div
                  key={link.id}
                  className={`linked-card${enteringPortalId === p.id ? " is-entering" : ""}`}
                >
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

                  <div className="deal-bind">
                    <label className="deal-bind-label" htmlFor={`deal-${p.id}`}>
                      Сделка сопровождения
                    </label>
                    {binding?.deal_title ? (
                      <span className="deal-bind-title muted">{binding.deal_title}</span>
                    ) : null}
                    {binding &&
                    (binding.paid_hours != null || binding.remaining_hours != null) ? (
                      <span className="deal-bind-hours">
                        {binding.paid_hours != null ? (
                          <span>Оплачено: {binding.paid_hours} ч</span>
                        ) : null}
                        {binding.paid_hours != null && binding.remaining_hours != null
                          ? " · "
                          : null}
                        {binding.remaining_hours != null ? (
                          <span>Остаток: {binding.remaining_hours} ч</span>
                        ) : null}
                      </span>
                    ) : null}
                    <div className="deal-bind-row">
                      <input
                        id={`deal-${p.id}`}
                        className="deal-bind-input"
                        inputMode="numeric"
                        placeholder="ID сделки, напр. 152"
                        value={dealValue}
                        onChange={(e) =>
                          setDealDrafts((prev) => ({ ...prev, [p.id]: e.target.value }))
                        }
                        disabled={dealBusy}
                      />
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={dealBusy || !dealValue.trim()}
                        onClick={() => void saveDealBinding(p.id)}
                      >
                        {dealBusy ? "…" : "Сохранить"}
                      </button>
                      {binding ? (
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={dealBusy}
                          onClick={() => void refreshDealHours(p.id)}
                          title="Обновить часы из Bitrix"
                        >
                          Часы
                        </button>
                      ) : null}
                      {binding ? (
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={dealBusy}
                          onClick={() => void clearDealBinding(p.id)}
                        >
                          Снять
                        </button>
                      ) : null}
                    </div>
                  </div>

                  <button
                    type="button"
                    className="linked-unlink"
                    title="Отключить клиента"
                    onClick={() => setPendingUnlink({ linkId: link.id, name: title })}
                  >
                    Отключить
                  </button>
                </div>
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
