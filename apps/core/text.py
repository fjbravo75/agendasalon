"""Text normalization helpers shared by domain models."""

import re
import unicodedata


def normalize_search_text(value: str) -> str:
    """Return a compact lowercase ASCII-ish value for duplicate detection."""
    raw_value = (value or "").strip().lower()
    decomposed = unicodedata.normalize("NFKD", raw_value)
    without_accents = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", without_accents).strip()
