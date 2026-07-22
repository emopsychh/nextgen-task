from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ActivityFeedView,
    AttachmentViewSet,
    CommentViewSet,
    ProjectViewSet,
    TaskViewSet,
)

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")
router.register("tasks", TaskViewSet, basename="task")
router.register("comments", CommentViewSet, basename="comment")
router.register("attachments", AttachmentViewSet, basename="attachment")

urlpatterns = [
    path("activity/", ActivityFeedView.as_view(), name="activity-feed"),
    path("", include(router.urls)),
]
