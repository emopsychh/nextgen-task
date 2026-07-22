from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BitrixAuthView,
    BitrixInstallView,
    DevAuthView,
    MeView,
    PortalDealBindingViewSet,
    PortalLinkViewSet,
    PortalViewSet,
    app_entry,
)

router = DefaultRouter()
router.register("portals", PortalViewSet, basename="portal")
router.register("portal-links", PortalLinkViewSet, basename="portal-link")
router.register("deal-bindings", PortalDealBindingViewSet, basename="deal-binding")

urlpatterns = [
    path("bitrix/install/", BitrixInstallView.as_view(), name="bitrix-install"),
    path("bitrix/auth/", BitrixAuthView.as_view(), name="bitrix-auth"),
    path("bitrix/entry/", app_entry, name="bitrix-entry"),
    path("auth/dev/", DevAuthView.as_view(), name="dev-auth"),
    path("me/", MeView.as_view(), name="me"),
    path("", include(router.urls)),
]
