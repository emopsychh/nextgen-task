"""Money (₽) ↔ hours helpers for accompaniment packages."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

QUANT = Decimal("0.01")
ZERO = Decimal("0.00")


def as_money(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None


def hours_from_money(amount: Decimal | None, rate: Decimal | None) -> Decimal | None:
    if amount is None or rate is None or rate <= 0:
        return None
    return (amount / rate).quantize(QUANT, rounding=ROUND_HALF_UP)


def money_from_hours(hours: Decimal | None, rate: Decimal | None) -> Decimal | None:
    if hours is None or rate is None or rate <= 0:
        return None
    return (hours * rate).quantize(QUANT, rounding=ROUND_HALF_UP)


def apply_money_fields(
    *,
    hourly_rate_rub: Decimal | None = None,
    package_rub: Decimal | None = None,
    balance_rub: Decimal | None = None,
    paid_hours: Decimal | None = None,
    remaining_hours: Decimal | None = None,
) -> dict[str, Decimal | None]:
    """
    Resolve package fields. Money is preferred: hours = rubles / rate.
    If only hours are given (legacy), money fields stay unset.
    """
    rate = hourly_rate_rub
    package = package_rub
    balance = balance_rub

    money_mode = rate is not None or package is not None or balance is not None
    if money_mode:
        if rate is None or rate <= 0:
            raise ValueError("Укажите стоимость часа (₽)")
        if package is None and paid_hours is not None:
            package = money_from_hours(paid_hours, rate)
        if balance is None:
            if remaining_hours is not None:
                balance = money_from_hours(remaining_hours, rate)
            elif package is not None:
                balance = package
        if package is None and balance is not None:
            package = balance
        if package is None:
            raise ValueError("Укажите баланс пакета (₽)")
        if balance is None:
            balance = package
        paid = hours_from_money(package, rate)
        remaining = hours_from_money(balance, rate)
        return {
            "hourly_rate_rub": rate,
            "package_rub": package,
            "balance_rub": balance,
            "paid_hours": paid,
            "remaining_hours": remaining,
        }

    # Legacy hours-only package
    paid = paid_hours
    remaining = remaining_hours
    if remaining is None and paid is not None:
        remaining = paid
    return {
        "hourly_rate_rub": None,
        "package_rub": None,
        "balance_rub": None,
        "paid_hours": paid,
        "remaining_hours": remaining,
    }


def deduct_spend_from_binding(binding, spent_hours: Decimal) -> list[str]:
    """
    Subtract spent hours from remaining_hours and (when rate is set) from balance_rub.
    Returns update_fields for save().
    """
    update_fields: list[str] = []
    spent = spent_hours.quantize(QUANT, rounding=ROUND_HALF_UP)
    if binding.remaining_hours is not None:
        binding.remaining_hours = max(ZERO, binding.remaining_hours - spent)
        update_fields.append("remaining_hours")

    rate = binding.hourly_rate_rub
    if rate is not None and rate > 0:
        cost = money_from_hours(spent, rate) or ZERO
        if binding.balance_rub is not None:
            binding.balance_rub = max(ZERO, binding.balance_rub - cost)
            update_fields.append("balance_rub")
        elif binding.remaining_hours is not None:
            # Rate set later — keep balance in sync with remaining hours.
            binding.balance_rub = money_from_hours(binding.remaining_hours, rate)
            update_fields.append("balance_rub")
    return update_fields
