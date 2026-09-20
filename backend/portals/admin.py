from django.contrib import admin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django import forms

from .models import AgencyUserPreference, BitrixUser, Portal, PortalDealBinding, PortalLink


@admin.register(Portal)
class PortalAdmin(admin.ModelAdmin):
    list_display = ("name", "domain", "member_id", "role", "is_active", "updated_at")
    list_filter = ("role", "is_active")
    search_fields = ("name", "domain", "member_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PortalLink)
class PortalLinkAdmin(admin.ModelAdmin):
    list_display = (
        "agency_portal",
        "client_portal",
        "bitrix_company_id",
        "bitrix_group_id",
        "created_at",
    )
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
    search_fields = ("deal_id", "deal_title", "client_portal__name", "client_portal__domain")
    autocomplete_fields = ("agency_portal", "client_portal")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "agency_portal",
                    "client_portal",
                    "deal_id",
                    "deal_title",
                    "is_active",
                ),
                "description": (
                    "Сделки и пакет часов заполняются здесь вручную "
                    "(синхронизация с CRM Bitrix отключена)."
                ),
            },
        ),
        (
            "Часы",
            {"fields": ("paid_hours", "remaining_hours", "hours_overage_applied")},
        ),
        (
            "Служебные поля стадии",
            {
                "classes": ("collapse",),
                "fields": ("category_id", "stage_id", "stage_semantic"),
            },
        ),
    )


class BitrixUserAdminForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Пароль",
        required=False,
        widget=forms.PasswordInput,
        help_text="Оставьте пустым, чтобы не менять. Нужен для входа сотрудников агентства.",
    )
    password2 = forms.CharField(
        label="Пароль ещё раз",
        required=False,
        widget=forms.PasswordInput,
    )
    password = ReadOnlyPasswordHashField(
        label="Хэш пароля",
        required=False,
        help_text="Пароль хранится в виде хэша. Задайте новый ниже.",
    )

    class Meta:
        model = BitrixUser
        fields = (
            "portal",
            "bitrix_id",
            "username",
            "password",
            "name",
            "last_name",
            "email",
            "avatar_url",
            "is_admin",
        )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1") or ""
        p2 = cleaned.get("password2") or ""
        if p1 or p2:
            if p1 != p2:
                raise forms.ValidationError("Пароли не совпадают")
        username = (cleaned.get("username") or "").strip()
        portal = cleaned.get("portal")
        if username and portal and portal.role != Portal.Role.AGENCY:
            raise forms.ValidationError(
                "Логин/пароль можно задавать только пользователям агентского портала"
            )
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        raw = self.cleaned_data.get("password1") or ""
        if raw:
            user.set_password(raw)
        if not user.bitrix_id and user.username:
            user.bitrix_id = f"local-{user.username}"
        if commit:
            user.save()
        return user


@admin.register(BitrixUser)
class BitrixUserAdmin(admin.ModelAdmin):
    form = BitrixUserAdminForm
    list_display = ("display_name", "username", "portal", "bitrix_id", "email", "is_admin")
    list_filter = ("portal", "is_admin")
    search_fields = ("name", "last_name", "email", "bitrix_id", "username")
    autocomplete_fields = ("portal",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "portal",
                    "bitrix_id",
                    "username",
                    "password",
                    "password1",
                    "password2",
                ),
                "description": (
                    "Для сотрудников агентства укажите username и пароль. "
                    "bitrix_id можно оставить как local-<username>."
                ),
            },
        ),
        ("Профиль", {"fields": ("name", "last_name", "email", "avatar_url", "is_admin")}),
    )


@admin.register(AgencyUserPreference)
class AgencyUserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "favorite_client_ids", "updated_at")
    search_fields = ("user__name", "user__last_name", "user__bitrix_id", "user__username")
    autocomplete_fields = ("user",)
