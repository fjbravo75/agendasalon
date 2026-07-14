import hashlib
import json

from django.db import migrations
from django.utils import timezone


DOCUMENT = {
    "kind": "legal_notice",
    "slug": "aviso-legal",
    "version": "2026.07.3",
    "title": "Aviso legal",
    "lead": "Información sobre la titularidad y las reglas de acceso a AgendaSalon.",
    "sections": [
        {
            "heading": "Titular y contacto",
            "paragraphs": [
                "La identidad y los datos de contacto disponibles para el contexto activo se muestran al inicio de este documento y proceden de la configuración del entorno.",
                "Las consultas relacionadas con privacidad deben dirigirse al correo específico indicado en esta página.",
            ],
        },
        {
            "heading": "Finalidad del sitio",
            "paragraphs": [
                "AgendaSalon ofrece una plataforma de gestión de citas para peluquerías, barberías y pequeños salones de belleza.",
                "El acceso debe realizarse de forma lícita y respetando las credenciales, permisos y datos de cada negocio.",
            ],
        },
        {
            "heading": "Propiedad intelectual y disponibilidad",
            "paragraphs": [
                "El código, la identidad visual, los textos propios y la estructura del producto están protegidos por la normativa aplicable.",
                "La plataforma aplica medidas razonables de continuidad y seguridad, sin garantizar la ausencia absoluta de interrupciones derivadas de mantenimiento o causas externas.",
            ],
        },
        {
            "heading": "Normativa aplicable",
            "paragraphs": [
                "Este servicio se interpreta conforme a la normativa española y europea que resulte aplicable, incluida la regulación sobre servicios digitales y protección de datos personales.",
            ],
        },
    ],
}


def _content_hash(document):
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def publish_legal_notice(apps, schema_editor):
    LegalDocument = apps.get_model("legal", "LegalDocument")
    LegalDocument.objects.filter(kind=DOCUMENT["kind"], is_active=True).update(
        is_active=False
    )
    LegalDocument.objects.update_or_create(
        kind=DOCUMENT["kind"],
        version=DOCUMENT["version"],
        defaults={
            "slug": DOCUMENT["slug"],
            "title": DOCUMENT["title"],
            "lead": DOCUMENT["lead"],
            "sections": DOCUMENT["sections"],
            "content_hash": _content_hash(DOCUMENT),
            "published_at": timezone.now(),
            "is_active": True,
        },
    )


def restore_previous_legal_notice(apps, schema_editor):
    LegalDocument = apps.get_model("legal", "LegalDocument")
    LegalDocument.objects.filter(
        kind=DOCUMENT["kind"], version=DOCUMENT["version"]
    ).update(is_active=False)
    LegalDocument.objects.filter(kind=DOCUMENT["kind"], version="2026.07").update(
        is_active=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("legal", "0005_customer_privacy_informed_party"),
    ]

    operations = [
        migrations.RunPython(publish_legal_notice, restore_previous_legal_notice),
    ]
