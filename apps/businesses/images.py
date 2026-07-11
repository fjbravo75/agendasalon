from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


PUBLIC_IMAGE_MAX_EDGE = 2400
PUBLIC_IMAGE_MAX_INPUT_BYTES = 5 * 1024 * 1024
PUBLIC_IMAGE_MAX_INPUT_PIXELS = 16_000_000
PUBLIC_IMAGE_MAX_OUTPUT_BYTES = 5 * 1024 * 1024
PUBLIC_IMAGE_WEBP_QUALITY = 84
PUBLIC_IMAGE_WEBP_METHOD = 4


class PublicImageProcessingError(Exception):
    """La imagen no puede convertirse al formato público seguro."""


def sanitize_public_image(uploaded_image):
    """Orienta, limita y recodifica una imagen estática sin metadatos."""

    try:
        uploaded_image.seek(0)
        with Image.open(uploaded_image) as source:
            source.seek(0)
            source.load()
            normalized = ImageOps.exif_transpose(source)
            normalized.load()

            has_transparency = normalized.mode in {"RGBA", "LA"} or (
                normalized.mode == "P" and "transparency" in normalized.info
            )
            normalized = normalized.convert("RGBA" if has_transparency else "RGB")
            if max(normalized.size) > PUBLIC_IMAGE_MAX_EDGE:
                normalized.thumbnail(
                    (PUBLIC_IMAGE_MAX_EDGE, PUBLIC_IMAGE_MAX_EDGE),
                    Image.Resampling.LANCZOS,
                )

            output = BytesIO()
            normalized.save(
                output,
                format="WEBP",
                quality=PUBLIC_IMAGE_WEBP_QUALITY,
                method=PUBLIC_IMAGE_WEBP_METHOD,
                exact=has_transparency,
            )
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise PublicImageProcessingError from exc

    if output.tell() > PUBLIC_IMAGE_MAX_OUTPUT_BYTES:
        raise PublicImageProcessingError

    safe_stem = Path(uploaded_image.name or "imagen").stem[:80] or "imagen"
    sanitized = ContentFile(output.getvalue(), name=f"{safe_stem}.webp")
    sanitized.content_type = "image/webp"
    return sanitized
