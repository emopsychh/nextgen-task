import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrapList, type DealBinding, type Portal } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { DealHoursCard } from "../../components/DealHoursCard";
import { FlashToast } from "../../components/FlashToast";
import { ModalPortal } from "../../components/ModalPortal";
import { useFlashToast } from "../../hooks/useFlashToast";
import { setPortalLabel } from "../../lib/portalLabelCache";
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

type HoursEditor = {
  portal: Portal;
  binding: DealBinding | null;
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
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [hoursEditor, setHoursEditor] = useState<HoursEditor | null>(null);
  const [hoursTitle, setHoursTitle] = useState("");
  const [hoursRate, setHoursRate] = useState("");
  const [hoursPackageRub, setHoursPackageRub] = useState("");
  const [hoursBalanceRub, setHoursBalanceRub] = useState("");
  const [hoursBusy, setHoursBusy] = useState(false);
  const [hoursError, setHoursError] = useState<string | null>(null);

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

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : "Ошибка загрузки"));
  }, [load]);

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

  function startRename(portal: Portal) {
    setError(null);
    setRenamingId(portal.id);
    setRenameDraft((portal.name || portal.domain || "").trim());
  }

  function cancelRename() {
    if (renaming) return;
    setRenamingId(null);
    setRenameDraft("");
  }

  async function saveRename() {
    if (!token || renamingId == null) return;
    const name = renameDraft.trim();
    if (!name) {
      setError("Укажите название клиента");
      return;
    }
    setRenaming(true);
    setError(null);
    try {
      const updated = await api<Portal>(
        `/api/portals/${renamingId}/`,
        { method: "PATCH", body: JSON.stringify({ name }) },
        token
      );
      const label = (updated.name || name).trim();
      setLinks((prev) => {
        const next = prev.map((link) =>
          link.client_portal.id === renamingId
            ? { ...link, client_portal: { ...link.client_portal, name: label } }
            : link
        );
        writePortalCache(CACHE_AGENCY_LINKS, agencyId, next);
        return next;
      });
      setPortalLabel(renamingId, label);
      window.dispatchEvent(new Event("clients-updated"));
      setRenamingId(null);
      setRenameDraft("");
      toast.show("Название обновлено", "Клиент переименован");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось переименовать");
    } finally {
      setRenaming(false);
    }
  }

  function openHoursEditor(client: Portal, binding: DealBinding | null) {
    setHoursError(null);
    setHoursEditor({ portal: client, binding });
    setHoursTitle(binding?.deal_title || client.name || client.domain || "");
    setHoursRate(
      binding?.hourly_rate_rub != null && binding.hourly_rate_rub !== ""
        ? String(binding.hourly_rate_rub)
        : ""
    );
    setHoursPackageRub(
      binding?.package_rub != null && binding.package_rub !== ""
        ? String(binding.package_rub)
        : ""
    );
    setHoursBalanceRub(
      binding?.balance_rub != null && binding.balance_rub !== ""
        ? String(binding.balance_rub)
        : binding?.package_rub != null && binding.package_rub !== ""
          ? String(binding.package_rub)
          : ""
    );
  }

  function closeHoursEditor() {
    if (hoursBusy) return;
    setHoursEditor(null);
    setHoursError(null);
  }

  const hoursPreview = (() => {
    const rate = Number(String(hoursRate).replace(",", "."));
    const packageRub = Number(String(hoursPackageRub).replace(",", "."));
    const balanceRub = Number(
      String(hoursBalanceRub || hoursPackageRub).replace(",", ".")
    );
    if (!(rate > 0) || !Number.isFinite(packageRub) || !Number.isFinite(balanceRub)) {
      return null;
    }
    return {
      paid: Math.round((packageRub / rate) * 100) / 100,
      remaining: Math.round((balanceRub / rate) * 100) / 100,
    };
  })();

  async function saveHoursPackage() {
    if (!token || !hoursEditor) return;
    const rate = hoursRate.trim();
    const packageRub = hoursPackageRub.trim();
    const balanceRub = hoursBalanceRub.trim() || packageRub;
    if (!rate || !packageRub) {
      setHoursError("Укажите стоимость часа и баланс пакета в рублях");
      return;
    }
    setHoursBusy(true);
    setHoursError(null);
    try {
      const title = hoursTitle.trim() || hoursEditor.portal.name || "Пакет сопровождения";
      const body = {
        deal_title: title,
        hourly_rate_rub: rate,
        package_rub: packageRub,
        balance_rub: balanceRub,
        is_active: true,
      };
      if (hoursEditor.binding) {
        await api(
          `/api/deal-bindings/${hoursEditor.binding.id}/`,
          {
            method: "PATCH",
            body: JSON.stringify(body),
          },
          token
        );
      } else {
        await api(
          "/api/deal-bindings/",
          {
            method: "POST",
            body: JSON.stringify({
              client_portal_id: hoursEditor.portal.id,
              ...body,
            }),
          },
          token
        );
      }
      toast.show("Баланс сохранён", hoursEditor.portal.name || "Клиент");
      setHoursEditor(null);
      await load();
    } catch (err) {
      setHoursError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setHoursBusy(false);
    }
  }

  return (
    <div className="clients-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Клиенты</h1>
          <p className="page-sub">Порталы клиентов и баланс сопровождения</p>
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
            <strong>Задайте пакет часов</strong>
            <p>Кнопка на карточке клиента — без CRM Bitrix</p>
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
              const hasDeal = Boolean(binding);
              return (
                <article
                  key={link.id}
                  className={`linked-card${enteringPortalId === p.id ? " is-entering" : ""}${hasDeal ? " has-deal" : ""}`}
                >
                  <header className="linked-card-top">
                    {renamingId === p.id ? (
                      <div className="linked-card-main linked-card-rename">
                        <span
                          className="linked-avatar"
                          style={{ background: hueFromId(p.id) }}
                        >
                          {initials(p)}
                        </span>
                        <div className="linked-meta">
                          <input
                            className="linked-rename-input"
                            value={renameDraft}
                            onChange={(e) => setRenameDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault();
                                void saveRename();
                              }
                              if (e.key === "Escape") cancelRename();
                            }}
                            disabled={renaming}
                            autoFocus
                            maxLength={255}
                            aria-label="Название клиента"
                          />
                          <span className="muted">{p.domain}</span>
                        </div>
                      </div>
                    ) : (
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
                    )}
                    <div className="linked-card-actions">
                      {renamingId === p.id ? (
                        <>
                          <button
                            type="button"
                            className="linked-action linked-action-save"
                            disabled={renaming || !renameDraft.trim()}
                            onClick={() => void saveRename()}
                          >
                            {renaming ? "Сохраняем…" : "Сохранить"}
                          </button>
                          <button
                            type="button"
                            className="linked-action"
                            disabled={renaming}
                            onClick={cancelRename}
                          >
                            Отмена
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="linked-action"
                            title="Переименовать клиента"
                            onClick={() => startRename(p)}
                          >
                            Переименовать
                          </button>
                          <button
                            type="button"
                            className="linked-unlink"
                            title="Отключить клиента"
                            onClick={() =>
                              setPendingUnlink({ linkId: link.id, name: title })
                            }
                          >
                            Отключить
                          </button>
                        </>
                      )}
                    </div>
                  </header>

                  <div className="deal-bind">
                    {hasDeal && binding ? (
                      <div className="deal-bind-status">
                        <div className="deal-bind-status-text">
                          <span className="deal-bind-kicker">Баланс</span>
                          <strong className="deal-bind-deal-name">
                            {binding.deal_title || `Пакет #${binding.deal_id}`}
                          </strong>
                        </div>
                        <DealHoursCard binding={binding} audience="agency" />
                        <button
                          type="button"
                          className="btn btn-ghost deal-bind-change"
                          onClick={() => openHoursEditor(p, binding)}
                        >
                          Изменить баланс
                        </button>
                      </div>
                    ) : (
                      <>
                        <p className="deal-bind-hint muted">
                          Без баланса и ставки часа списание с таймеров не работает
                        </p>
                        <button
                          type="button"
                          className="btn btn-accent"
                          onClick={() => openHoursEditor(p, null)}
                        >
                          Задать баланс
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

      {hoursEditor ? (
        <ModalPortal>
        <div className="modal-backdrop" role="presentation" onClick={closeHoursEditor}>
          <div
            className="modal-card stack"
            role="dialog"
            aria-modal="true"
            aria-label="Баланс клиента"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="section-title" style={{ margin: 0 }}>
              Баланс — {hoursEditor.portal.name || hoursEditor.portal.domain}
            </h2>
            <label className="field">
              <span>Название</span>
              <input
                value={hoursTitle}
                onChange={(e) => setHoursTitle(e.target.value)}
                disabled={hoursBusy}
              />
            </label>
            <label className="field">
              <span>Стоимость часа (₽)</span>
              <input
                inputMode="decimal"
                value={hoursRate}
                onChange={(e) => setHoursRate(e.target.value)}
                disabled={hoursBusy}
                required
              />
            </label>
            <label className="field">
              <span>Пакет (₽)</span>
              <input
                inputMode="decimal"
                value={hoursPackageRub}
                onChange={(e) => setHoursPackageRub(e.target.value)}
                disabled={hoursBusy}
                required
              />
            </label>
            <label className="field">
              <span>Остаток баланса (₽)</span>
              <input
                inputMode="decimal"
                value={hoursBalanceRub}
                onChange={(e) => setHoursBalanceRub(e.target.value)}
                disabled={hoursBusy}
                placeholder="как пакет, если пусто"
              />
            </label>
            {hoursPreview ? (
              <p className="muted" style={{ margin: 0 }}>
                Часов в пакете: {hoursPreview.paid} · остаток: {hoursPreview.remaining}
              </p>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                Часы посчитаются как баланс ÷ стоимость часа
              </p>
            )}
            {hoursError ? <div className="error-banner">{hoursError}</div> : null}
            <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={hoursBusy}
                onClick={closeHoursEditor}
              >
                Отмена
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={hoursBusy}
                onClick={() => void saveHoursPackage()}
              >
                {hoursBusy ? "Сохраняем…" : "Сохранить"}
              </button>
            </div>
          </div>
        </div>
        </ModalPortal>
      ) : null}
    </div>
  );
}
