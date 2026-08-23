import {
  asPackageHours,
  formatPackageHours,
  formatPackageHoursShort,
} from "../lib/format";
import type { DealBinding } from "../api/types";

type Props = {
  binding: DealBinding;
  /** Softer copy for the client portal home. */
  audience?: "agency" | "client";
};

export function hasDealHoursPackage(binding: DealBinding | null | undefined): boolean {
  if (!binding) return false;
  const paid = asPackageHours(binding.paid_hours);
  const remaining = asPackageHours(binding.remaining_hours);
  const credit = asPackageHours(binding.hours_credit);
  const overage = asPackageHours(binding.hours_overage);
  const applied = asPackageHours(binding.hours_overage_applied);
  return (
    paid != null ||
    remaining != null ||
    (credit != null && credit > 0) ||
    (overage != null && overage > 0) ||
    (applied != null && applied > 0)
  );
}

export function DealHoursCard({ binding, audience = "agency" }: Props) {
  const paid = asPackageHours(binding.paid_hours);
  const remaining = asPackageHours(binding.remaining_hours);
  const credit = asPackageHours(binding.hours_credit);
  const overage = asPackageHours(binding.hours_overage);
  const applied = asPackageHours(binding.hours_overage_applied);
  const won = Boolean(binding.is_won);
  const hasCredit = credit != null && credit > 0;
  const hasOverage = overage != null && overage > 0;
  const hasApplied = applied != null && applied > 0;

  if (paid == null && remaining == null && !hasCredit && !hasOverage && !hasApplied) {
    return null;
  }

  const packageSize = paid != null && paid > 0 ? paid : null;
  const used =
    packageSize != null && remaining != null
      ? Math.max(0, packageSize - Math.max(0, remaining))
      : null;
  const usedPct =
    packageSize != null && used != null
      ? Math.min(100, (used / packageSize) * 100)
      : null;
  const over = remaining != null && remaining <= 0 && !won;
  const forClient = audience === "client";

  if (forClient && !won) {
    return (
      <div
        className={`deal-hours-card is-client-pack${over ? " is-over" : ""}${hasCredit ? " has-credit" : ""}${hasOverage || hasApplied ? " has-overage" : ""}`}
        aria-label="Ваш пакет часов"
      >
        <div className="deal-hours-card-head">
          <h2 className="section-title">Пакет часов</h2>
        </div>

        <div className="deal-hours-pack-top">
          <p className="deal-hours-pack-remain">
            Осталось {remaining != null ? formatPackageHours(remaining) : "—"}
          </p>
          <dl className="deal-hours-pack-meta">
            <div>
              <dt>В пакете</dt>
              <dd>{paid != null ? formatPackageHours(paid) : "—"}</dd>
            </div>
            <div>
              <dt>Использовано</dt>
              <dd>{used != null ? formatPackageHoursShort(used) : "—"}</dd>
            </div>
          </dl>
        </div>
        {usedPct != null ? (
          <div
            className="deal-hours-card-track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(usedPct)}
            aria-label="Использовано из пакета"
          >
            <div
              className="deal-hours-card-fill"
              style={{ width: `${Math.max(usedPct, used && used > 0 ? 2 : 0)}%` }}
            />
          </div>
        ) : null}

        {hasCredit ? (
          <div className="deal-hours-card-credit">
            <span className="deal-hours-card-credit-label">Неиспользованный остаток</span>
            <strong className="deal-hours-card-credit-value">
              {formatPackageHours(credit)}
            </strong>
            <p className="deal-hours-card-credit-hint">
              {binding.hours_credit_source_title
                ? `После «${binding.hours_credit_source_title}». `
                : ""}
              Добавятся к следующей сделке сопровождения.
            </p>
          </div>
        ) : null}

        {hasOverage ? (
          <div className="deal-hours-card-credit is-overage">
            <span className="deal-hours-card-credit-label">Перерасход</span>
            <strong className="deal-hours-card-credit-value">
              {formatPackageHours(overage)}
            </strong>
            <p className="deal-hours-card-credit-hint">
              {binding.hours_overage_source_title
                ? `После «${binding.hours_overage_source_title}». `
                : ""}
              Спишется со следующей сделки сопровождения.
            </p>
          </div>
        ) : null}

        {hasApplied && !hasOverage ? (
          <div className="deal-hours-card-credit is-overage">
            <span className="deal-hours-card-credit-label">Перерасход с прошлой сделки</span>
            <strong className="deal-hours-card-credit-value">
              {formatPackageHours(applied)}
            </strong>
            <p className="deal-hours-card-credit-hint">
              Уже учтён в этом пакете и в отчёте.
            </p>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div
      className={`deal-hours-card${over ? " is-over" : ""}${won ? " is-won" : ""}${hasCredit ? " has-credit" : ""}${hasOverage || hasApplied ? " has-overage" : ""}`}
      aria-label={forClient ? "Ваш пакет часов" : "Часы по сделке сопровождения"}
    >
      <div className="deal-hours-card-head">
        <span className="deal-hours-card-kicker">
          {won ? "Сделка закрыта" : forClient ? "Пакет часов" : "Пакет сопровождения"}
        </span>
        {!won && binding.deal_title ? (
          <span className="deal-hours-card-deal muted" title={binding.deal_title}>
            {binding.deal_title}
          </span>
        ) : null}
      </div>

      {won ? (
        <p className="deal-hours-card-won">Завершена успешно</p>
      ) : (
        <div className="deal-hours-card-stats">
          <div className="deal-hours-stat">
            <span className="deal-hours-stat-label">Осталось</span>
            <strong className="deal-hours-stat-value is-remain">
              {remaining != null ? formatPackageHours(remaining) : "—"}
            </strong>
          </div>
          <div className="deal-hours-stat is-secondary">
            <span className="deal-hours-stat-label">В пакете</span>
            <strong className="deal-hours-stat-value">
              {paid != null ? formatPackageHours(paid) : "—"}
            </strong>
          </div>
        </div>
      )}

      {usedPct != null && !won ? (
        <div
          className="deal-hours-card-track"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(usedPct)}
          aria-label="Использовано из пакета"
        >
          <div
            className="deal-hours-card-fill"
            style={{ width: `${Math.max(usedPct, used && used > 0 ? 2 : 0)}%` }}
          />
        </div>
      ) : null}

      {hasCredit ? (
        <div className="deal-hours-card-credit">
          <span className="deal-hours-card-credit-label">Неиспользованный остаток</span>
          <strong className="deal-hours-card-credit-value">
            {formatPackageHours(credit)}
          </strong>
          <p className="deal-hours-card-credit-hint">
            {binding.hours_credit_source_title
              ? `После «${binding.hours_credit_source_title}». `
              : ""}
            {forClient
              ? "Добавятся к следующей сделке сопровождения."
              : "Перейдут на следующую сделку сопровождения этого клиента."}
          </p>
        </div>
      ) : null}

      {hasOverage ? (
        <div className="deal-hours-card-credit is-overage">
          <span className="deal-hours-card-credit-label">Перерасход</span>
          <strong className="deal-hours-card-credit-value">
            {formatPackageHours(overage)}
          </strong>
          <p className="deal-hours-card-credit-hint">
            {binding.hours_overage_source_title
              ? `После «${binding.hours_overage_source_title}». `
              : ""}
            {forClient
              ? "Спишется со следующей сделки сопровождения."
              : "Перейдёт на следующую сделку сопровождения этого клиента."}
          </p>
        </div>
      ) : null}

      {hasApplied && !hasOverage ? (
        <div className="deal-hours-card-credit is-overage">
          <span className="deal-hours-card-credit-label">Перерасход с прошлой сделки</span>
          <strong className="deal-hours-card-credit-value">
            {formatPackageHours(applied)}
          </strong>
          <p className="deal-hours-card-credit-hint">
            {forClient
              ? "Уже учтён в этом пакете."
              : "Уже списан с этого пакета и попал в отчёт."}
          </p>
        </div>
      ) : null}
    </div>
  );
}
