"""Phone helpers shared by accounts and customer records."""

from django.core.exceptions import ValidationError
import phonenumbers


def normalize_phone(value: str, default_region: str = "ES") -> str:
    """Return an E.164 phone number or raise ValidationError."""
    raw_value = (value or "").strip()
    if not raw_value:
        raise ValidationError("El telefono es obligatorio.")

    try:
        parsed = phonenumbers.parse(raw_value, default_region)
    except phonenumbers.NumberParseException as exc:
        raise ValidationError("El telefono no tiene un formato valido.") from exc

    if not phonenumbers.is_possible_number(parsed):
        raise ValidationError("El telefono no parece posible.")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
