"""Base settings shared by all AgendaSalon environments."""

from pathlib import Path
import os



BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "agendasalon-dev-secret-key-change-me-outside-production",
)

DEBUG = False

ALLOWED_HOSTS: list[str] = []

CSRF_TRUSTED_ORIGINS: list[str] = []
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"

TRUSTED_PROXY_IPS = {
    ip.strip()
    for ip in os.environ.get("DJANGO_TRUSTED_PROXY_IPS", "").split(",")
    if ip.strip()
}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.accounts",
    "apps.businesses",
    "apps.customers",
    "apps.booking",
    "apps.holidays",
    "apps.notifications",
    "apps.dashboards",
    "apps.legal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.legal.middleware.ProfessionalLegalOnboardingMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

_CSP_COMMON_DIRECTIVES = [
    "default-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "style-src-elem 'self' https://fonts.googleapis.com",
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data: https://fonts.gstatic.com",
    "connect-src 'self'",
    "media-src 'self'",
    "manifest-src 'self'",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
]

CONTENT_SECURITY_POLICY = "; ".join(
    [
        *_CSP_COMMON_DIRECTIVES,
        "script-src 'self'",
        "script-src-elem 'self'",
        "script-src-attr 'none'",
    ]
)

ADMIN_CONTENT_SECURITY_POLICY = "; ".join(
    [
        *_CSP_COMMON_DIRECTIVES,
        "script-src 'self' 'unsafe-inline'",
        "script-src-elem 'self' 'unsafe-inline'",
        "script-src-attr 'none'",
    ]
)

PERMISSIONS_POLICY = (
    "browsing-topics=(), camera=(), geolocation=(), microphone=(), payment=(), usb=()"
)

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
                "apps.businesses.context_processors.professional_appearance",
                "apps.legal.context_processors.legal_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_USER_MODEL = "accounts.User"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "es-es"

TIME_ZONE = "Europe/Madrid"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/cuenta/entrar/"
LOGIN_REDIRECT_URL = "/profesional/"
LOGOUT_REDIRECT_URL = "/cuenta/desconectado/"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

AGENDA_PLATFORM_LEGAL_NAME = os.environ.get(
    "AGENDA_PLATFORM_LEGAL_NAME",
    "AgendaSalon · demostración académica",
)
AGENDA_PLATFORM_TAX_ID = os.environ.get("AGENDA_PLATFORM_TAX_ID", "")
AGENDA_PLATFORM_LEGAL_ADDRESS = os.environ.get(
    "AGENDA_PLATFORM_LEGAL_ADDRESS",
    "",
)
AGENDA_PLATFORM_PRIVACY_EMAIL = os.environ.get(
    "AGENDA_PLATFORM_PRIVACY_EMAIL",
    "privacidad@agendasalon.local",
)
AGENDA_PLATFORM_WEBSITE = os.environ.get(
    "AGENDA_PLATFORM_WEBSITE",
    "http://127.0.0.1:8012",
)
AGENDA_PLATFORM_LEGAL_DEMO = os.environ.get(
    "AGENDA_PLATFORM_LEGAL_DEMO",
    "1",
).strip().lower() in {"1", "true", "yes", "on"}
AGENDA_BACKUP_SCHEDULE_CONFIGURED = False
