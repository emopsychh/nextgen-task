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
import logging

from .bitrix import BitrixAPIError, BitrixClient
from .deal_resolve import resolve_or_refresh_binding, sync_deal_hours_meta
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

logger = logging.getLogger(__name__)


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
                # Never use APP_SID here — it is not application_token and breaks event auth.
                "application_token": request.data.get("application_token")
                or request.POST.get("application_token")
                or "",
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
                try:
                    from board.status_sync import ensure_task_event_bindings
                    from portals.models import Portal as PortalModel

                    mid = auth.get("member_id") or auth.get("MEMBER_ID")
                    if mid:
                        p = PortalModel.objects.filter(member_id=mid).first()
                        if p:
                            ensure_task_event_bindings(p)
                except Exception:
                    logger.exception("event.bind during install failed")
            except Exception:
                # Still finish install UI so Bitrix does not loop forever
                logger.exception("Bitrix install upsert failed")

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
            try:
                from board.status_sync import ensure_task_event_bindings

                ensure_task_event_bindings(portal)
            except Exception:
                logger.exception("event.bind during auth failed for portal %s", portal.id)
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
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

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
        # Best-effort: bind accompaniment deal by portal-link UF field
        try:
            resolve_or_refresh_binding(
                agency_portal=self.request.user.portal,
                client_portal=client_portal,
            )
        except BitrixAPIError as exc:
            logger.info(
                "Auto deal bind skipped for client portal %s: %s",
                client_portal.id,
                exc,
            )


class PortalDealBindingViewSet(viewsets.ModelViewSet):
    """Agency links a client to an accompaniment CRM deal (via portal link field)."""

    serializer_class = PortalDealBindingSerializer
    permission_classes = [IsPortalAuthenticated, IsAgencyPortal]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    filterset_fields = ["client_portal", "is_active"]

    def get_queryset(self):
        return PortalDealBinding.objects.filter(
            agency_portal=self.request.user.portal
        ).select_related("client_portal", "agency_portal")

    def _client_portal_from_request(self, request):
        client_id = request.data.get("client_portal_id")
        if client_id is None:
            return None, Response({"detail": "client_portal_id required"}, status=400)
        try:
            client_portal = Portal.objects.get(pk=client_id, role=Portal.Role.CLIENT)
        except Portal.DoesNotExist:
            return None, Response({"detail": "Client portal not found"}, status=404)
        if not can_access_client_portal(request.user, client_portal):
            return None, Response({"detail": "Client is not linked to this agency"}, status=403)
        return client_portal, None

    def create(self, request, *args, **kwargs):
        client_portal, err = self._client_portal_from_request(request)
        if err:
            return err

        deal_id = str(request.data.get("deal_id") or "").strip()

        # Preferred: resolve open accompaniment deal by portal-link UF field
        if not deal_id:
            try:
                binding = resolve_or_refresh_binding(
                    agency_portal=request.user.portal,
                    client_portal=client_portal,
                )
            except BitrixAPIError as exc:
                return Response({"detail": f"Bitrix CRM: {exc}"}, status=400)
            if not binding:
                return Response({"detail": "Клиент не привязан к агентству"}, status=400)
            return Response(PortalDealBindingSerializer(binding).data, status=201)

        # Fallback: explicit deal_id (advanced)
        meta = {
            "deal_title": "",
            "category_id": "",
            "paid_hours": None,
            "remaining_hours": None,
        }
        if request.user.portal.access_token:
            try:
                meta = sync_deal_hours_meta(BitrixClient(request.user.portal), deal_id)
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
        refresh = request.data.get("refresh") in (True, "1", "true", "yes")
        is_active = request.data.get("is_active")

        if refresh or "bitrix_company_id" in request.data:
            # Re-resolve by portal link (company id no longer used)
            try:
                binding = resolve_or_refresh_binding(
                    agency_portal=request.user.portal,
                    client_portal=binding.client_portal,
                )
            except BitrixAPIError as exc:
                return Response({"detail": f"Bitrix CRM: {exc}"}, status=400)
            if not binding:
                return Response({"detail": "Не удалось обновить привязку"}, status=400)

        if is_active is not None and binding:
            active = bool(is_active)
            if active:
                PortalDealBinding.objects.filter(
                    agency_portal=request.user.portal,
                    client_portal=binding.client_portal,
                    is_active=True,
                ).exclude(pk=binding.pk).update(is_active=False)
            binding.is_active = active
            binding.save(update_fields=["is_active", "updated_at"])

        return Response(PortalDealBindingSerializer(binding).data)

    @action(detail=True, methods=["post"], url_path="refresh-hours")
    def refresh_hours(self, request, pk=None):
        binding = self.get_object()
        if not request.user.portal.access_token:
            return Response({"detail": "Agency portal has no Bitrix token"}, status=400)

        try:
            binding = resolve_or_refresh_binding(
                agency_portal=request.user.portal,
                client_portal=binding.client_portal,
            )
        except BitrixAPIError:
            # Keep existing binding; just refresh hours from current deal_id
            try:
                meta = sync_deal_hours_meta(BitrixClient(request.user.portal), binding.deal_id)
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
            except BitrixAPIError as exc:
                return Response({"detail": f"Bitrix CRM: {exc}"}, status=400)

        if not binding:
            return Response({"detail": "Сделка не найдена"}, status=404)
        return Response(PortalDealBindingSerializer(binding).data)

    def perform_destroy(self, instance):
        instance.delete()


