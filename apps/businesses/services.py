from apps.businesses.models import BusinessMembership


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
    identity = f"{business.slug} {business.commercial_name}".lower()
    if "barber" in identity:
        return "barberia"
    return "salon"


def get_business_public_image_url(business):
    if not business.public_image:
        return ""
    try:
        return business.public_image.url
    except ValueError:
        return ""
