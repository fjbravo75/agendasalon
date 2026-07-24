from django.conf import settings
from django.core.exceptions import ValidationError
from django.templatetags.static import static

from apps.businesses.models import (
    BusinessMembership,
    PlatformPublicContact,
    PlatformSettings,
)
from apps.core.phone import normalize_phone


def get_active_memberships_for_user(user):
    if not getattr(user, "is_authenticated", False):
        return BusinessMembership.objects.none()

    return (
        BusinessMembership.objects.select_related("business")
        .filter(
            user=user,
            is_active=True,
            business__is_active=True,
        )
        .order_by("business__commercial_name", "pk")
    )


def get_primary_business_for_user(user):
    if not getattr(user, "is_authenticated", False) or user.is_superuser:
        return None

    membership = get_active_memberships_for_user(user).first()
    if membership is None:
        return None
    return membership.business


def user_has_active_business(user):
    return get_active_memberships_for_user(user).exists()


def get_business_visual_theme(business):
    if not business.public_images.filter(is_selected=True).exists():
        return business.public_image_preset
    identity = f"{business.slug} {business.commercial_name}".lower()
    if "barber" in identity:
        return "barberia"
    return "salon"


def get_business_public_image_url(business):
    selected_image = business.public_images.filter(is_selected=True).first()
    if selected_image is not None:
        try:
            return selected_image.image.url
        except ValueError:
            pass

    has_gallery_history = business.public_images.exists()
    if business.public_image and not has_gallery_history:
        try:
            return business.public_image.url
        except ValueError:
            pass

    preset_images = {
        business.PublicImagePreset.SALON: "img/customer-login-peluqueria-mari-bg.webp",
        business.PublicImagePreset.BARBERSHOP: "img/customer-login-barberia-norte-bg-v2.webp",
    }
    return static(preset_images[business.public_image_preset])


def get_platform_settings():
    return (
        PlatformSettings.objects.prefetch_related("login_images")
        .filter(pk=PlatformSettings.SINGLETON_PK)
        .first()
        or PlatformSettings(pk=PlatformSettings.SINGLETON_PK)
    )


def get_platform_public_contact():
    contact = PlatformPublicContact.objects.filter(
        pk=PlatformPublicContact.SINGLETON_PK
    ).first()
    if contact is not None:
        return contact

    phone = getattr(settings, "AGENDA_PLATFORM_CONTACT_PHONE", "").strip()
    try:
        phone_normalized = normalize_phone(phone) if phone else ""
    except ValidationError:
        phone = ""
        phone_normalized = ""
    return PlatformPublicContact(
        pk=PlatformPublicContact.SINGLETON_PK,
        email=settings.AGENDA_PLATFORM_CONTACT_EMAIL,
        phone=phone,
        phone_normalized=phone_normalized,
    )


def get_platform_login_image_url(platform_settings=None):
    platform_settings = platform_settings or get_platform_settings()
    selected_image = platform_settings.login_images.filter(is_selected=True).first()
    if selected_image is not None:
        try:
            return selected_image.image.url
        except ValueError:
            pass

    preset_images = {
        PlatformSettings.LoginImagePreset.AGENDASALON: "img/agendasalon-internal-login-bg.webp",
        PlatformSettings.LoginImagePreset.SALON: "img/customer-login-peluqueria-mari-bg.webp",
        PlatformSettings.LoginImagePreset.BARBERSHOP: (
            "img/customer-login-barberia-norte-bg-v2.webp"
        ),
    }
    return static(preset_images[platform_settings.login_image_preset])
