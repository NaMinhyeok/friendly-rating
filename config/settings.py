import json
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [
        item.strip() for item in os.getenv(name, default).split(",") if item.strip()
    ]


def env_json_object(name: str) -> dict[str, str]:
    value = os.getenv(name, "").strip()
    if not value:
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    return {
        key: item
        for key, item in parsed.items()
        if isinstance(key, str) and isinstance(item, str)
    }


_RAILWAY_ENVIRONMENT_MARKERS = (
    "RAILWAY_ENVIRONMENT_ID",
    "RAILWAY_ENVIRONMENT_NAME",
    # Keep the existing marker as a compatibility alias for deployed environments.
    "RAILWAY_ENVIRONMENT",
)
IS_RAILWAY = any(os.getenv(name) for name in _RAILWAY_ENVIRONMENT_MARKERS)
DEBUG = env_bool("DEBUG", default=not IS_RAILWAY)

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is disabled.")
    SECRET_KEY = "django-insecure-local-development-only"

ALLOWED_HOSTS = env_list(
    "ALLOWED_HOSTS",
    default="localhost,127.0.0.1,[::1],testserver",
)
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

railway_public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
if IS_RAILWAY:
    ALLOWED_HOSTS.append("healthcheck.railway.app")
if railway_public_domain:
    ALLOWED_HOSTS.append(railway_public_domain)
    CSRF_TRUSTED_ORIGINS.append(f"https://{railway_public_domain}")

public_base_url_default = (
    f"https://{railway_public_domain}/" if railway_public_domain else ""
)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", public_base_url_default).strip()
if PUBLIC_BASE_URL:
    PUBLIC_BASE_URL = f"{PUBLIC_BASE_URL.rstrip('/')}/"


# Firebase Cloud Messaging

_firebase_public_keys = {
    "apiKey",
    "appId",
    "authDomain",
    "messagingSenderId",
    "projectId",
    "storageBucket",
}
FIREBASE_WEB_CONFIG = {
    key: value
    for key, value in env_json_object("FIREBASE_WEB_CONFIG_JSON").items()
    if key in _firebase_public_keys
}
FIREBASE_VAPID_PUBLIC_KEY = os.getenv("FIREBASE_VAPID_PUBLIC_KEY", "").strip()
FIREBASE_SERVICE_ACCOUNT_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
PUSH_NOTIFICATIONS_ENABLED = env_bool("PUSH_NOTIFICATIONS_ENABLED")
_firebase_service_account = env_json_object("FIREBASE_SERVICE_ACCOUNT_JSON")

_firebase_required_public_keys = {
    "apiKey",
    "appId",
    "authDomain",
    "messagingSenderId",
    "projectId",
}
_firebase_public_config_is_valid = all(
    FIREBASE_WEB_CONFIG.get(key) for key in _firebase_required_public_keys
)
_firebase_vapid_key_is_valid = 80 <= len(FIREBASE_VAPID_PUBLIC_KEY) <= 180 and all(
    character.isalnum() or character in "-_" for character in FIREBASE_VAPID_PUBLIC_KEY
)
_firebase_service_account_is_valid = (
    _firebase_service_account.get("type") == "service_account"
    and all(
        _firebase_service_account.get(key)
        for key in ("project_id", "client_email", "private_key", "token_uri")
    )
    and _firebase_service_account.get("project_id")
    == FIREBASE_WEB_CONFIG.get("projectId")
)
_public_base_url = urlsplit(PUBLIC_BASE_URL)
_public_base_url_is_valid = (
    _public_base_url.scheme == "https"
    and bool(_public_base_url.netloc)
    and not _public_base_url.query
    and not _public_base_url.fragment
)
PUSH_NOTIFICATIONS_AVAILABLE = (
    PUSH_NOTIFICATIONS_ENABLED
    and _firebase_public_config_is_valid
    and _firebase_vapid_key_is_valid
    and _firebase_service_account_is_valid
    and _public_base_url_is_valid
)
if PUSH_NOTIFICATIONS_ENABLED and not PUSH_NOTIFICATIONS_AVAILABLE:
    raise ImproperlyConfigured(
        "Push notifications are enabled but Firebase credentials, VAPID key, "
        "matching project IDs, or an HTTPS PUBLIC_BASE_URL are invalid."
    )


