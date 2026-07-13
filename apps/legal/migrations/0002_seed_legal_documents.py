import hashlib
import json

from django.db import migrations
from django.utils import timezone


DOCUMENTS = (
    {
        "kind": "legal_notice",
        "slug": "aviso-legal",
        "version": "2026.07",
        "title": "Aviso legal",
        "lead": "Información sobre la titularidad y las reglas de acceso a AgendaSalon.",
        "sections": [
            {
                "heading": "Titular y contacto",
                "paragraphs": [
                    "La identidad, el domicilio, la identificación fiscal y el canal de contacto del titular se muestran al inicio de este documento y proceden de la configuración del entorno.",
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
    },
    {
        "kind": "platform_privacy",
        "slug": "privacidad-plataforma",
        "version": "2026.07",
        "title": "Privacidad de AgendaSalon",
        "lead": "Cómo trata AgendaSalon los datos de propietarios, profesionales y personas que contactan con la plataforma.",
        "sections": [
            {
                "heading": "Responsable y alcance",
                "paragraphs": [
                    "El titular de AgendaSalon actúa como responsable de los datos de las cuentas profesionales, la seguridad del servicio, el soporte y la relación con los negocios.",
                    "Los datos de clientes y citas de cada salón se tratan por cuenta de ese negocio y se regulan además mediante el acuerdo de encargo de tratamiento.",
                ],
            },
            {
                "heading": "Datos, finalidades y base jurídica",
                "paragraphs": [
                    "Se tratan datos identificativos y de contacto, credenciales protegidas, pertenencia a negocios, registros de seguridad y comunicaciones de soporte.",
                    "Las finalidades son gestionar el acceso, prestar el servicio contratado, mantener su seguridad, atender incidencias y cumplir obligaciones legales. Las bases jurídicas son la ejecución del contrato, las medidas precontractuales, el cumplimiento de obligaciones y, cuando corresponda, el interés legítimo debidamente ponderado.",
                ],
            },
            {
                "heading": "Destinatarios y proveedores",
                "paragraphs": [
                    "Los datos no se venden. Pueden acceder proveedores necesarios para alojamiento, copias, comunicaciones o mantenimiento bajo instrucciones, garantías y contratos adecuados.",
                    "Cualquier transferencia internacional se documentará y se apoyará en un mecanismo válido antes de producirse.",
                ],
            },
            {
                "heading": "Conservación y derechos",
                "paragraphs": [
                    "Los datos se conservan durante la relación con el negocio y posteriormente durante los plazos necesarios para atender obligaciones o responsabilidades. Los registros de seguridad se conservan de forma limitada y proporcional.",
                    "Las personas pueden solicitar acceso, rectificación, supresión, oposición, limitación o portabilidad mediante el canal de privacidad indicado, así como reclamar ante la Agencia Española de Protección de Datos.",
                ],
            },
        ],
    },
    {
        "kind": "terms",
        "slug": "condiciones-servicio",
        "version": "2026.07",
        "title": "Condiciones del servicio",
        "lead": "Condiciones que ordenan la relación entre AgendaSalon y cada negocio usuario.",
        "sections": [
            {
                "heading": "Objeto y cuenta profesional",
                "paragraphs": [
                    "AgendaSalon facilita herramientas para configurar servicios, horarios, cierres, clientes y citas. El negocio mantiene la responsabilidad sobre su actividad profesional y la exactitud de la información que publica.",
                    "Las credenciales son personales. El negocio debe comunicar accesos indebidos y mantener actualizados sus profesionales autorizados.",
                ],
            },
            {
                "heading": "Uso responsable",
                "paragraphs": [
                    "No debe incorporarse información sanitaria, especialmente protegida o ajena a la gestión ordinaria de citas. Las notas internas deben limitarse a información operativa necesaria.",
                    "Queda prohibido intentar acceder a otros negocios, alterar el servicio, introducir contenido ilícito o utilizar los datos para finalidades incompatibles.",
                ],
            },
            {
                "heading": "Disponibilidad, cambios y suspensión",
                "paragraphs": [
                    "El servicio puede experimentar tareas de mantenimiento o incidencias. Se aplicarán medidas razonables para preservar disponibilidad, integridad y restauración.",
                    "Un acceso puede pausarse ante incumplimientos, riesgo de seguridad o petición del negocio, conservando los datos conforme a las obligaciones y criterios aplicables.",
                ],
            },
            {
                "heading": "Protección de datos y finalización",
                "paragraphs": [
                    "La relación respecto de datos de clientes se completa con el acuerdo de encargo de tratamiento.",
                    "Al finalizar el servicio, los datos se devolverán, portarán, bloquearán o suprimirán conforme a las instrucciones del responsable y a las obligaciones legales aplicables.",
                ],
            },
        ],
    },
    {
        "kind": "data_processing",
        "slug": "encargo-tratamiento",
        "version": "2026.07",
        "title": "Acuerdo de encargo de tratamiento",
        "lead": "Reglas para el tratamiento de datos de clientes por AgendaSalon siguiendo instrucciones de cada negocio.",
        "sections": [
            {
                "heading": "Objeto, duración y partes",
                "paragraphs": [
                    "El negocio actúa como responsable de los datos de sus clientes y citas. AgendaSalon actúa como encargado mientras presta el servicio y únicamente para esa finalidad.",
                    "El acuerdo permanece vigente durante la relación de servicio y durante las operaciones de devolución, bloqueo o supresión necesarias para cerrarla.",
                ],
            },
            {
                "heading": "Datos y personas interesadas",
                "paragraphs": [
                    "El tratamiento puede comprender datos identificativos y de contacto, servicios reservados, citas, autorizaciones familiares, estado operativo e historial del salón.",
                    "Las personas interesadas son clientes, contactos autorizados y profesionales. No se prevé tratar categorías especiales de datos.",
                ],
            },
            {
                "heading": "Instrucciones, confidencialidad y seguridad",
                "paragraphs": [
                    "AgendaSalon tratará los datos siguiendo instrucciones documentadas del negocio y garantizará compromisos de confidencialidad para las personas autorizadas.",
                    "Aplicará medidas proporcionadas al riesgo para autenticación, aislamiento por negocio, control de permisos, trazabilidad, copias, integridad, restauración y comunicaciones cifradas en producción.",
                ],
            },
            {
                "heading": "Subencargados y transferencias",
                "paragraphs": [
                    "Los proveedores necesarios para alojamiento, copias o comunicaciones se documentarán como subencargados y quedarán sujetos a obligaciones equivalentes.",
                    "El negocio será informado de cambios relevantes y podrá plantear una objeción fundada. No se realizarán transferencias internacionales sin un mecanismo válido.",
                ],
            },
            {
                "heading": "Asistencia, incidencias y auditoría",
                "paragraphs": [
                    "AgendaSalon colaborará razonablemente en el ejercicio de derechos, análisis de riesgos, consultas de la autoridad y gestión de brechas. Las incidencias relevantes se comunicarán sin dilación indebida con la información disponible.",
                    "Se facilitará la información necesaria para demostrar el cumplimiento y se permitirán comprobaciones proporcionadas que preserven la seguridad de otros negocios.",
                ],
            },
            {
                "heading": "Fin del servicio",
                "paragraphs": [
                    "Al finalizar, los datos se devolverán o suprimirán según las instrucciones del negocio, salvo que una obligación exija conservarlos bloqueados. Las copias seguirán su ciclo seguro de retención hasta su eliminación.",
                ],
            },
        ],
    },
    {
        "kind": "customer_privacy",
        "slug": "privacidad-clientes",
        "version": "2026.07",
        "title": "Privacidad de clientes y reservas",
        "lead": "Información sobre el uso de tus datos cuando creas una cuenta o reservas en un negocio de AgendaSalon.",
        "sections": [
            {
                "heading": "Quién es responsable",
                "paragraphs": [
                    "El negocio que aparece en esta página decide para qué utiliza los datos de sus clientes y actúa como responsable. AgendaSalon presta la infraestructura y actúa como encargado del tratamiento.",
                ],
            },
            {
                "heading": "Datos, finalidad y legitimación",
                "paragraphs": [
                    "Se utilizan los datos identificativos y de contacto necesarios, las citas, los servicios solicitados, el estado de asistencia y las autorizaciones para reservar por otra persona.",
                    "La finalidad es crear y gestionar la cuenta del salón, preparar y confirmar citas, evitar solapes, mantener el historial operativo y atender solicitudes. La gestión de la reserva se basa en medidas precontractuales y en la relación de servicio solicitada; no depende de un consentimiento publicitario.",
                ],
            },
            {
                "heading": "Destinatarios y seguridad",
                "paragraphs": [
                    "Acceden únicamente el negocio, sus profesionales autorizados y los proveedores técnicos necesarios bajo contrato. Los datos no se venden ni se utilizan para publicidad de terceros.",
                    "AgendaSalon aplica aislamiento entre negocios, contraseñas protegidas, control de permisos, revalidación de citas, trazabilidad y copias de seguridad.",
                ],
            },
            {
                "heading": "Conservación y derechos",
                "paragraphs": [
                    "El criterio concreto de conservación se muestra junto a la identidad del negocio. La supresión puede implicar bloqueo cuando sea necesario atender responsabilidades.",
                    "Puedes solicitar acceso, rectificación, supresión, oposición, limitación o portabilidad mediante el correo indicado o, si has iniciado sesión, desde el formulario de esta página. También puedes reclamar ante la Agencia Española de Protección de Datos.",
                ],
            },
            {
                "heading": "Datos que no deben incluirse",
                "paragraphs": [
                    "AgendaSalon está diseñado para información operativa de citas. No deben incorporarse diagnósticos, alergias, tratamientos médicos u otros datos especialmente protegidos en notas o formularios.",
                ],
            },
        ],
    },
    {
        "kind": "cookies",
        "slug": "cookies",
        "version": "2026.07",
        "title": "Política de cookies",
        "lead": "Cookies utilizadas para mantener el acceso, proteger formularios y conservar temporalmente una selección de reserva.",
        "sections": [
            {
                "heading": "Cookies técnicas",
                "paragraphs": [
                    "AgendaSalon utiliza cookies necesarias para mantener la sesión, proteger formularios frente a solicitudes no autorizadas y conservar durante un tiempo limitado la selección de una reserva.",
                    "Estas cookies no crean perfiles publicitarios ni siguen la navegación por otros sitios.",
                ],
            },
            {
                "heading": "Duración y control",
                "paragraphs": [
                    "La sesión profesional expira y se cierra conforme a la configuración de seguridad. La selección de reserva se conserva solo durante el proceso de confirmación.",
                    "El navegador permite eliminar o bloquear cookies, aunque hacerlo puede impedir el acceso o la confirmación de formularios.",
                ],
            },
            {
                "heading": "Servicios futuros",
                "paragraphs": [
                    "Si se incorporan herramientas analíticas, publicitarias o cookies no necesarias, esta política y el mecanismo de elección se actualizarán antes de activarlas.",
                ],
            },
        ],
    },
)


def _content_hash(document):
    payload = {
        key: document[key]
        for key in ("kind", "slug", "version", "title", "lead", "sections")
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def seed_legal_documents(apps, schema_editor):
    LegalDocument = apps.get_model("legal", "LegalDocument")
    Business = apps.get_model("businesses", "Business")

    Business.objects.update(legal_compliance_enabled=True)
    published_at = timezone.now()
    for document in DOCUMENTS:
        LegalDocument.objects.filter(kind=document["kind"], is_active=True).update(
            is_active=False
        )
        LegalDocument.objects.update_or_create(
            kind=document["kind"],
            version=document["version"],
            defaults={
                "slug": document["slug"],
                "title": document["title"],
                "lead": document["lead"],
                "sections": document["sections"],
                "content_hash": _content_hash(document),
                "published_at": published_at,
                "is_active": True,
            },
        )


def unpublish_documents(apps, schema_editor):
    LegalDocument = apps.get_model("legal", "LegalDocument")
    LegalDocument.objects.filter(version="2026.07").update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("legal", "0001_initial"),
    ]
    operations = [
        migrations.RunPython(seed_legal_documents, unpublish_documents),
    ]
