from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("full_name", "normalized_phone")
    list_display = (
        "normalized_phone",
        "full_name",
        "email",
        "is_staff",
        "is_active",
    )
    list_filter = ("is_staff", "is_active", "is_superuser", "groups")
    search_fields = ("full_name", "phone", "normalized_phone", "email")
    fieldsets = (
        (None, {"fields": ("normalized_phone", "password")}),
        ("Datos personales", {"fields": ("full_name", "phone", "email")}),
        (
            "Permisos",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Fechas", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "normalized_phone",
                    "full_name",
                    "phone",
                    "email",
                    "password1",
                    "password2",
                    "is_staff",
                    "is_superuser",
                    "is_active",
                ),
            },
        ),
    )

# Register your models here.
