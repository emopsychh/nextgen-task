"""SSE stream + cursor polling for portal live updates."""

from __future__ import annotations

import json
import time

from django.http import StreamingHttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from portals.authentication import AnonymousPortalUser
from portals.models import BitrixUser, Portal
from portals.permissions import can_access_client_portal

from board.realtime import _redis, get_cursor, portal_channel


def _user_from_request(request):
    """Support Authorization header or ?access_token= for EventSource."""
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False) and getattr(user, "portal", None):
        return user
    raw = (
        request.query_params.get("access_token")
        or request.query_params.get("token")
        or ""
    )
    if not raw:
        auth = request.META.get("HTTP_AUTHORIZATION") or ""
        if auth.lower().startswith("bearer "):
            raw = auth.split(" ", 1)[1].strip()
    if not raw:
        return None
    try:
        token = AccessToken(raw)
        portal_id = token.get("portal_id")
        bitrix_user_id = token.get("bitrix_user_id")
        if not portal_id or not bitrix_user_id:
            return None
        portal = Portal.objects.get(pk=portal_id, is_active=True)
        bitrix_user = BitrixUser.objects.get(portal=portal, bitrix_id=str(bitrix_user_id))
        return AnonymousPortalUser(portal=portal, bitrix_user=bitrix_user)
    except Exception:
        return None


class SyncCursorView(APIView):
    """Lightweight version counter — FE polls when SSE unavailable."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        user = _user_from_request(request)
        if not user:
            return Response({"detail": "Unauthorized"}, status=401)
        portal_id = request.query_params.get("portal")
        if not portal_id:
            return Response({"detail": "portal required"}, status=400)
        portal = Portal.objects.filter(pk=portal_id).first()
        if not portal or not can_access_client_portal(user, portal):
            return Response({"detail": "No access"}, status=403)
        return Response({"portal": int(portal_id), "v": get_cursor(int(portal_id))})


class PortalStreamView(APIView):
    """Server-Sent Events for a portal. Heartbeat every ~15s."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        user = _user_from_request(request)
        if not user:
            return Response({"detail": "Unauthorized"}, status=401)
        portal_id = request.query_params.get("portal")
        if not portal_id:
            return Response({"detail": "portal required"}, status=400)
        portal = Portal.objects.filter(pk=portal_id).first()
        if not portal or not can_access_client_portal(user, portal):
            return Response({"detail": "No access"}, status=403)

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
