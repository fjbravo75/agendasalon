from django.core.exceptions import ValidationError


NON_ROUTABLE_EMAIL_SUFFIXES = (".invalid", ".local", ".test", ".example", ".localhost")


def normalize_and_validate_routable_email(email):
    """Normalize an interactive email and reject domains that cannot receive mail."""
    normalized_email = (email or "").strip().lower()
    domain = normalized_email.rpartition("@")[2]
    if domain == "localhost" or domain.endswith(NON_ROUTABLE_EMAIL_SUFFIXES):
        raise ValidationError(
            "Usa un correo real que pueda recibir mensajes; los dominios locales o "
            "reservados no son válidos aquí."
        )
    return normalized_email
