"""Helpers for CRM deal paid / remaining hours fields."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings


def _field_paid() -> str:
    return (settings.BITRIX_DEAL_PAID_HOURS_FIELD or "").strip()


def _field_remaining() -> str:
    return (settings.BITRIX_DEAL_REMAINING_HOURS_FIELD or "").strip()


def hours_fields_configured() -> bool:
    return bool(_field_paid() and _field_remaining())


def parse_hours(raw) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw).replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def seconds_to_hours(seconds: int) -> Decimal:
    hours = Decimal(max(0, int(seconds or 0))) / Decimal(3600)
    return hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class DealHours:
    paid: Decimal | None
    remaining: Decimal | None


def read_deal_hours(deal: dict) -> DealHours:
    paid_key = _field_paid()
    rem_key = _field_remaining()
    paid = parse_hours(deal.get(paid_key)) if paid_key else None
    remaining = parse_hours(deal.get(rem_key)) if rem_key else None
    return DealHours(paid=paid, remaining=remaining)


def compute_remaining_after_spend(deal: dict, spent_seconds: int) -> tuple[Decimal | None, Decimal]:
    """
    Return (new_remaining, spent_hours).

    If remaining is empty but paid is set, start from paid (first use).
    Never modifies the paid field value — only returns what remaining should become.
    """
    spent = seconds_to_hours(spent_seconds)
    if spent <= 0 or not hours_fields_configured():
        hours = read_deal_hours(deal)
        return hours.remaining, spent

    hours = read_deal_hours(deal)
    base = hours.remaining
    if base is None:
        base = hours.paid if hours.paid is not None else Decimal("0.00")
    new_remaining = (base - spent).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if new_remaining < 0:
        new_remaining = Decimal("0.00")
    return new_remaining, spent


def remaining_update_fields(new_remaining: Decimal) -> dict:
    key = _field_remaining()
    if not key:
        return {}
    # Bitrix number fields accept float / string
    return {key: float(new_remaining)}
