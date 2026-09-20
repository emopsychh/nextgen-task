from django.contrib import admin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django import forms

from .models import AgencyUserPreference, BitrixUser, Portal, PortalDealBinding, PortalLink


@admin.register(Portal)
class PortalAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "domain", "member_id", "role", "is_active", "updated_at")
    list_filter = ("role", "is_active")
    search_fields = ("name", "organization", "domain", "member_id")
    readonly_fields = ("created_at", "updated_at")
    fields = (
        "member_id",
        "domain",
        "role",
        "name",
        "organization",
        "timezone",
        "is_active",
        "created_at",
        "updated_at",
    )


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
        "hourly_rate_rub",
        "package_rub",
        "balance_rub",
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
                    "Пакет задаётся в рублях: стоимость часа и баланс. "
                    "Часы считаются автоматически (баланс ÷ ставка)."
                ),
            },
        ),
        (
            "Баланс (₽)",
            {
                "fields": (
                    "hourly_rate_rub",
                    "package_rub",
                    "balance_rub",
                    "paid_hours",
                    "remaining_hours",
                    "hours_overage_applied",
                ),
            },
        ),
        (
            "Служебные поля стадии",
            {
                "classes": ("collapse",),
                "fields": ("category_id", "stage_id", "stage_semantic"),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        from portals.deal_money import hours_from_money

        rate = obj.hourly_rate_rub
        if rate is not None and rate > 0:
            if obj.package_rub is not None:
                obj.paid_hours = hours_from_money(obj.package_rub, rate)
            if obj.balance_rub is not None:
                obj.remaining_hours = hours_from_money(obj.balance_rub, rate)
            elif obj.package_rub is not None and obj.balance_rub is None:
                obj.balance_rub = obj.package_rub
                obj.remaining_hours = hours_from_money(obj.balance_rub, rate)
        super().save_model(request, obj, form, change)


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
        if username and portal and portal.role not in (
            Portal.Role.AGENCY,
            Portal.Role.CLIENT,
        ):
            raise forms.ValidationError(
                "Логин/пароль можно задавать только пользователям agency/client портала"
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
                    "Укажите username и пароль для входа через веб "
                    "(сотрудники агентства и клиенты). "
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
