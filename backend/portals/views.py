from django.db.models import Q
from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .bitrix import BitrixAPIError, BitrixClient
from .deal_hours import hours_fields_configured, read_deal_hours, remaining_update_fields
from .models import Portal, PortalDealBinding, PortalLink
from .permissions import IsAgencyPortal, IsPortalAuthenticated, can_access_client_portal
from .serializers import (
    MeSerializer,
    PortalDealBindingSerializer,
    PortalLinkSerializer,
    PortalSerializer,
    issue_tokens,
    upsert_bitrix_user,
    upsert_portal_from_auth,
)


def _bitrix_install_finish_html() -> HttpResponse:
    """Bitrix keeps reopening install until BX24.installFinish() is called in the iframe."""
    html = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <title>Nextgen manager — установка</title>
  <script src="//api.bitrix24.com/api/v1/"></script>
  <style>
    body { font-family: system-ui, sans-serif; padding: 2rem; color: #1f2937; }
  </style>
</head>
<body>
  <p>Установка Nextgen manager…</p>
  <script>
    BX24.init(function () {
      BX24.installFinish();
    });
  </script>
</body>
</html>"""
    return HttpResponse(html, content_type="text/html; charset=utf-8")


def _collect_bitrix_query(request) -> str:
    """Forward Bitrix auth fields from GET/POST into a query string for the SPA."""
    keys = (
        "AUTH_ID",
        "REFRESH_ID",
        "AUTH_EXPIRES",
        "DOMAIN",
        "domain",
        "member_id",
        "MEMBER_ID",
        "APP_SID",
        "LANG",
        "PROTOCOL",
    )
    params: list[str] = []
    seen: set[str] = set()
    sources = []
    if hasattr(request, "data") and isinstance(request.data, dict):
        sources.append(request.data)
    sources.append(request.POST)
    sources.append(request.GET)
    for src in sources:
        for key in keys:
            if key in seen:
                continue
            val = src.get(key)
            if val is None or val == "":
                continue
            from urllib.parse import quote

            params.append(f"{key}={quote(str(val), safe='')}")
            seen.add(key)
    return "&".join(params)


@method_decorator(csrf_exempt, name="dispatch")
class BitrixInstallView(APIView):
    """Handler for Bitrix local app install / reinstall (installation wizard URL)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        auth = request.data.get("auth") or request.data
        if isinstance(auth, str):
            return Response({"detail": "Invalid auth payload"}, status=400)

        # Nested auth from Bitrix event
        if "access_token" not in auth and "auth" in request.data:
            auth = request.data["auth"]

        # Placement-style flat POST (AUTH_ID) — still finish install in browser
        if not (isinstance(auth, dict) and (auth.get("access_token") or auth.get("AUTH_ID"))):
            flat = {
                "access_token": request.data.get("AUTH_ID") or request.POST.get("AUTH_ID"),
                "refresh_token": request.data.get("REFRESH_ID") or request.POST.get("REFRESH_ID") or "",
                "member_id": request.data.get("member_id")
                or request.data.get("MEMBER_ID")
                or request.POST.get("member_id")
                or request.POST.get("MEMBER_ID"),
                "domain": request.data.get("DOMAIN")
                or request.data.get("domain")
                or request.POST.get("DOMAIN"),
                "expires_in": request.data.get("AUTH_EXPIRES") or request.POST.get("AUTH_EXPIRES") or 3600,
                "application_token": request.data.get("APP_SID") or request.POST.get("APP_SID") or "",
            }
            if flat["access_token"] and flat["member_id"]:
                auth = flat

        if isinstance(auth, dict) and (auth.get("access_token") or auth.get("AUTH_ID") or auth.get("member_id")):
            try:
                if auth.get("AUTH_ID") and not auth.get("access_token"):
                    auth = {
                        **auth,
                        "access_token": auth.get("AUTH_ID"),
                        "refresh_token": auth.get("REFRESH_ID", auth.get("refresh_token", "")),
                        "expires_in": auth.get("AUTH_EXPIRES") or auth.get("expires_in") or 3600,
                    }
                upsert_portal_from_auth(auth)
            except Exception:
                # Still finish install UI so Bitrix does not loop forever
                pass

        return _bitrix_install_finish_html()

    def get(self, request):
        return _bitrix_install_finish_html()


@method_decorator(csrf_exempt, name="dispatch")
class BitrixAuthView(APIView):
    """Exchange Bitrix placement/auth payload for our JWT."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        data = request.data
        auth = data.get("auth") or data
        domain = data.get("DOMAIN") or data.get("domain") or auth.get("domain")

        if not auth.get("access_token") and not auth.get("AUTH_ID"):
            return Response({"detail": "Bitrix auth required"}, status=400)

        # Placement often sends AUTH_ID instead of access_token
        if auth.get("AUTH_ID") and not auth.get("access_token"):
            auth = {
                **auth,
                "access_token": auth.get("AUTH_ID"),
                "refresh_token": auth.get("REFRESH_ID", auth.get("refresh_token", "")),
                "member_id": auth.get("member_id") or data.get("member_id") or data.get("MEMBER_ID"),
                "domain": domain,
                "expires_in": auth.get("AUTH_EXPIRES") or auth.get("expires_in") or 3600,
            }

        try:
            portal = upsert_portal_from_auth(auth, domain=domain)
            client = BitrixClient(portal)
            user_data = client.get_current_user()
            bitrix_user = upsert_bitrix_user(portal, user_data)
        except (BitrixAPIError, Exception) as exc:
            return Response({"detail": str(exc)}, status=400)

        tokens = issue_tokens(portal, bitrix_user)
        return Response(
            {
                **tokens,
                "portal": PortalSerializer(portal).data,
                "user": {
                    "id": bitrix_user.id,
                    "bitrix_id": bitrix_user.bitrix_id,
                    "display_name": bitrix_user.display_name,
                    "name": bitrix_user.name,
                    "last_name": bitrix_user.last_name,
                    "email": bitrix_user.email,
                    "avatar_url": bitrix_user.avatar_url,
                    "is_admin": bitrix_user.is_admin,
                },
            }
        )


class DevAuthView(APIView):
    """Local development login without Bitrix."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        if not settings.DEV_AUTH_BYPASS:
            return Response({"detail": "Dev auth disabled"}, status=403)

        role = request.data.get("role", "agency")
        member_id = request.data.get("member_id") or f"dev-{role}"
        domain = request.data.get("domain") or f"{role}.local"

        if role == Portal.Role.CLIENT or role == "client":
            default_first = "Клиент"
            default_last = "Demo"
        else:
            default_first = "Агентство"
            default_last = "Demo"

        first_name = request.data.get("first_name") or default_first
        last_name = request.data.get("last_name") or default_last

        portal, created = Portal.objects.get_or_create(
            member_id=member_id,
            defaults={
                "domain": domain,
                "role": role if role in dict(Portal.Role.choices) else Portal.Role.AGENCY,
                "name": request.data.get("name") or f"Dev {role.title()}",
            },
        )
        if not created and portal.role == Portal.Role.UNKNOWN:
            portal.role = role
            portal.name = portal.name or f"Dev {role.title()}"
            portal.save(update_fields=["role", "name", "updated_at"])

        bitrix_user, _ = portal.users.update_or_create(
            bitrix_id=request.data.get("bitrix_id") or f"dev-{role}-user",
            defaults={
                "name": first_name,
                "last_name": last_name,
                "email": request.data.get("email") or f"{role}@example.com",
                "is_admin": True,
            },
        )
        # Refresh legacy dev users created as bitrix_id="1" / "Dev User"
        portal.users.filter(bitrix_id="1").exclude(pk=bitrix_user.pk).update(
            name=first_name,
            last_name=last_name,
            email=request.data.get("email") or f"{role}@example.com",
        )

        # Auto-link first agency to client portals in dev
        if portal.role == Portal.Role.CLIENT:
            agency = Portal.objects.filter(role=Portal.Role.AGENCY).first()
            if agency:
                PortalLink.objects.get_or_create(agency_portal=agency, client_portal=portal)

        tokens = issue_tokens(portal, bitrix_user)
        return Response(
            {
                **tokens,
                "portal": PortalSerializer(portal).data,
                "user": {
                    "id": bitrix_user.id,
                    "bitrix_id": bitrix_user.bitrix_id,
                    "display_name": bitrix_user.display_name,
                    "name": bitrix_user.name,
                    "last_name": bitrix_user.last_name,
                    "email": bitrix_user.email,
                    "avatar_url": bitrix_user.avatar_url,
                    "is_admin": bitrix_user.is_admin,
                },
            }
        )


class MeView(APIView):
    permission_classes = [IsPortalAuthenticated]

    def get(self, request):
        data = {
            "portal": request.user.portal,
            "user": request.user.bitrix_user,
        }
        return Response(MeSerializer(data).data)


class PortalViewSet(viewsets.ModelViewSet):
    serializer_class = PortalSerializer
    permission_classes = [IsPortalAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        if user.is_agency:
            # Own portal + real client portals (skip broken installs without domain)
            return Portal.objects.filter(
                Q(id=user.portal.id)
                | (
                    Q(role=Portal.Role.CLIENT)
                    & ~Q(domain="")
                    & ~Q(domain__iexact="unknown")
                )
            )
        return Portal.objects.filter(id=user.portal.id)

    def partial_update(self, request, *args, **kwargs):
        portal = self.get_object()
        # Only own portal name; role is assigned from AGENCY_* env, not UI
        if portal.id != request.user.portal.id and not request.user.is_agency:
            return Response({"detail": "Forbidden"}, status=403)
        allowed = {}
        if "name" in request.data:
            allowed["name"] = request.data["name"]
        for k, v in allowed.items():
            setattr(portal, k, v)
        if allowed:
            portal.save()
        return Response(PortalSerializer(portal).data)


class PortalLinkViewSet(viewsets.ModelViewSet):
    serializer_class = PortalLinkSerializer
    permission_classes = [IsPortalAuthenticated, IsAgencyPortal]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return PortalLink.objects.filter(agency_portal=self.request.user.portal).select_related(
            "client_portal", "agency_portal"
        )

    def perform_create(self, serializer):
        client_portal = serializer.validated_data["client_portal"]
        link, _ = PortalLink.objects.get_or_create(
            agency_portal=self.request.user.portal,
            client_portal=client_portal,
        )
        serializer.instance = link


class PortalDealBindingViewSet(viewsets.ModelViewSet):
    """Agency binds a CRM deal (Сопровождение) to a linked client portal."""

    serializer_class = PortalDealBindingSerializer
    permission_classes = [IsPortalAuthenticated, IsAgencyPortal]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    filterset_fields = ["client_portal", "is_active"]

    def get_queryset(self):
        return PortalDealBinding.objects.filter(
            agency_portal=self.request.user.portal
        ).select_related("client_portal", "agency_portal")

    def _sync_deal_from_bitrix(self, deal_id: str) -> dict:
        """Fetch deal title/category/hours; seed remaining from paid if empty."""
        portal = self.request.user.portal
        meta = {
            "deal_title": "",
            "category_id": "",
            "paid_hours": None,
            "remaining_hours": None,
        }
        if not portal.access_token:
            return meta

        client = BitrixClient(portal)
        deal = client.get_deal(deal_id)
        meta["deal_title"] = str(deal.get("TITLE") or deal.get("title") or "")
        meta["category_id"] = str(deal.get("CATEGORY_ID") or deal.get("categoryId") or "")

        if hours_fields_configured():
            hours = read_deal_hours(deal)
            paid = hours.paid
            remaining = hours.remaining
            # First bind / empty remaining: copy paid → remaining in Bitrix
            if remaining is None and paid is not None:
                client.update_deal(deal_id, remaining_update_fields(paid))
                remaining = paid
            meta["paid_hours"] = paid
            meta["remaining_hours"] = remaining
        return meta

    def create(self, request, *args, **kwargs):
        client_portal = None
        client_id = request.data.get("client_portal_id")
        if client_id is not None:
            try:
                client_portal = Portal.objects.get(pk=client_id, role=Portal.Role.CLIENT)
            except Portal.DoesNotExist:
                return Response({"detail": "Client portal not found"}, status=404)
        if not client_portal or not can_access_client_portal(request.user, client_portal):
            return Response({"detail": "Client is not linked to this agency"}, status=403)

        deal_id = str(request.data.get("deal_id") or "").strip()
        if not deal_id:
            return Response({"detail": "deal_id required"}, status=400)

        meta = {
            "deal_title": "",
            "category_id": "",
            "paid_hours": None,
            "remaining_hours": None,
        }
        if request.user.portal.access_token:
            try:
                meta = self._sync_deal_from_bitrix(deal_id)
            except BitrixAPIError as exc:
                return Response({"detail": f"Bitrix CRM: {exc}"}, status=400)

        PortalDealBinding.objects.filter(
            agency_portal=request.user.portal,
            client_portal=client_portal,
            is_active=True,
        ).update(is_active=False)

        binding, _ = PortalDealBinding.objects.update_or_create(
            agency_portal=request.user.portal,
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

        return Response(PortalDealBindingSerializer(binding).data, status=201)

    def partial_update(self, request, *args, **kwargs):
        binding = self.get_object()
        deal_id = request.data.get("deal_id")
        is_active = request.data.get("is_active")

        if deal_id is not None:
            deal_id = str(deal_id).strip()
            if not deal_id:
                return Response({"detail": "deal_id required"}, status=400)
            meta = {
                "deal_title": binding.deal_title,
                "category_id": binding.category_id,
                "paid_hours": binding.paid_hours,
                "remaining_hours": binding.remaining_hours,
            }
            if request.user.portal.access_token:
                try:
                    meta = self._sync_deal_from_bitrix(deal_id)
                except BitrixAPIError as exc:
                    return Response({"detail": f"Bitrix CRM: {exc}"}, status=400)
            binding.deal_id = deal_id
            binding.deal_title = meta["deal_title"]
            binding.category_id = meta["category_id"]
            binding.paid_hours = meta["paid_hours"]
            binding.remaining_hours = meta["remaining_hours"]

        if is_active is not None:
            active = bool(is_active)
            if active:
                PortalDealBinding.objects.filter(
                    agency_portal=request.user.portal,
                    client_portal=binding.client_portal,
                    is_active=True,
                ).exclude(pk=binding.pk).update(is_active=False)
            binding.is_active = active

        binding.save()
        return Response(PortalDealBindingSerializer(binding).data)

    @action(detail=True, methods=["post"], url_path="refresh-hours")
    def refresh_hours(self, request, pk=None):
        binding = self.get_object()
        if not request.user.portal.access_token:
            return Response({"detail": "Agency portal has no Bitrix token"}, status=400)
        try:
            meta = self._sync_deal_from_bitrix(binding.deal_id)
        except BitrixAPIError as exc:
            return Response({"detail": f"Bitrix CRM: {exc}"}, status=400)
        binding.deal_title = meta["deal_title"] or binding.deal_title
        binding.category_id = meta["category_id"] or binding.category_id
        binding.paid_hours = meta["paid_hours"]
        binding.remaining_hours = meta["remaining_hours"]
        binding.save(
            update_fields=[
                "deal_title",
                "category_id",
                "paid_hours",
                "remaining_hours",
                "updated_at",
            ]
        )
        return Response(PortalDealBindingSerializer(binding).data)

    def perform_destroy(self, instance):
        instance.delete()


@csrf_exempt
@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def app_entry(request):
    """Open SPA from Bitrix menu; forward placement auth as query params."""
    qs = _collect_bitrix_query(request)
    target = settings.FRONTEND_URL.rstrip("/") + "/"
    if qs:
        target = f"{target}?{qs}"
    return HttpResponseRedirect(target)

