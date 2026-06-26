from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.phone import normalize_phone


class UserManager(BaseUserManager):
    """Create users identified by normalized phone number."""

    use_in_migrations = True

    def _create_user(self, normalized_phone, password, **extra_fields):
        if not normalized_phone:
            raise ValueError("El telefono normalizado es obligatorio.")

        normalized_phone = normalize_phone(normalized_phone)
        extra_fields.setdefault("phone", normalized_phone)
        email = extra_fields.get("email")
        if email:
            extra_fields["email"] = self.normalize_email(email)

        user = self.model(normalized_phone=normalized_phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, normalized_phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(normalized_phone, password, **extra_fields)

    def create_superuser(self, normalized_phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("El superusuario debe tener is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("El superusuario debe tener is_superuser=True.")

        return self._create_user(normalized_phone, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Internal SaaS user for professionals and superadministrators."""

    full_name = models.CharField("nombre completo", max_length=150)
    phone = models.CharField("telefono", max_length=32)
    normalized_phone = models.CharField(
        "telefono normalizado",
        max_length=32,
        unique=True,
        help_text="Telefono en formato E.164 usado para iniciar sesion.",
    )
    email = models.EmailField("email", blank=True)
    is_staff = models.BooleanField("staff", default=False)
    is_active = models.BooleanField("activo", default=True)
    date_joined = models.DateTimeField("fecha de alta", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "normalized_phone"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        ordering = ["full_name", "normalized_phone"]

    def save(self, *args, **kwargs):
        if self.phone and not self.normalized_phone:
            self.normalized_phone = normalize_phone(self.phone)
        if self.normalized_phone:
            self.normalized_phone = normalize_phone(self.normalized_phone)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name or self.normalized_phone

# Create your models here.
