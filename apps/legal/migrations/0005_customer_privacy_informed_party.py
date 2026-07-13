from django.db import migrations, models


def populate_informed_party_names(apps, schema_editor):
    CustomerPrivacyEvidence = apps.get_model("legal", "CustomerPrivacyEvidence")
    evidence_rows = list(
        CustomerPrivacyEvidence.objects.select_related("business_client").all()
    )
    for evidence in evidence_rows:
        evidence.informed_party_name_snapshot = evidence.business_client.full_name
    if evidence_rows:
        CustomerPrivacyEvidence.objects.bulk_update(
            evidence_rows,
            ["informed_party_name_snapshot"],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("legal", "0004_legal_documents_2026_07_2"),
    ]

    operations = [
        migrations.AddField(
            model_name="customerprivacyevidence",
            name="informed_party_name_snapshot",
            field=models.CharField(
                default="",
                max_length=160,
                verbose_name="nombre de la persona informada",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="customerprivacyevidence",
            name="informed_party_type",
            field=models.CharField(
                choices=[
                    ("client", "Cliente"),
                    ("authorized_person", "Persona autorizada"),
                ],
                default="client",
                max_length=24,
                verbose_name="persona informada",
            ),
        ),
        migrations.AlterField(
            model_name="customerprivacyevidence",
            name="channel",
            field=models.CharField(
                choices=[
                    ("online_registration", "Registro online"),
                    ("client_invitation", "Invitación online"),
                    ("booking", "Confirmación de reserva"),
                    ("phone", "Teléfono"),
                    ("whatsapp", "WhatsApp"),
                    ("in_person", "En el establecimiento"),
                    ("email", "Correo electrónico"),
                    ("other", "Otro canal"),
                ],
                max_length=24,
                verbose_name="canal",
            ),
        ),
        migrations.RunPython(populate_informed_party_names, migrations.RunPython.noop),
    ]
