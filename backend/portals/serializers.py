from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import BitrixUser, Portal, PortalDealBinding, PortalLink


class PortalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Portal
        fields = (
            "id",
            "member_id",
            "domain",
            "role",
            "name",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class PortalLinkSerializer(serializers.ModelSerializer):
    client_portal = PortalSerializer(read_only=True)
    client_portal_id = serializers.PrimaryKeyRelatedField(
        queryset=Portal.objects.filter(role=Portal.Role.CLIENT),
        source="client_portal",
        write_only=True,
    )

    class Meta:
        model = PortalLink
        fields = (
            "id",
            "agency_portal",
            "client_portal",
            "client_portal_id",
            "bitrix_company_id",
            "bitrix_group_id",
            "created_at",
        )
        read_only_fields = (
            "id",
            "agency_portal",
            "client_portal",
            "bitrix_company_id",
            "bitrix_group_id",
            "created_at",
        )


class PortalDealBindingSerializer(serializers.ModelSerializer):
    client_portal = PortalSerializer(read_only=True)
    client_portal_id = serializers.PrimaryKeyRelatedField(
        queryset=Portal.objects.filter(role=Portal.Role.CLIENT),
        source="client_portal",
        write_only=True,
    )
    bitrix_company_id = serializers.SerializerMethodField()
    is_won = serializers.SerializerMethodField()
    hours_credit = serializers.SerializerMethodField()
    hours_credit_source_deal_id = serializers.SerializerMethodField()
    hours_credit_source_title = serializers.SerializerMethodField()

    class Meta:
        model = PortalDealBinding
        fields = (
            "id",
            "agency_portal",
            "client_portal",
            "client_portal_id",
            "deal_id",
            "deal_title",
            "category_id",
            "stage_id",
            "stage_semantic",
            "is_won",
            "paid_hours",
            "remaining_hours",
            "hours_credit",
            "hours_credit_source_deal_id",
            "hours_credit_source_title",
            "bitrix_company_id",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "agency_portal",
            "client_portal",
            "deal_id",
            "deal_title",
            "category_id",
            "stage_id",
            "stage_semantic",
            "is_won",
            "paid_hours",
            "remaining_hours",
            "hours_credit",
            "hours_credit_source_deal_id",
            "hours_credit_source_title",
            "bitrix_company_id",
            "created_at",
            "updated_at",
        )

    def _link(self, obj):
        cache = self.context.setdefault("_portal_link_by_pair", {})
        key = (obj.agency_portal_id, obj.client_portal_id)
        if key not in cache:
            prefetched = getattr(obj.client_portal, "_current_agency_links", None)
            if prefetched is not None:
                cache[key] = prefetched[0] if prefetched else None
            else:
                cache[key] = PortalLink.objects.filter(
                    agency_portal_id=obj.agency_portal_id,
                    client_portal_id=obj.client_portal_id,
                ).first()
        return cache[key]

    def get_bitrix_company_id(self, obj):
        link = self._link(obj)
        return link.bitrix_company_id if link else ""

    def get_is_won(self, obj):
        return str(obj.stage_semantic or "").upper() == "S"

    def get_hours_credit(self, obj):
        link = self._link(obj)
        if not link or link.hours_credit is None:
            return None
        try:
            val = float(link.hours_credit)
        except (TypeError, ValueError):
            return None
        return val if val > 0 else 0.0

    def get_hours_credit_source_deal_id(self, obj):
        link = self._link(obj)
        return (link.hours_credit_source_deal_id if link else "") or ""

    def get_hours_credit_source_title(self, obj):
        link = self._link(obj)
        return (link.hours_credit_source_title if link else "") or ""


class BitrixUserSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = BitrixUser
        fields = (
            "id",
            "bitrix_id",
            "name",
            "last_name",
            "email",
            "avatar_url",
            "is_admin",
            "display_name",
        )


class MeSerializer(serializers.Serializer):
    portal = PortalSerializer()
    user = BitrixUserSerializer()


def issue_tokens(portal: Portal, bitrix_user: BitrixUser) -> dict:
    refresh = RefreshToken()
    refresh["portal_id"] = portal.id
    refresh["bitrix_user_id"] = bitrix_user.bitrix_id
    refresh["portal_role"] = portal.role
    access = refresh.access_token
    access["portal_id"] = portal.id
    access["bitrix_user_id"] = bitrix_user.bitrix_id
    access["portal_role"] = portal.role
    return {
        "access": str(access),
        "refresh": str(refresh),
    }


def resolve_portal_role(member_id: str, domain: str = "") -> str:
    """Agency if member_id/domain listed in env; otherwise client."""
    from portals.models import Portal

    agency_members = {
        m.strip() for m in (settings.AGENCY_MEMBER_IDS or "").split(",") if m.strip()
    }
    agency_domains = {
        d.strip().lower().replace("https://", "").replace("http://", "").rstrip("/")
        for d in (settings.AGENCY_DOMAINS or "").split(",")
        if d.strip()
    }
    domain_norm = (
        str(domain or "")
        .lower()
        .replace("https://", "")
        .replace("http://", "")
        .rstrip("/")
        .replace("/rest/", "")
        .replace("/rest", "")
    )
    if member_id and member_id in agency_members:
        return Portal.Role.AGENCY
    if domain_norm and domain_norm in agency_domains:
        return Portal.Role.AGENCY
    return Portal.Role.CLIENT


def upsert_portal_from_auth(
    auth: dict,
    domain: str | None = None,
    *,
    replace_tokens: bool | None = None,
    update_oauth_tokens: bool | None = None,
) -> Portal:
    """
    Create/update Portal from Bitrix auth.

    OAuth access/refresh tokens belong to whoever installed the local app and
    must stay stable for outbound Bitrix sync. Employee placement logins must
    not overwrite them (otherwise sync runs as the last opener).

    replace_tokens=True / update_oauth_tokens=True
        — always save tokens (install / reinstall / healer login)
    replace_tokens=False / update_oauth_tokens=False
        — keep existing tokens; bootstrap only when portal has none
    both None (default)
        — agency: pin existing service token; client/empty: save
    """
    member_id = str(auth.get("member_id") or "")
    if not member_id:
        raise serializers.ValidationError("member_id required")

    portal_domain = domain or auth.get("domain") or auth.get("client_endpoint", "")
    if "://" in str(portal_domain):
        portal_domain = portal_domain.split("://", 1)[1]
    portal_domain = str(portal_domain).rstrip("/").replace("/rest/", "").replace("/rest", "")
    if not portal_domain or portal_domain.lower() == "unknown":
        raise serializers.ValidationError("domain required")

    role = resolve_portal_role(member_id, portal_domain)
    existing = Portal.objects.filter(member_id=member_id).first()
    if existing and existing.role in (
        Portal.Role.AGENCY,
        Portal.Role.CLIENT,
    ):
        # Keep an already-classified role; env lists may lag behind.
        role = existing.role

    app_tok = (
        str(auth.get("application_token") or auth.get("applicationToken") or "").strip()
        or (settings.BITRIX_APPLICATION_TOKEN or "").strip()
    )

    defaults: dict = {
        "domain": portal_domain,
        "role": role,
        "is_active": True,
    }
    if app_tok:
        defaults["application_token"] = app_tok

    new_access = str(auth.get("access_token") or "").strip()
    new_refresh = str(auth.get("refresh_token") or "").strip()
    has_access = bool(existing and (existing.access_token or "").strip())
    has_service_token = bool(
        existing
        and (existing.access_token or "").strip()
        and (existing.refresh_token or "").strip()
    )

    force_write = replace_tokens is True or update_oauth_tokens is True
    force_keep = replace_tokens is False or update_oauth_tokens is False

    if force_write:
        write_tokens = True
    elif force_keep:
        # Bootstrap only when the portal has no access token yet.
        write_tokens = not has_access
    else:
        # Default: pin agency service token once set; clients may refresh on open.
        if role == Portal.Role.AGENCY and has_service_token:
            write_tokens = False
        else:
            write_tokens = True

    if write_tokens:
        if new_access:
            defaults["access_token"] = new_access
        # Never wipe a good refresh_token with an empty placement REFRESH_ID.
        if new_refresh:
            defaults["refresh_token"] = new_refresh
        elif existing and (existing.refresh_token or "").strip():
            defaults["refresh_token"] = existing.refresh_token
        elif not existing:
            defaults["refresh_token"] = new_refresh
        try:
            expires_in = int(auth.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600
        defaults["expires_at"] = timezone.now() + timedelta(seconds=expires_in)

    portal, _ = Portal.objects.update_or_create(
        member_id=member_id,
        defaults=defaults,
    )
    # Prefer Bitrix domain as display name when empty
    if not portal.name:
        portal.name = portal_domain.split(".")[0]
        portal.save(update_fields=["name", "updated_at"])
    return portal


def upsert_bitrix_user(portal: Portal, user_data: dict) -> BitrixUser:
    bitrix_id = str(user_data.get("ID") or user_data.get("id") or "")
    if not bitrix_id:
        raise serializers.ValidationError("Bitrix user id missing")

    personal_photo = user_data.get("PERSONAL_PHOTO") or user_data.get("personal_photo") or ""
    bitrix_user, _ = BitrixUser.objects.update_or_create(
        portal=portal,
        bitrix_id=bitrix_id,
        defaults={
            "name": user_data.get("NAME") or user_data.get("name") or "",
            "last_name": user_data.get("LAST_NAME") or user_data.get("last_name") or "",
            "email": user_data.get("EMAIL") or user_data.get("email") or "",
            "avatar_url": personal_photo if isinstance(personal_photo, str) else "",
            "is_admin": bool(user_data.get("ADMIN") or user_data.get("IS_ADMIN")),
        },
    )
    return bitrix_user
