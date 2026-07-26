import { useEffect, useState } from "react";
import { formatPackageHours } from "../lib/format";

export type DealCandidate = {
  deal_id: string;
  title: string;
  stage_id?: string;
  company_id?: string;
  paid_hours?: number | null;
  remaining_hours?: number | null;
  bound_to_other_client?: boolean;
  bound_client_portal_id?: number | null;
};

type Props = {
  open: boolean;
  portalName: string;
  portalDomain: string;
  loading: boolean;
  error: string | null;
  deals: DealCandidate[];
  currentDealId?: string | null;
  confirmingId?: string | null;
  onClose: () => void;
  onSelect: (dealId: string) => void;
};

export function DealPickModal({
  open,
  portalName,
  portalDomain,
  loading,
  error,
  deals,
  currentDealId,
  confirmingId,
  onClose,
  onSelect,
}: Props) {
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setPicked(currentDealId || (deals.length === 1 ? deals[0].deal_id : null));
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !confirmingId) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, currentDealId, confirmingId, onClose, deals]);

  if (!open) return null;

  const selectable = deals.filter((d) => !d.bound_to_other_client);
  const canConfirm =
    Boolean(picked) &&
    !confirmingId &&
    !loading &&
    selectable.some((d) => d.deal_id === picked);

  return (
    <div className="modal-backdrop deal-pick-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card modal-card-wide deal-pick-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="deal-pick-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="deal-pick-head">
          <div className="deal-pick-head-text">
            <p className="deal-pick-kicker">Пакет сопровождения</p>
            <h3 id="deal-pick-title" className="deal-pick-title">
              Сделка для {portalName}
            </h3>
            <p className="deal-pick-sub muted">{portalDomain}</p>
          </div>
          <button
            type="button"
            className="deal-pick-close"
            onClick={onClose}
            disabled={Boolean(confirmingId)}
            aria-label="Закрыть"
          >
            ×
          </button>
        </header>

        <div className="deal-pick-scope" role="note">
          <span className="deal-pick-scope-dot" aria-hidden />
          Только сделки этого портала — где в CRM уже стоит его ссылка
        </div>

        {error ? <div className="error-banner">{error}</div> : null}

        <div className="deal-pick-list" role="listbox" aria-label="Сделки этого портала">
          {loading ? (
            <div className="deal-pick-empty muted">
              <span className="deal-pick-spinner" aria-hidden />
              Загружаем сделки из CRM…
            </div>
          ) : deals.length === 0 ? (
            <div className="deal-pick-empty">
              <strong>Нет сделок для этого портала</strong>
              <p className="muted">
                Откройте сделку сопровождения в CRM и укажите ссылку на{" "}
                <span className="deal-pick-domain">{portalDomain}</span> — после этого она
                появится в списке.
              </p>
            </div>
          ) : (
            deals.map((d) => {
              const selected = picked === d.deal_id;
              const current = currentDealId === d.deal_id;
              const locked = Boolean(d.bound_to_other_client);
              return (
                <button
                  key={d.deal_id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  disabled={locked || Boolean(confirmingId)}
                  className={`deal-pick-item${selected ? " is-selected" : ""}${locked ? " is-locked" : ""}${current ? " is-current" : ""}`}
                  onClick={() => !locked && setPicked(d.deal_id)}
                >
                  <span className={`deal-pick-radio${selected ? " on" : ""}`} aria-hidden />
                  <span className="deal-pick-item-main">
                    <strong className="deal-pick-item-title">{d.title}</strong>
                    <span className="deal-pick-item-meta">
                      <span>#{d.deal_id}</span>
                      {current ? <span className="deal-pick-badge">текущая</span> : null}
                      {locked ? (
                        <span className="deal-pick-badge is-warn">у другого клиента</span>
                      ) : null}
                    </span>
                  </span>
                  <span className="deal-pick-item-hours">
                    <span className="deal-pick-hours-remain">
                      {d.remaining_hours != null
                        ? formatPackageHours(d.remaining_hours)
                        : "—"}
                    </span>
                    <span className="muted deal-pick-hours-paid">
                      пакет{" "}
                      {d.paid_hours != null ? formatPackageHours(d.paid_hours) : "—"}
                    </span>
                  </span>
                </button>
              );
            })
          )}
        </div>

        <div className="modal-actions deal-pick-actions">
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onClose}
            disabled={Boolean(confirmingId)}
          >
            Отмена
          </button>
          <button
            type="button"
            className="btn btn-accent"
            disabled={!canConfirm}
            onClick={() => picked && onSelect(picked)}
          >
            {confirmingId ? "Привязываем…" : "Привязать"}
          </button>
        </div>
      </div>
    </div>
  );
}
