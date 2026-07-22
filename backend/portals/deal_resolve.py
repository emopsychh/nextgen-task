"""Resolve accompaniment CRM deals by Bitrix company id."""

from __future__ import annotations

from django.conf import settings

from portals.bitrix import BitrixAPIError, BitrixClient
from portals.deal_hours import (
    hours_fields_configured,
    read_deal_hours,
    remaining_update_fields,
)


def accompaniment_category_id() -> str:
    return (settings.BITRIX_ACCOMPANIMENT_CATEGORY_ID or "").strip()


def find_open_deal_for_company(client: BitrixClient, company_id: str) -> dict | None:
    """
    Find the newest open deal for a company in the accompaniment funnel.
    Returns raw deal dict or None.
    """
    company_id = str(company_id or "").strip()
    if not company_id:
        return None

    filt: dict = {
        "COMPANY_ID": company_id,
        "CLOSED": "N",
    }
    category = accompaniment_category_id()
    if category:
        filt["CATEGORY_ID"] = category

    select = ["ID", "TITLE", "CATEGORY_ID", "COMPANY_ID", "STAGE_ID", "DATE_MODIFY", "CLOSED"]
    paid = (settings.BITRIX_DEAL_PAID_HOURS_FIELD or "").strip()
    rem = (settings.BITRIX_DEAL_REMAINING_HOURS_FIELD or "").strip()
    if paid:
        select.append(paid)
    if rem:
        select.append(rem)

    result = client.call(
        "crm.deal.list",
        {
            "filter": filt,
            "order": {"DATE_MODIFY": "DESC"},
            "select": select,
            "start": 0,
        },
    )
    deals = result if isinstance(result, list) else []
    if isinstance(result, dict):
        deals = result.get("deals") or result.get("items") or []
    if not deals:
        return None
    deal = deals[0]
    return deal if isinstance(deal, dict) else None


def sync_deal_hours_meta(client: BitrixClient, deal_id: str, deal: dict | None = None) -> dict:
    """Title/category/hours; seed remaining from paid when remaining is empty."""
    meta = {
        "deal_title": "",
        "category_id": "",
        "paid_hours": None,
        "remaining_hours": None,
    }
    if deal is None:
        deal = client.get_deal(deal_id)
    meta["deal_title"] = str(deal.get("TITLE") or deal.get("title") or "")
    meta["category_id"] = str(deal.get("CATEGORY_ID") or deal.get("categoryId") or "")

    if hours_fields_configured():
        hours = read_deal_hours(deal)
        paid = hours.paid
        remaining = hours.remaining
        if remaining is None and paid is not None:
            client.update_deal(deal_id, remaining_update_fields(paid))
            remaining = paid
        meta["paid_hours"] = paid
        meta["remaining_hours"] = remaining
    return meta


def resolve_or_refresh_binding(*, agency_portal, client_portal, company_id: str | None = None):
    """
    Ensure an active PortalDealBinding exists for the client.
    Uses PortalLink.bitrix_company_id (or explicit company_id) to find the deal.
    """
    from portals.models import PortalDealBinding, PortalLink

    link = (
        PortalLink.objects.filter(
            agency_portal=agency_portal,
            client_portal=client_portal,
        )
        .first()
    )
    if not link:
        return None

    cid = str(company_id or link.bitrix_company_id or "").strip()
    if company_id is not None:
        link.bitrix_company_id = cid
        link.save(update_fields=["bitrix_company_id"])

    if not cid:
        return (
            PortalDealBinding.objects.filter(
                agency_portal=agency_portal,
                client_portal=client_portal,
                is_active=True,
            )
            .order_by("-updated_at")
            .first()
        )

    if not agency_portal.access_token:
        raise BitrixAPIError("Agency portal has no Bitrix token")

    client = BitrixClient(agency_portal)
    deal = find_open_deal_for_company(client, cid)
    if not deal:
        raise BitrixAPIError(
            f"Не найдена открытая сделка сопровождения для компании #{cid}"
            + (f" (воронка {accompaniment_category_id()})" if accompaniment_category_id() else "")
        )

    deal_id = str(deal.get("ID") or deal.get("id") or "")
    if not deal_id:
        raise BitrixAPIError("Bitrix вернул сделку без ID")

    meta = sync_deal_hours_meta(client, deal_id, deal)

    PortalDealBinding.objects.filter(
        agency_portal=agency_portal,
        client_portal=client_portal,
        is_active=True,
    ).exclude(deal_id=deal_id).update(is_active=False)

    binding, _ = PortalDealBinding.objects.update_or_create(
        agency_portal=agency_portal,
        client_portal=client_portal,
        deal_id=deal_id,
        defaults={
            "deal_title": meta["deal_title"],
            "category_id": meta["category_id"],
            "paid_hours": meta["paid_hours"],
            "remaining_hours": meta["remaining_hours"],
            "is_active": True,
        },
    )
    if not binding.is_active:
        binding.is_active = True
        binding.deal_title = meta["deal_title"] or binding.deal_title
        binding.category_id = meta["category_id"] or binding.category_id
        binding.paid_hours = meta["paid_hours"]
        binding.remaining_hours = meta["remaining_hours"]
        binding.save(
            update_fields=[
                "is_active",
                "deal_title",
                "category_id",
                "paid_hours",
                "remaining_hours",
                "updated_at",
            ]
        )
    return binding


def get_active_binding(*, agency_portal, client_portal):
    from portals.models import PortalDealBinding

    return (
        PortalDealBinding.objects.filter(
            agency_portal=agency_portal,
            client_portal=client_portal,
            is_active=True,
        )
        .order_by("-updated_at")
        .first()
    )