# Private media uploads (Cloudflare R2)

MEDIA_UPLOADS_ENABLED = env_bool("MEDIA_UPLOADS_ENABLED")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "").strip().rstrip("/")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "").strip()
R2_REGION_NAME = os.getenv("R2_REGION_NAME", "auto").strip() or "auto"
MEDIA_UPLOAD_URL_TTL_SECONDS = int(os.getenv("MEDIA_UPLOAD_URL_TTL_SECONDS", "900"))
MEDIA_DOWNLOAD_URL_TTL_SECONDS = int(os.getenv("MEDIA_DOWNLOAD_URL_TTL_SECONDS", "300"))

_r2_endpoint = urlsplit(R2_ENDPOINT_URL)
_r2_configuration_is_valid = (
    _r2_endpoint.scheme == "https"
    and bool(_r2_endpoint.netloc)
    and not _r2_endpoint.query
    and not _r2_endpoint.fragment
    and all(
        (
            R2_ACCESS_KEY_ID,
            R2_SECRET_ACCESS_KEY,
            R2_BUCKET_NAME,
            R2_REGION_NAME,
        )
    )
    and 60 <= MEDIA_UPLOAD_URL_TTL_SECONDS <= 3600
    and 60 <= MEDIA_DOWNLOAD_URL_TTL_SECONDS <= 3600
)
MEDIA_UPLOADS_AVAILABLE = MEDIA_UPLOADS_ENABLED and _r2_configuration_is_valid
if MEDIA_UPLOADS_ENABLED and not MEDIA_UPLOADS_AVAILABLE:
    raise ImproperlyConfigured(
        "Media uploads are enabled but the R2 endpoint, bucket credentials, "
        "bucket name, region, or signed URL TTL settings are invalid."
    )


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "rest_framework",
    "drf_spectacular",
    "apps.ratings.apps.RatingsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AXES_HANDLER = "axes.handlers.database.AxesDatabaseHandler"
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_RESET_ON_SUCCESS = True
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
AXES_PASSWORD_FORM_FIELD = "pin"
AXES_CLIENT_IP_CALLABLE = "apps.ratings.security.get_client_ip_address"
AXES_LOCKOUT_CALLABLE = "apps.ratings.views.login_lockout"
AXES_HTTP_RESPONSE_CODE = 429
AXES_COOLOFF_MESSAGE = "로그인 시도가 너무 많습니다. 15분 후 다시 시도해 주세요."
AXES_DISABLE_ACCESS_LOG = True

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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


database_url = os.getenv("DATABASE_URL")
if not database_url and not DEBUG:
    raise ImproperlyConfigured("DATABASE_URL must be set when DEBUG is disabled.")

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600 if database_url else 0,
        conn_health_checks=True,
    )
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "ko-kr"

TIME_ZONE = "Asia/Seoul"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
WHITENOISE_MIMETYPES = {
    ".webmanifest": "application/manifest+json",
}

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.ratings.api.authentication.SameOriginSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "apps.ratings.api.parsers.BoundedJSONParser",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "apps.ratings.api.renderers.EnvelopeJSONRenderer",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.ratings.api.exceptions.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "우리 사이 API",
    "DESCRIPTION": "두 참가자의 관계 점수, 대화와 알림 기기 API",
    "VERSION": "1.0.0",
    "OAS_VERSION": "3.1.0",
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "ErrorResultType": ["ERROR"],
        "SuccessResultType": ["SUCCESS"],
        "RequestErrorType": ["REQUEST"],
        "AuthenticationErrorType": ["AUTHENTICATION"],
        "NotFoundValue": ["NOT_FOUND"],
    },
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=not DEBUG)
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SECURE_HSTS_SECONDS = int(
    os.getenv("SECURE_HSTS_SECONDS", "3600" if not DEBUG else "0")
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
