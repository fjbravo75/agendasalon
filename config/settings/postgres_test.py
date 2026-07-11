"""Test settings for exercising the full suite against PostgreSQL."""

from .prod import *  # noqa: F403


SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
