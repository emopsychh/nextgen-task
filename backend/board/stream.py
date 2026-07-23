"""SSE stream + cursor polling for portal live updates."""

from __future__ import annotations

import json
import time

from django.conf import settings
from django.core import signing
from django.http import JsonResponse, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from portals.authentication import AnonymousPortalUser
from portals.models import BitrixUser, Portal
from portals.permissions import IsPortalAuthenticated, can_access_client_portal

from board.realtime import _redis, get_cursor, portal_channel

# Salt for the short-lived signed token used to authorise EventSource
# connections (which cannot carry an Authorization header).
STREAM_TOKEN_SALT = "board.stream.token.v1"


def _resolve_portal_user(portal_id, bitrix_user_id):
    if not portal_id or not bitrix_user_id:
        return None
    try:
        portal = Portal.objects.get(pk=portal_id, is_active=True)
        bitrix_user = BitrixUser.objects.get(portal=portal, bitrix_id=str(bitrix_user_id))
    except (Portal.DoesNotExist, BitrixUser.DoesNotExist):
        return None
    return AnonymousPortalUser(portal=portal, bitrix_user=bitrix_user)


def mint_stream_token(user) -> str:
    """Sign a short-lived capability identifying the portal user.

    Used instead of putting the long-lived app JWT in the SSE URL.
    """
    return signing.dumps(
        {"p": user.portal_id, "u": user.bitrix_user.bitrix_id},
        salt=STREAM_TOKEN_SALT,
    )


def _user_from_request(request):
    """Authenticate via DRF (Authorization header) or a signed stream token.

    The app JWT is only ever accepted from the Authorization header — never
    from the query string — so it cannot leak into access logs. EventSource,
    which cannot set headers, passes a short-lived signed `?t=` token instead.
    """
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False) and getattr(user, "portal", None):
        return user
    params = getattr(request, "query_params", None) or getattr(request, "GET", {})
    raw = (params.get("t") or "") if params is not None else ""
    if raw:
        try:
            data = signing.loads(
                raw, salt=STREAM_TOKEN_SALT, max_age=settings.STREAM_TOKEN_TTL
            )
        except signing.BadSignature:
            return None
        return _resolve_portal_user(data.get("p"), data.get("u"))
    return None


class StreamTokenView(APIView):
    """Mint a short-lived signed token for the EventSource connection.

    Authenticated with the normal app JWT (Authorization header); returns a
    capability scoped to the caller that expires after STREAM_TOKEN_TTL.
    """

    permission_classes = [IsPortalAuthenticated]

    def get(self, request):
        portal_id = request.query_params.get("portal")
        if not portal_id:
            return Response({"detail": "portal required"}, status=400)
        portal = Portal.objects.filter(pk=portal_id).first()
        if not portal or not can_access_client_portal(request.user, portal):
            return Response({"detail": "No access"}, status=403)
        return Response(
            {"t": mint_stream_token(request.user), "ttl": settings.STREAM_TOKEN_TTL}
        )


class SyncCursorView(APIView):
    """Lightweight version counter — FE polls when SSE unavailable.

    Authenticated with the app JWT via the Authorization header (standard DRF),
    so no credential is ever placed in the query string / logs.
    """

    permission_classes = [IsPortalAuthenticated]
    renderer_classes = [JSONRenderer]

    def get(self, request):
        user = request.user
        portal_id = request.query_params.get("portal")
        if not portal_id:
            return Response({"detail": "portal required"}, status=400)
        portal = Portal.objects.filter(pk=portal_id).first()
        if not portal or not can_access_client_portal(user, portal):
            return Response({"detail": "No access"}, status=403)
        return Response({"portal": int(portal_id), "v": get_cursor(int(portal_id))})


@method_decorator(csrf_exempt, name="dispatch")
class PortalStreamView(View):
    """
    Server-Sent Events for a portal. Heartbeat every ~15s.

    Plain Django View (not DRF APIView) so EventSource Accept: text/event-stream
    does not trigger DRF content-negotiation 406.
    """

    def get(self, request):
        user = _user_from_request(request)
        if not user:
            return JsonResponse({"detail": "Unauthorized"}, status=401)
        portal_id = request.GET.get("portal")
        if not portal_id:
            return JsonResponse({"detail": "portal required"}, status=400)
        portal = Portal.objects.filter(pk=portal_id).first()
        if not portal or not can_access_client_portal(user, portal):
            return JsonResponse({"detail": "No access"}, status=403)

        channel = portal_channel(int(portal_id))
        last_v = get_cursor(int(portal_id))

        def event_stream():
            yield f"data: {json.dumps({'hello': True, 'v': last_v})}\n\n"
            client = _redis()
            pubsub = None
            if client:
                try:
                    pubsub = client.pubsub(ignore_subscribe_messages=True)
                    pubsub.subscribe(channel)
                except Exception:
                    pubsub = None
            try:
                idle = 0
                while True:
                    message = None
                    if pubsub:
                        try:
                            message = pubsub.get_message(timeout=1.0)
                        except Exception:
                            message = None
                    if message and message.get("type") == "message":
                        data = message.get("data") or "{}"
                        yield f"data: {data}\n\n"
                        idle = 0
                    else:
                        idle += 1
                        if idle >= 15:
                            yield ": ping\n\n"
                            idle = 0
                        else:
                            time.sleep(1)
            finally:
                if pubsub:
                    try:
                        pubsub.unsubscribe(channel)
                        pubsub.close()
                    except Exception:
                        pass

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
