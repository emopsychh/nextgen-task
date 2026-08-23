from django.db.models.signals import post_save
from django.dispatch import receiver

from portals.models import PortalDealBinding


@receiver(post_save, sender=PortalDealBinding, dispatch_uid="ensure_binding_work_report")
def ensure_binding_work_report(sender, instance, **kwargs):
    # Local import avoids an app-loading cycle between portals and board.
    from board.reports import ensure_report_for_binding

    ensure_report_for_binding(instance)
