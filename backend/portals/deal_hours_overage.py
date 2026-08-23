"""Carry package overspend from a closed report onto the next deal."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from portals.bitrix import BitrixAPIError, BitrixClient
from portals.deal_hours import (
    hours_fields_configured,
    parse_hours,
    read_deal_hours,
    remaining_update_fields,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


def overage_hours_amount(raw) -> Decimal:
    parsed = parse_hours(raw)
    if parsed is None:
        return ZERO
    return max(ZERO, parsed)


def overage_seconds(hours) -> int:
    amount = overage_hours_amount(hours)
    if amount <= 0:
        return 0
    return int((amount * Decimal(3600)).quantize(Decimal("1")))


def portal_link_for_binding(binding):
    from portals.models import PortalLink

    if not binding:
        return None
    return PortalLink.objects.filter(
        agency_portal_id=binding.agency_portal_id,
        client_portal_id=binding.client_portal_id,
    ).first()


def capture_hours_overage(*, link, binding, overage) -> bool:
    """Park closed-task hours above the paid package for the next deal."""
    amount = overage_hours_amount(overage)
    if amount <= 0 or not link or not binding:
        return False

    deal_id = str(binding.deal_id or "").strip()
    if not deal_id:
        return False

    from portals.models import PortalLink

    with transaction.atomic():
        link = PortalLink.objects.select_for_update().get(pk=link.pk)

        if str(link.hours_overage_last_source_deal_id or "") == deal_id:
            return False
        if (
            str(link.hours_overage_source_deal_id or "") == deal_id
            and overage_hours_amount(link.hours_overage) > 0
        ):
            return False

        existing = overage_hours_amount(link.hours_overage)
        source_id = str(link.hours_overage_source_deal_id or "")
        if existing > 0 and source_id not in ("", deal_id):
            parked = existing + amount
        else:
            parked = amount

        link.hours_overage = parked
        link.hours_overage_source_deal_id = deal_id
        link.hours_overage_source_title = (
            binding.deal_title or f"Сделка #{deal_id}"
        )[:500]
        link.save(
            update_fields=[
                "hours_overage",
                "hours_overage_source_deal_id",
                "hours_overage_source_title",
            ]
        )
        logger.info(
            "hours overage captured client=%s deal=%s → %s",
            link.client_portal_id,
            deal_id,
            parked,
        )
        return True


def _mark_overage_applied(link, *, deal_id: str, amount: Decimal, source_id: str) -> None:
    link.hours_overage_applied_to_deal_id = deal_id
    link.hours_overage_last_amount = amount
    link.hours_overage_last_source_deal_id = source_id
    link.hours_overage = ZERO
    link.hours_overage_source_deal_id = ""
    link.hours_overage_source_title = ""
    link.save(
        update_fields=[
            "hours_overage",
            "hours_overage_source_deal_id",
            "hours_overage_source_title",
            "hours_overage_applied_to_deal_id",
            "hours_overage_last_amount",
            "hours_overage_last_source_deal_id",
        ]
    )


def _sync_applied_overage(
    binding,
    overage: Decimal,
    *,
    remaining=None,
    paid=None,
) -> list[str]:
    """Stamp overage on the deal and deduct it from remaining once."""
    update_fields: list[str] = []
    already = overage_hours_amount(binding.hours_overage_applied)
    first_apply = already <= 0
    if already != overage:
        binding.hours_overage_applied = overage
        update_fields.append("hours_overage_applied")

    rem = parse_hours(remaining if remaining is not None else binding.remaining_hours)
    paid_h = parse_hours(paid if paid is not None else binding.paid_hours)
    if rem is None:
        rem = paid_h if paid_h is not None else ZERO
    ceiling_base = paid_h if paid_h is not None else rem
    ceiling = (ceiling_base - overage).quantize(Decimal("0.01"))
    if ceiling < 0:
        ceiling = ZERO
    if first_apply:
        new_remaining = (rem - overage).quantize(Decimal("0.01"))
        if new_remaining < 0:
            new_remaining = ZERO
    else:
        new_remaining = rem if rem <= ceiling else ceiling
    if rem != new_remaining:
        binding.remaining_hours = new_remaining
        update_fields.append("remaining_hours")
    if update_fields:
        update_fields.append("updated_at")
    return update_fields


def next_overage_binding(link, *, source_deal_id: str = ""):
    """Pick the next package of this client that can take parked overspend."""
    from board.models import WorkReport
    from portals.models import PortalDealBinding

    source_deal_id = str(
        source_deal_id or getattr(link, "hours_overage_source_deal_id", "") or ""
    ).strip()
    rows = list(
        PortalDealBinding.objects.filter(
            agency_portal_id=link.agency_portal_id,
            client_portal_id=link.client_portal_id,
        )
        .exclude(deal_id=source_deal_id)
        .order_by("-is_active", "-updated_at", "-id")
    )
    if not rows:
        return None
    draft_ids = set(
        WorkReport.objects.filter(
            deal_binding_id__in=[row.pk for row in rows],
            status=WorkReport.Status.DRAFT,
        ).values_list("deal_binding_id", flat=True)
    )
    for row in rows:
        if row.pk in draft_ids:
            return row
    return rows[0]


def apply_pending_hours_overage(link) -> Decimal | None:
    """Apply parked overspend to the next package, even if that deal is inactive."""
    if not link:
        return None
    from portals.models import PortalLink

    link = PortalLink.objects.get(pk=link.pk)
    if overage_hours_amount(link.hours_overage) <= 0:
        return None
    target = next_overage_binding(link)
    if not target:
        return None
    return apply_hours_overage_to_binding(target, allow_inactive=True)


def apply_hours_overage_to_binding(binding, *, allow_inactive: bool = False) -> Decimal | None:
    """
    Deduct pending overage from this deal and stamp it on the report fill.
    Safe to call on every binding save: skips the source deal and repeats.
    """
    if not binding:
        return None
    if not allow_inactive and not getattr(binding, "is_active", False):
        return None

    deal_id = str(binding.deal_id or "").strip()
    if not deal_id:
        return None

    from portals.models import PortalLink

    link = portal_link_for_binding(binding)
    if not link:
        return None

    with transaction.atomic():
        link = PortalLink.objects.select_for_update().get(pk=link.pk)
        binding = type(binding).objects.select_for_update().get(pk=binding.pk)
        if not allow_inactive and not binding.is_active:
            return None

        already_applied = overage_hours_amount(binding.hours_overage_applied)
        if str(link.hours_overage_applied_to_deal_id or "") == deal_id:
            last = overage_hours_amount(link.hours_overage_last_amount)
            applied = already_applied if already_applied > 0 else last
            if applied <= 0:
                return None
            update_fields = _sync_applied_overage(binding, applied)
            if update_fields:
                binding.save(update_fields=update_fields)
            return applied

        overage = overage_hours_amount(link.hours_overage)
        if overage <= 0:
            return None

        source_id = str(link.hours_overage_source_deal_id or "").strip()
        if source_id == deal_id:
            return None

        rem = parse_hours(binding.remaining_hours)
        paid = parse_hours(binding.paid_hours)
        update_fields = _sync_applied_overage(binding, overage, remaining=rem, paid=paid)
        if update_fields:
            binding.save(update_fields=update_fields)
        _mark_overage_applied(link, deal_id=deal_id, amount=overage, source_id=source_id)
        logger.info(
            "hours overage applied locally client=%s → deal=%s remaining=%s overage=%s",
            link.client_portal_id,
            deal_id,
            binding.remaining_hours,
            overage,
        )
        return overage


def apply_hours_overage_to_new_deal(
    *,
    link,
    client: BitrixClient,
    new_deal_id: str,
    current_remaining=None,
) -> Decimal | None:
    """Subtract pending overage from the new deal remaining in CRM."""
    if not hours_fields_configured() or not link:
        return None

    new_deal_id = str(new_deal_id or "").strip()
    if not new_deal_id:
        return None

    from portals.models import PortalLink

    with transaction.atomic():
        link = PortalLink.objects.select_for_update().get(pk=link.pk)
        if str(link.hours_overage_applied_to_deal_id or "") == new_deal_id:
            return parse_hours(current_remaining)

        overage = overage_hours_amount(link.hours_overage)
        if overage <= 0:
            return None

        source_id = str(link.hours_overage_source_deal_id or "").strip()
        if source_id == new_deal_id:
            return None

        try:
            deal = client.get_deal(new_deal_id)
        except BitrixAPIError as exc:
            logger.info("apply overage get_deal failed %s: %s", new_deal_id, exc)
            return None

        hours = read_deal_hours(deal)
        rem = hours.remaining
        if rem is None:
            rem = hours.paid if hours.paid is not None else ZERO
        if current_remaining is not None:
            parsed_current = parse_hours(current_remaining)
            if parsed_current is not None:
                rem = parsed_current

        deducted = min(overage, rem) if rem > 0 else ZERO
        new_remaining = (rem - deducted).quantize(Decimal("0.01"))
        if new_remaining < 0:
            new_remaining = ZERO

        try:
            client.update_deal(new_deal_id, remaining_update_fields(new_remaining))
        except BitrixAPIError as exc:
            logger.info(
                "apply hours overage failed deal=%s overage=%s: %s",
                new_deal_id,
                overage,
                exc,
            )
            return None

        if source_id:
            try:
                client.add_deal_timeline_comment(
                    new_deal_id,
                    f"Списано {overage} ч перерасхода со сделки #{source_id} "
                    f"«{link.hours_overage_source_title or source_id}».",
                )
            except BitrixAPIError:
                pass

        _mark_overage_applied(
            link, deal_id=new_deal_id, amount=overage, source_id=source_id
        )
        logger.info(
            "hours overage applied client=%s → deal=%s remaining=%s (was %s − %s)",
            link.client_portal_id,
            new_deal_id,
            new_remaining,
            rem,
            deducted,
        )
        return new_remaining
