import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("SECRET_KEY", "unsafe-dev-key")
DEBUG = os.getenv("DEBUG", "0") == "1"
if not DEBUG and SECRET_KEY in ("", "unsafe-dev-key"):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "SECRET_KEY must be set to a strong random value when DEBUG=0"
    )
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "portals",
    "board",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # XFrameOptionsMiddleware omitted so the SPA can load inside Bitrix iframe
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

_default_sqlite = "sqlite:///" + str(BASE_DIR / "db.sqlite3").replace("\\", "/")
_database_url = os.getenv("DATABASE_URL", _default_sqlite)
# Relative sqlite:///db.sqlite3 → always resolve next to manage.py (backend/)
if _database_url in ("sqlite:///db.sqlite3", "sqlite://./db.sqlite3"):
    _database_url = _default_sqlite

DATABASES = {
    "default": dj_database_url.parse(_database_url, conn_max_age=600),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- Attachment access control -------------------------------------------
# Uploaded files are NEVER served publicly. They are delivered only through the
# authenticated `/api/attachments/<id>/download/` endpoint, gated by a signed,
# expiring capability token. In production nginx serves the bytes efficiently
# via X-Accel-Redirect from an `internal` location.
try:
    ATTACHMENT_URL_TTL = int(os.getenv("ATTACHMENT_URL_TTL", str(60 * 60 * 12)))  # 12h
except (TypeError, ValueError):
    ATTACHMENT_URL_TTL = 60 * 60 * 12
MEDIA_USE_X_ACCEL = os.getenv("MEDIA_USE_X_ACCEL", "0") == "1"
MEDIA_X_ACCEL_PREFIX = (
    os.getenv("MEDIA_X_ACCEL_PREFIX", "/_protected_media/").rstrip("/") + "/"
)

# --- Live sync (SSE) ------------------------------------------------------
# EventSource cannot send Authorization headers, so the SSE connection is
# authorised by a short-lived signed token minted from the user's JWT. The
# long-lived app JWT is NEVER placed in a URL (it would land in access logs).
try:
    STREAM_TOKEN_TTL = int(os.getenv("STREAM_TOKEN_TTL", "900"))  # 15 min
except (TypeError, ValueError):
    STREAM_TOKEN_TTL = 900

# Behind reverse proxy / nginx
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.getenv("CSRF_TRUSTED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "portals.authentication.PortalJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

from datetime import timedelta  # noqa: E402

try:
    _access_min = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
except (TypeError, ValueError):
    _access_min = 60
try:
    _refresh_days = int(os.getenv("REFRESH_TOKEN_DAYS", "7"))
except (TypeError, ValueError):
    _refresh_days = 7

SIMPLE_JWT = {
    # Short-lived access token: a leaked/logged token is useless within an hour.
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=_access_min),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=_refresh_days),
    # Rotate on every refresh and blacklist the old refresh token, so a stolen
    # refresh token stops working the moment the real client refreshes.
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_TASK_TRACK_STARTED = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_ALWAYS_EAGER = os.getenv("CELERY_TASK_ALWAYS_EAGER", "0") == "1"

BITRIX_CLIENT_ID = os.getenv("BITRIX_CLIENT_ID", "")
BITRIX_CLIENT_SECRET = os.getenv("BITRIX_CLIENT_SECRET", "")
BITRIX_APPLICATION_TOKEN = os.getenv("BITRIX_APPLICATION_TOKEN", "")
# Custom deal number fields (UF_CRM_…) in воронка «Сопровождение»
# Paid hours are never overwritten; remaining hours are decremented per closed timer session.
BITRIX_DEAL_PAID_HOURS_FIELD = os.getenv("BITRIX_DEAL_PAID_HOURS_FIELD", "").strip()
BITRIX_DEAL_REMAINING_HOURS_FIELD = os.getenv("BITRIX_DEAL_REMAINING_HOURS_FIELD", "").strip()
# Deal UF «Ссылка на портал Битрикс24» — auto-find deal by client portal domain
BITRIX_DEAL_PORTAL_LINK_FIELD = os.getenv("BITRIX_DEAL_PORTAL_LINK_FIELD", "").strip()
# Company UF «ID проекта» (Bitrix workgroup / GROUP_ID)
BITRIX_COMPANY_PROJECT_ID_FIELD = os.getenv("BITRIX_COMPANY_PROJECT_ID_FIELD", "").strip()
# CRM category (funnel) id for «Сопровождение»
BITRIX_ACCOMPANIMENT_CATEGORY_ID = os.getenv("BITRIX_ACCOMPANIMENT_CATEGORY_ID", "").strip()
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "http://localhost:8000").rstrip("/")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS", "0") == "1"
if DEV_AUTH_BYPASS and not DEBUG:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "DEV_AUTH_BYPASS must be 0 when DEBUG=0 — it disables authentication entirely"
    )
# Comma-separated Bitrix member_id and/or portal domains treated as agency.
# Everyone else defaults to client (no UI role picker).
AGENCY_MEMBER_IDS = os.getenv("AGENCY_MEMBER_IDS", "")
AGENCY_DOMAINS = os.getenv("AGENCY_DOMAINS", "")