@method_decorator(csrf_exempt, name="dispatch")
class BitrixEventView(APIView):
    """Incoming Bitrix app events (OnTaskAdd / OnTaskUpdate / OnTaskCommentAdd)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        event, data, auth = _parse_bitrix_event(request)
        event_u = str(event or "").upper().replace("_", "")
        if event_u not in ("ONTASKUPDATE", "ONTASKCOMMENTADD", "ONTASKADD"):
            return Response({"ok": True, "ignored": event or "empty"})

        bitrix_task_id = _bitrix_event_task_id(data)
        if event_u == "ONTASKCOMMENTADD":
            after = data.get("FIELDS_AFTER") if isinstance(data.get("FIELDS_AFTER"), dict) else {}
            bitrix_task_id = str(
                (after or {}).get("TASK_ID")
                or (after or {}).get("TASKID")
                or bitrix_task_id
                or ""
            )
        if not bitrix_task_id:
            return Response({"ok": False, "reason": "no_task_id"}, status=200)

        member_id = str(
            (auth or {}).get("member_id")
            or (auth or {}).get("MEMBER_ID")
            or request.data.get("member_id")
            or request.POST.get("member_id")
            or ""
        )
        domain = (
            (auth or {}).get("domain")
            or (auth or {}).get("DOMAIN")
            or request.data.get("DOMAIN")
            or ""
        )
        portal = None
        if member_id:
            portal = Portal.objects.filter(member_id=member_id).first()
        if portal is None and domain:
            portal = Portal.objects.filter(domain__icontains=str(domain).replace("https://", "")).first()
        if portal is None:
            return Response({"ok": False, "reason": "unknown_portal"}, status=200)

        app_token = str(
            (auth or {}).get("application_token")
            or (auth or {}).get("applicationToken")
            or request.data.get("application_token")
            or request.POST.get("application_token")
            or ""
        ).strip()
        portal_tok = (portal.application_token or "").strip()
        settings_tok = (settings.BITRIX_APPLICATION_TOKEN or "").strip()
        accepted = {t for t in (portal_tok, settings_tok) if t}
        if accepted:
            if not app_token or app_token not in accepted:
                logger.warning(
                    "Bitrix event forbidden portal=%s domain=%s has_token=%s",
                    portal.id,
                    portal.domain,
                    bool(app_token),
                )
                return Response({"ok": False, "reason": "forbidden"}, status=403)
            # Heal portal row if placement install stored APP_SID instead of app token
            if app_token and app_token != portal_tok and (
                not portal_tok or app_token == settings_tok
            ):
                portal.application_token = app_token
                portal.save(update_fields=["application_token", "updated_at"])
        elif not settings.DEBUG:
            return Response({"ok": False, "reason": "app_token_not_configured"}, status=403)
        elif app_token:
            portal.application_token = app_token
            portal.save(update_fields=["application_token", "updated_at"])

        # Refresh tokens from event auth when provided
        access = (auth or {}).get("access_token") or (auth or {}).get("AUTH_ID")
        if access:
            portal.access_token = access
            refresh = (auth or {}).get("refresh_token") or (auth or {}).get("REFRESH_ID")
            if refresh:
                portal.refresh_token = refresh
            portal.save(update_fields=["access_token", "refresh_token", "updated_at"])

        from board.comment_sync import ingest_bitrix_comment_event
        from board.project_sync import ingest_agency_bitrix_task
        from board.status_sync import handle_bitrix_task_update
        from portals.models import Portal as PortalModel

        if event_u == "ONTASKCOMMENTADD":
            result = ingest_bitrix_comment_event(
                portal=portal, bitrix_task_id=str(bitrix_task_id), data=data
            )
        elif event_u == "ONTASKADD":
            if portal.role == PortalModel.Role.AGENCY:
                result = ingest_agency_bitrix_task(
                    agency_portal=portal, bitrix_task_id=str(bitrix_task_id)
                )
            else:
                result = {"ok": True, "ignored": "client_task_add"}
        else:
            result = handle_bitrix_task_update(
                portal=portal, bitrix_task_id=str(bitrix_task_id), event_data=data
            )
        try:
            from board.realtime import publish_portal_event, publish_task_event
            from board.models import Task

            task_id = (result or {}).get("task_id")
            if task_id:
                t = Task.objects.select_related("project").filter(pk=task_id).first()
                if t:
                    publish_task_event(t, kind=event_u.lower())
            else:
                # Agency ingest may create project under a client portal
                client_id = (result or {}).get("client_portal_id")
                if client_id:
                    publish_portal_event(client_id, {"kind": event_u.lower()})
                elif portal.role == PortalModel.Role.CLIENT:
                    publish_portal_event(portal.id, {"kind": event_u.lower()})
        except Exception:
            pass
        return Response(result)

    def get(self, request):
        return Response({"ok": True, "service": "bitrix-events"})


def _parse_bitrix_event(request) -> tuple[str, dict, dict]:
    """Normalize Bitrix event POST (nested JSON or flat form fields)."""
    src = request.data if hasattr(request, "data") else {}
    post = request.POST

    event = src.get("event") or src.get("EVENT") or post.get("event") or post.get("EVENT") or ""

    data = src.get("data") if isinstance(src.get("data"), dict) else None
    auth = src.get("auth") if isinstance(src.get("auth"), dict) else None

    if data is None or auth is None:
        flat: dict = {}
        try:
            flat.update({k: post.get(k) for k in post.keys()})
        except Exception:
            pass
        if hasattr(src, "items"):
            try:
                for k, v in src.items():
                    if k not in flat:
                        flat[k] = v
            except Exception:
                pass
        nested = _unflatten_bitrix(flat)
        if data is None:
            data = nested.get("data") if isinstance(nested.get("data"), dict) else {}
        if auth is None:
            auth = nested.get("auth") if isinstance(nested.get("auth"), dict) else {}

    return str(event), data or {}, auth or {}


def _unflatten_bitrix(flat: dict) -> dict:
    """Turn data[FIELDS_AFTER][ID]=1 into nested dicts."""
    root: dict = {}
    for raw_key, value in flat.items():
        key = str(raw_key)
        if "[" not in key:
            root[key] = value
            continue
        parts: list[str] = []
        buf = ""
        i = 0
        while i < len(key):
            ch = key[i]
            if ch == "[":
                if buf:
                    parts.append(buf)
                    buf = ""
                j = key.find("]", i)
                if j < 0:
                    buf += ch
                    i += 1
                    continue
                parts.append(key[i + 1 : j])
                i = j + 1
            else:
                buf += ch
                i += 1
        if buf:
            parts.append(buf)
        cursor = root
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
            if not isinstance(cursor, dict):
                break
        else:
            if isinstance(cursor, dict):
                cursor[parts[-1]] = value
    return root


def _bitrix_event_task_id(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    after = data.get("FIELDS_AFTER") or data.get("fields_after") or {}
    if isinstance(after, dict):
        tid = after.get("ID") or after.get("id") or after.get("TASK_ID") or after.get("taskId")
        if tid not in (None, "", "0"):
            return str(tid)
    before = data.get("FIELDS_BEFORE") or data.get("fields_before") or {}
    if isinstance(before, dict):
        tid = before.get("ID") or before.get("id") or before.get("TASK_ID")
        if tid not in (None, "", "0"):
            return str(tid)
    tid = data.get("ID") or data.get("id") or data.get("TASK_ID") or data.get("taskId")
    return str(tid) if tid not in (None, "", "0") else ""


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

