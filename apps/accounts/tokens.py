from django.contrib.auth.tokens import PasswordResetTokenGenerator


class ProfessionalEmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """Token de correo que no se invalida por iniciar o cerrar sesión."""

    key_salt = "apps.accounts.tokens.ProfessionalEmailVerificationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        verified_at = user.email_verified_at
        if verified_at is not None:
            verified_at = verified_at.replace(microsecond=0, tzinfo=None)
        return (
            f"{user.pk}{user.password}{timestamp}{user.email_normalized}"
            f"{verified_at}{user.email_verification_required}"
        )


professional_email_verification_token_generator = (
    ProfessionalEmailVerificationTokenGenerator()
)
