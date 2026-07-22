from django.contrib import admin

from .models import BitrixUser, Portal, PortalDealBinding, PortalLink


@admin.register(Portal)
class PortalAdmin(admin.ModelAdmin):
    list_display = ("name", "domain", "member_id", "role", "is_active", "updated_at")
    list_filter = ("role", "is_active")
    search_fields = ("name", "domain", "member_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PortalLink)
class PortalLinkAdmin(admin.ModelAdmin):
    list_display = ("agency_portal", "client_portal", "created_at")
    autocomplete_fields = ("agency_portal", "client_portal")


@admin.register(PortalDealBinding)
class PortalDealBindingAdmin(admin.ModelAdmin):
    list_display = (
        "deal_id",
        "deal_title",
        "paid_hours",
        "remaining_hours",
        "client_portal",
        "agency_portal",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active", "agency_portal")
    search_fields = ("deal_id", "deal_title")
    autocomplete_fields = ("agency_portal", "client_portal")


@admin.register(BitrixUser)
class BitrixUserAdmin(admin.ModelAdmin):
    list_display = ("display_name", "portal", "bitrix_id", "email", "is_admin")
    list_filter = ("portal", "is_admin")
    search_fields = ("name", "last_name", "email", "bitrix_id")
