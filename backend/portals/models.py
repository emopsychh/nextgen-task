from django.db import models


class Portal(models.Model):
    class Role(models.TextChoices):
        AGENCY = "agency", "Agency"
        CLIENT = "client", "Client"
        UNKNOWN = "unknown", "Unknown"

    member_id = models.CharField(max_length=64, unique=True, db_index=True)
    domain = models.CharField(max_length=255)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.UNKNOWN)
    name = models.CharField(max_length=255, blank=True)
    organization = models.CharField(
        max_length=255,
        blank=True,
        help_text='Юридическое название организации, например АНО "ЧИКАГА"',
    )
    # IANA timezone for due dates / Bitrix DEADLINE wall clock (client portals).
    # Agency UI displays deadlines in Europe/Moscow regardless.
    timezone = models.CharField(max_length=64, default="Europe/Moscow", blank=True)
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    application_token = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "domain"]

    def __str__(self):
        return self.name or self.domain or self.member_id


class PortalLink(models.Model):
    agency_portal = models.ForeignKey(
        Portal,
        on_delete=models.CASCADE,
        related_name="client_links",
        limit_choices_to={"role": Portal.Role.AGENCY},
    )
    client_portal = models.ForeignKey(
        Portal,
        on_delete=models.CASCADE,
        related_name="agency_links",
        limit_choices_to={"role": Portal.Role.CLIENT},
    )
    # Agency CRM company + Bitrix workgroup (from company UF «ID проекта»)
    bitrix_company_id = models.CharField(max_length=64, blank=True)
    bitrix_group_id = models.CharField(max_length=64, blank=True)
    # Unused package hours after a won accompaniment deal — rolled into the next deal
    hours_credit = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, blank=True
    )
    hours_credit_source_deal_id = models.CharField(max_length=64, blank=True)
    hours_credit_source_title = models.CharField(max_length=500, blank=True)
    # Idempotency / repair after rollover
    hours_credit_applied_to_deal_id = models.CharField(max_length=64, blank=True)
    hours_credit_last_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, blank=True
    )
    hours_credit_last_source_deal_id = models.CharField(max_length=64, blank=True)
    # Closed-task hours above paid package — billed against the next deal
    hours_overage = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, blank=True
    )
    hours_overage_source_deal_id = models.CharField(max_length=64, blank=True)
    hours_overage_source_title = models.CharField(max_length=500, blank=True)
    hours_overage_applied_to_deal_id = models.CharField(max_length=64, blank=True)
    hours_overage_last_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, blank=True
    )
    hours_overage_last_source_deal_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("agency_portal", "client_portal")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.agency_portal} → {self.client_portal}"


class BitrixUser(models.Model):
    portal = models.ForeignKey(Portal, on_delete=models.CASCADE, related_name="users")
    bitrix_id = models.CharField(max_length=64)
    # Agency staff login (password auth). Client users stay Bitrix-only.
    username = models.CharField(max_length=150, unique=True, null=True, blank=True)
    password = models.CharField(max_length=128, blank=True)
    name = models.CharField(max_length=255, blank=True)
    last_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    avatar_url = models.URLField(blank=True)
    is_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("portal", "bitrix_id")
        ordering = ["name", "last_name"]

    def __str__(self):
        return f"{self.display_name} ({self.portal})"

    @property
    def display_name(self):
        full = f"{self.name} {self.last_name}".strip()
        return full or self.username or self.email or self.bitrix_id

    def set_password(self, raw_password: str) -> None:
        from django.contrib.auth.hashers import make_password

        self.password = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        from django.contrib.auth.hashers import check_password

        if not self.password or not raw_password:
            return False
        return check_password(raw_password, self.password)


class AgencyUserPreference(models.Model):
    """Per-agency-employee UI prefs (favorites are personal, not shared)."""

    user = models.OneToOneField(
        BitrixUser,
        on_delete=models.CASCADE,
        related_name="agency_preference",
    )
    favorite_client_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Prefs for {self.user_id}"


class PortalDealBinding(models.Model):
    """Links a client portal to an agency CRM deal (воронка «Сопровождение»)."""

    agency_portal = models.ForeignKey(
        Portal,
        on_delete=models.CASCADE,
        related_name="deal_bindings",
        limit_choices_to={"role": Portal.Role.AGENCY},
    )
    client_portal = models.ForeignKey(
        Portal,
        on_delete=models.CASCADE,
        related_name="accompaniment_deals",
        limit_choices_to={"role": Portal.Role.CLIENT},
    )
    deal_id = models.CharField(max_length=64)
    deal_title = models.CharField(max_length=500, blank=True)
    category_id = models.CharField(max_length=64, blank=True)
    stage_id = models.CharField(max_length=64, blank=True)
    stage_semantic = models.CharField(
        max_length=8,
        blank=True,
        help_text="Bitrix stage SEMANTICS: S=success, F=failure, empty=process",
    )
    paid_hours = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    remaining_hours = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    hourly_rate_rub = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Стоимость часа в рублях",
    )
    package_rub = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Размер пакета в рублях",
    )
    balance_rub = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Остаток баланса в рублях",
    )
    hours_overage_applied = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, blank=True
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["agency_portal", "client_portal"],
                condition=models.Q(is_active=True),
                name="uniq_active_deal_binding_per_client",
            ),
            # One CRM deal → at most one active client under the agency.
            models.UniqueConstraint(
                fields=["agency_portal", "deal_id"],
                condition=models.Q(is_active=True),
                name="uniq_active_deal_binding_per_deal",
            ),
        ]

    def __str__(self):
        return f"Deal #{self.deal_id} → {self.client_portal}"
