from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ActivityFeedView,
    AttachmentViewSet,
    CommentViewSet,
    ProjectViewSet,
    SupportTicketViewSet,
    TaskViewSet,
    WorkReportViewSet,
)
from .stream import PortalStreamView, StreamTokenView, SyncCursorView

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")
router.register("tasks", TaskViewSet, basename="task")
router.register("comments", CommentViewSet, basename="comment")
router.register("attachments", AttachmentViewSet, basename="attachment")
router.register("reports", WorkReportViewSet, basename="report")
router.register("tickets", SupportTicketViewSet, basename="ticket")

urlpatterns = [
    path("activity/", ActivityFeedView.as_view(), name="activity-feed"),
    path("stream/", PortalStreamView.as_view(), name="portal-stream"),
    path("stream/token/", StreamTokenView.as_view(), name="stream-token"),
    path("sync/cursor/", SyncCursorView.as_view(), name="sync-cursor"),
    path("", include(router.urls)),
]
