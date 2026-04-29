"""
Django settings for the ELD Trip Planner backend.

All environment-specific values are read from environment variables via
django-environ. See .env.example for the full list of supported keys.
"""

from pathlib import Path
from urllib.parse import urlparse

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)

environ.Env.read_env(BASE_DIR / ".env")


# Core ------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-change-me")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Railway exposes the deployed hostname as RAILWAY_PUBLIC_DOMAIN once a domain
# is generated; appending it lets the app boot with no extra ALLOWED_HOSTS env.
RAILWAY_PUBLIC_DOMAIN = env("RAILWAY_PUBLIC_DOMAIN", default="")
if RAILWAY_PUBLIC_DOMAIN:
    ALLOWED_HOSTS.append(RAILWAY_PUBLIC_DOMAIN)

# Railway's platform healthcheck and internal proxies hit the container with
# Host headers that aren't always RAILWAY_PUBLIC_DOMAIN (e.g. *.up.railway.app
# or *.railway.internal). Allow any railway.app subdomain when running there.
if env("RAILWAY_ENVIRONMENT", default=""):
    ALLOWED_HOSTS.append(".railway.app")
    ALLOWED_HOSTS.append(".railway.internal")

CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
if RAILWAY_PUBLIC_DOMAIN:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RAILWAY_PUBLIC_DOMAIN}")

# Behind Railway's TLS-terminating proxy, request.is_secure() must look at the
# forwarded header instead of the raw socket.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# Applications ----------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "trips",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
ASGI_APPLICATION = "config.asgi.application"


# Database --------------------------------------------------------------------

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    ),
}


# Auth ------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization --------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# Static files ----------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# CORS ------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = [
    f"{parsed.scheme}://{parsed.netloc}"
    for raw in env.list("CORS_ALLOWED_ORIGINS", default=[])
    if (parsed := urlparse(raw)).netloc
]
CORS_ALLOW_CREDENTIALS = True


# Django REST Framework -------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # AllowAny keeps anonymous trips supported (spec §7.4); per-action
    # endpoints can tighten this with their own permission_classes.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 10,
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}


# Third-party services --------------------------------------------------------

OSRM_BASE_URL = env("OSRM_BASE_URL", default="https://router.project-osrm.org")
ORS_BASE_URL = env("ORS_BASE_URL", default="https://api.openrouteservice.org")
ORS_API_KEY = env("ORS_API_KEY", default="")
NOMINATIM_URL = env(
    "NOMINATIM_URL",
    default="https://nominatim.openstreetmap.org/search",
)
NOMINATIM_USER_AGENT = env(
    "NOMINATIM_USER_AGENT",
    default="eld-trip-planner/0.1 (contact: usama.dev0@gmail.com)",
)
