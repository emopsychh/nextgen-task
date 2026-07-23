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
            "paid_hours",
            "remaining_hours",
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
            "paid_hours",
            "remaining_hours",
            "bitrix_company_id",
            "created_at",
            "updated_at",
        )

    def get_bitrix_company_id(self, obj):
        link = PortalLink.objects.filter(
            agency_portal_id=obj.agency_portal_id,
            client_portal_id=obj.client_portal_id,
        ).first()
        return link.bitrix_company_id if link else ""


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


def upsert_portal_from_auth(auth: dict, domain: str | None = None) -> Portal:
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

    app_tok = (
        str(auth.get("application_token") or auth.get("applicationToken") or "").strip()
        or (settings.BITRIX_APPLICATION_TOKEN or "").strip()
    )
    defaults = {
        "domain": portal_domain,
        "role": role,
        "access_token": auth.get("access_token", ""),
        "refresh_token": auth.get("refresh_token", ""),
        "expires_at": timezone.now()
        + timedelta(seconds=int(auth.get("expires_in", 3600))),
        "is_active": True,
    }
    if app_tok:
        defaults["application_token"] = app_tok

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
