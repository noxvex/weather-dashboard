from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ["SECRET_KEY"]

DEBUG = os.environ.get("DEBUG", "False") == "True"

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    ".railway.app",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "ingest",
    "notes",
]

AUTH_USER_MODEL = "accounts.CustomUser"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise must be immediately after SecurityMiddleware to serve compressed
    # static files in production without going through Django's view layer.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "weather_dashboard.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "weather_dashboard.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        env="DATABASE_URL",
        conn_max_age=600,
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "cs"
TIME_ZONE = "Europe/Prague"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Django looks here for your hand-written static files during development
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Open-Meteo endpoints — swap URLs + keys in .env only, zero code changes required
OPEN_METEO_BASE_URL = os.environ.get("OPEN_METEO_BASE_URL", "https://api.open-meteo.com")
OPEN_METEO_API_KEY = os.environ.get("OPEN_METEO_API_KEY", "")

OPEN_METEO_SEASONAL_URL = os.environ.get("OPEN_METEO_SEASONAL_URL", "https://seasonal-api.open-meteo.com")
OPEN_METEO_SEASONAL_API_KEY = os.environ.get("OPEN_METEO_SEASONAL_API_KEY", "")

OPEN_METEO_ARCHIVE_URL = os.environ.get("OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com")
OPEN_METEO_ARCHIVE_API_KEY = os.environ.get("OPEN_METEO_ARCHIVE_API_KEY", "")

OPEN_METEO_AIRQUALITY_URL = os.environ.get("OPEN_METEO_AIRQUALITY_URL", "https://air-quality-api.open-meteo.com")
OPEN_METEO_AIRQUALITY_API_KEY = os.environ.get("OPEN_METEO_AIRQUALITY_API_KEY", "")

# Auth redirects
LOGIN_URL = "/prihlaseni/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/prihlaseni/"

# Email — console backend for local dev; switch to SMTP in .env before go-live
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "weather@dashboard.local")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")

# ── Production security ───────────────────────────────────────────────────────

# Railway is a TLS-terminating proxy: it forwards the original scheme in this
# header so Django knows the request arrived over HTTPS, not HTTP.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Only send session and CSRF cookies over HTTPS (safe since Railway is HTTPS-only).
# Never enable SECURE_SSL_REDIRECT here — Railway's proxy already handles it and
# Django redirecting on top would create a redirect loop.
SESSION_COOKIE_SECURE = not DEBUG  # False in local dev, True in production
CSRF_COOKIE_SECURE = not DEBUG

# CSRF protection for HTTPS — without this every form POST in production gets a
# 403. Add a custom domain later via CSRF_TRUSTED_ORIGINS_EXTRA env var (comma-
# separated list of https:// URLs, e.g. "https://yoursite.cz").
CSRF_TRUSTED_ORIGINS = ["https://*.railway.app"]
_csrf_extra = os.environ.get("CSRF_TRUSTED_ORIGINS_EXTRA", "")
if _csrf_extra:
    CSRF_TRUSTED_ORIGINS += [o.strip() for o in _csrf_extra.split(",") if o.strip()]

# ── Static files — WhiteNoise for production ─────────────────────────────────

# CompressedManifestStaticFilesStorage: gzips files + appends content-hash to
# filenames so browsers cache forever. Requires `collectstatic` before start.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
