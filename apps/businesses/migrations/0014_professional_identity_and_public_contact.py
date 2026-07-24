import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Count


OPEN_STATUSES = ("new", "reviewing", "contacted")


def normalize_and_validate_existing_identities(apps, schema_editor):
    SignupRequest = apps.get_model("businesses", "BusinessSignupRequest")
    Membership = apps.get_model("businesses", "BusinessMembership")

    requests = list(SignupRequest.objects.all().only("pk", "email"))
    for signup_request in requests:
        signup_request.email_normalized = (signup_request.email or "").strip().lower()
    if requests:
        SignupRequest.objects.bulk_update(requests, ["email_normalized"])

    duplicate_open_phones = (
        SignupRequest.objects.filter(status__in=OPEN_STATUSES)
        .values("normalized_phone")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .exists()
    )
    duplicate_open_emails = (
        SignupRequest.objects.filter(status__in=OPEN_STATUSES)
        .exclude(email_normalized="")
        .values("email_normalized")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .exists()
    )
    duplicate_memberships = (
        Membership.objects.values("user_id")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .exists()
    )
    if duplicate_open_phones or duplicate_open_emails or duplicate_memberships:
        raise RuntimeError(
            "No se pueden activar las restricciones de identidad: existen "
            "duplicidades que requieren revisión manual."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0013_business_notification_email_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="businesssignuprequest",
            name="email_normalized",
            field=models.EmailField(
                blank=True,
                default="",
                editable=False,
                max_length=254,
                verbose_name="correo electrónico normalizado",
            ),
            preserve_default=False,
        ),
        migrations.RunPython(
            normalize_and_validate_existing_identities,
            migrations.RunPython.noop,
        ),
        migrations.CreateModel(
            name="PlatformPublicContact",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "email",
                    models.EmailField(
                        max_length=254,
                        verbose_name="correo público de contacto",
                    ),
                ),
                (
                    "phone",
                    models.CharField(
                        blank=True,
                        max_length=32,
                        verbose_name="teléfono público de contacto",
                    ),
                ),
                (
                    "phone_normalized",
                    models.CharField(
                        blank=True,
                        editable=False,
                        max_length=32,
                        verbose_name="teléfono público normalizado",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name="última actualización",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_platform_public_contact",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="actualizado por",
                    ),
                ),
            ],
            options={
                "verbose_name": "contacto público de plataforma",
                "verbose_name_plural": "contacto público de plataforma",
            },
        ),
        migrations.AddConstraint(
            model_name="businesssignuprequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=OPEN_STATUSES),
                fields=("normalized_phone",),
                name="signup_open_phone_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="businesssignuprequest",
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(status__in=OPEN_STATUSES)
                    & ~models.Q(email_normalized="")
                ),
                fields=("email_normalized",),
                name="signup_open_email_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="businessmembership",
            constraint=models.UniqueConstraint(
                fields=("user",),
                name="membership_user_unique",
            ),
        ),
    ]
