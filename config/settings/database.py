"""Database configuration helpers shared by production and operational tools."""

from urllib.parse import parse_qs, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured


def postgres_database_config(database_url: str) -> dict:
    if not database_url:
        raise ImproperlyConfigured("DJANGO_DATABASE_URL is required in production.")

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DJANGO_DATABASE_URL must use PostgreSQL.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ImproperlyConfigured("DJANGO_DATABASE_URL contains an invalid port.") from exc

    database_name = unquote(parsed.path.lstrip("/"))
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    host = parsed.hostname or ""
    if not all((database_name, user, password, host)):
        raise ImproperlyConfigured(
            "DJANGO_DATABASE_URL must include database, user, password and host."
        )

    query = parse_qs(parsed.query)
    sslmode = (query.get("sslmode") or ["require"])[-1]
    if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
        raise ImproperlyConfigured("DJANGO_DATABASE_URL contains an invalid sslmode.")

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": database_name,
        "USER": user,
        "PASSWORD": password,
        "HOST": host,
        "PORT": str(port or 5432),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"sslmode": sslmode},
    }
