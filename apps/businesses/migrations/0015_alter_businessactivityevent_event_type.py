from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0014_professional_identity_and_public_contact"),
    ]

    operations = [
        migrations.AlterField(
            model_name="businessactivityevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("appointment_created", "Cita creada"),
                    ("appointment_cancelled", "Cita cancelada"),
                    ("appointment_completed", "Cita atendida"),
                    ("appointment_no_show", "Ausencia registrada"),
                    ("service_created", "Servicio creado"),
                    ("service_updated", "Servicio actualizado"),
                    ("service_paused", "Servicio pausado"),
                    ("service_reactivated", "Servicio reactivado"),
                    ("availability_created", "Horario creado"),
                    ("availability_updated", "Horario actualizado"),
                    ("availability_paused", "Horario pausado"),
                    ("availability_reactivated", "Horario reactivado"),
                    ("closure_created", "Cierre creado"),
                    ("closure_updated", "Cierre actualizado"),
                    ("closure_paused", "Cierre pausado"),
                    ("closure_reactivated", "Cierre reactivado"),
                    ("work_line_created", "Línea creada"),
                    ("work_line_updated", "Línea actualizada"),
                    ("work_line_paused", "Línea pausada"),
                    ("work_line_reactivated", "Línea reactivada"),
                    ("business_created", "Negocio creado"),
                    ("business_updated", "Negocio actualizado"),
                    ("business_paused", "Negocio pausado"),
                    ("business_reactivated", "Negocio reactivado"),
                    ("public_booking_enabled", "Reserva pública activada"),
                    ("public_booking_disabled", "Reserva pública pausada"),
                    ("visual_settings_updated", "Apariencia actualizada"),
                    ("notification_settings_updated", "Avisos actualizados"),
                    ("membership_created", "Acceso profesional creado"),
                    ("membership_paused", "Acceso profesional pausado"),
                    ("membership_reactivated", "Acceso profesional reactivado"),
                    ("client_invitation_created", "Invitación de cliente creada"),
                    ("client_invitation_revoked", "Invitación de cliente revocada"),
                    ("client_access_activated", "Cuenta de cliente activada"),
                    ("client_records_merged", "Fichas de cliente unificadas"),
                    (
                        "client_merge_review_dismissed",
                        "Coincidencia de clientes descartada",
                    ),
                    (
                        "national_holidays_enabled",
                        "Festivos nacionales aplicados",
                    ),
                    (
                        "national_holidays_disabled",
                        "Festivos nacionales desactivados",
                    ),
                    (
                        "legal_documentation_accepted",
                        "Documentación legal aceptada",
                    ),
                    (
                        "data_rights_request_updated",
                        "Solicitud de derechos actualizada",
                    ),
                ],
                max_length=40,
                verbose_name="tipo de evento",
            ),
        ),
    ]
