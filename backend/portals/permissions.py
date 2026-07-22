from rest_framework.permissions import BasePermission

from .models import Portal, PortalLink


class IsPortalAuthenticated(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and getattr(request.user, "is_authenticated", False) and getattr(request.user, "portal", None))


class IsAgencyPortal(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(getattr(user, "is_agency", False))


def linked_client_portal_ids(agency_portal: Portal):
    return PortalLink.objects.filter(agency_portal=agency_portal).values_list(
        "client_portal_id", flat=True
    )


def can_access_client_portal(user, client_portal: Portal) -> bool:
    if not user or not getattr(user, "portal", None):
        return False
    if user.portal_id == client_portal.id:
        return True
    if user.is_agency:
        return PortalLink.objects.filter(
            agency_portal=user.portal,
            client_portal=client_portal,
        ).exists()
    return False
