from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    BitrixAuthView,
    BitrixEventView,
    BitrixInstallView,
    ChangePasswordView,
    DevAuthView,
    MePreferencesView,
    MeView,
    PasswordAuthView,
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
    path("bitrix/events/", BitrixEventView.as_view(), name="bitrix-events"),
    path("bitrix/entry/", app_entry, name="bitrix-entry"),
    path("auth/dev/", DevAuthView.as_view(), name="dev-auth"),
    path("auth/login/", PasswordAuthView.as_view(), name="password-auth"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("me/preferences/", MePreferencesView.as_view(), name="me-preferences"),
    path("", include(router.urls)),
]
