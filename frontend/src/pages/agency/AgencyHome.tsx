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
  bitrix_company_id?: string;
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
  const [companyDrafts, setCompanyDrafts] = useState<Record<number, string>>({});
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

  function companyValueFor(portalId: number, link: LinkRow, binding?: DealBinding) {
    return (
      companyDrafts[portalId] ??
      binding?.bitrix_company_id ??
      link.bitrix_company_id ??
      ""
    );
  }

  async function findDealByCompany(portalId: number, link: LinkRow) {
    if (!token) return;
    const companyId = companyValueFor(portalId, link, bindingByPortal.get(portalId)).trim();
    if (!companyId) {
      setError("Укажите ID компании Bitrix");
      return;
    }
    setDealBusyId(portalId);
    setError(null);
    try {
      await api(
        "/api/deal-bindings/",
        {
          method: "POST",
          body: JSON.stringify({
            client_portal_id: portalId,
            bitrix_company_id: companyId,
          }),
        },
        token
      );
      toast.show("Открытая сделка найдена в воронке «Сопровождение»", "Сделка привязана");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось найти сделку");
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
      setCompanyDrafts((prev) => {
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
          <p className="page-sub">Порталы Bitrix и сделки сопровождения</p>
        </div>
        <div className="stat-pill">
          <span className="stat-pill-value">{links.length}</span>
          <span className="stat-pill-label">подключено</span>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      <FlashToast message={toast.message} title={toast.title} leaving={toast.leaving} />

      {available.length > 0 ? (
        <section className="connect-card">
          <div className="connect-head">
            <h2 className="section-title">Подключить</h2>
          </div>
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
        </section>
      ) : null}

      <section className="linked-section" data-tour="tour-connect-client">
        <div className="linked-head">
          <h2 className="section-title">Ваши клиенты</h2>
        </div>

        {links.length === 0 ? (
          <div className="empty-linked">
            <p className="empty-linked-title">Пока пусто</p>
            <p className="muted empty-linked-sub">
              Клиент ставит Nextgen на своём портале — он появится здесь для подключения.
            </p>
          </div>
        ) : (
          <div className="linked-grid">
            {links.map((link) => {
              const p = link.client_portal;
              const title = p.name || p.domain;
              const binding = bindingByPortal.get(p.id);
              const companyValue = companyValueFor(p.id, link, binding);
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
                    {hasDeal ? (
                      <div className="deal-bind-status">
                        <div className="deal-bind-status-text">
                          <span className="deal-bind-kicker">Сделка сопровождения</span>
                          <strong className="deal-bind-deal-name">
                            {binding?.deal_title || `Сделка #${binding?.deal_id}`}
                          </strong>
                          {binding?.deal_id ? (
                            <span className="deal-bind-deal-id">#{binding.deal_id}</span>
                          ) : null}
                        </div>
                        {(binding?.paid_hours != null || binding?.remaining_hours != null) && (
                          <div className="deal-hours" aria-label="Часы по сделке">
                            {binding.paid_hours != null ? (
                              <div className="deal-hours-cell">
                                <span className="deal-hours-label">Оплачено</span>
                                <span className="deal-hours-value">{binding.paid_hours}</span>
                                <span className="deal-hours-unit">ч</span>
                              </div>
                            ) : null}
                            {binding.remaining_hours != null ? (
                              <div className="deal-hours-cell is-remaining">
                                <span className="deal-hours-label">Остаток</span>
                                <span className="deal-hours-value">{binding.remaining_hours}</span>
                                <span className="deal-hours-unit">ч</span>
                              </div>
                            ) : null}
                          </div>
                        )}
                      </div>
                    ) : (
                      <p className="deal-bind-hint">ID компании из CRM агентства</p>
                    )}

                    <div className="deal-bind-form">
                      <label className="deal-bind-label" htmlFor={`company-${p.id}`}>
                        ID компании
                      </label>
                      <div className="deal-bind-row">
                        <input
                          id={`company-${p.id}`}
                          className="deal-bind-input"
                          inputMode="numeric"
                          placeholder="Напр. 40"
                          value={companyValue}
                          onChange={(e) =>
                            setCompanyDrafts((prev) => ({ ...prev, [p.id]: e.target.value }))
                          }
                          disabled={dealBusy}
                        />
                        <button
                          type="button"
                          className="btn btn-accent"
                          disabled={dealBusy || !companyValue.trim()}
                          onClick={() => void findDealByCompany(p.id, link)}
                        >
                          {dealBusy ? "…" : hasDeal ? "Обновить" : "Найти"}
                        </button>
                      </div>
                      {hasDeal ? (
                        <div className="deal-bind-actions">
                          <button
                            type="button"
                            className="deal-bind-linkbtn"
                            disabled={dealBusy}
                            onClick={() => void refreshDealHours(p.id)}
                          >
                            Обновить часы
                          </button>
                          <button
                            type="button"
                            className="deal-bind-linkbtn is-danger"
                            disabled={dealBusy}
                            onClick={() => void clearDealBinding(p.id)}
                          >
                            Снять привязку
                          </button>
                        </div>
                      ) : null}
                    </div>
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
