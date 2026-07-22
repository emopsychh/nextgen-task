from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from .models import BitrixUser, Portal


class PortalJWTAuthentication(JWTAuthentication):
    """JWT that carries portal/user claims for Bitrix-backed sessions."""

    def get_user(self, validated_token):
        portal_id = validated_token.get("portal_id")
        bitrix_user_id = validated_token.get("bitrix_user_id")
        if not portal_id or not bitrix_user_id:
            raise InvalidToken("Token missing portal claims")

        try:
            portal = Portal.objects.get(pk=portal_id, is_active=True)
            bitrix_user = BitrixUser.objects.get(portal=portal, bitrix_id=str(bitrix_user_id))
        except (Portal.DoesNotExist, BitrixUser.DoesNotExist) as exc:
            raise InvalidToken("Portal user not found") from exc

        # Attach a lightweight user-like object for DRF
        user = AnonymousPortalUser(portal=portal, bitrix_user=bitrix_user)
        return user


class AnonymousPortalUser:
    is_authenticated = True
    is_anonymous = False
    is_staff = False
    is_superuser = False

    def __init__(self, portal: Portal, bitrix_user: BitrixUser):
        self.portal = portal
        self.bitrix_user = bitrix_user
        self.pk = bitrix_user.pk
        self.id = bitrix_user.pk
        self.portal_id = portal.id

    @property
    def is_agency(self):
        return self.portal.role == Portal.Role.AGENCY

    @property
    def is_client(self):
        return self.portal.role == Portal.Role.CLIENT

    def __str__(self):
        return self.bitrix_user.display_name
