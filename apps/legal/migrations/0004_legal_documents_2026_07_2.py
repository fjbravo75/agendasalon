import hashlib
import json

from django.db import migrations
from django.utils import timezone


DOCUMENTS = (
    {
        "kind": "platform_privacy",
        "slug": "privacidad-plataforma",
        "version": "2026.07.2",
        "title": "Privacidad de AgendaSalon",
        "lead": "Información clara sobre los datos que utiliza AgendaSalon para prestar el servicio a negocios y profesionales.",
        "sections": [
            {
                "heading": "Quién trata los datos y a quién afecta",
                "paragraphs": [
                    "El titular identificado al comienzo de esta página es responsable de los datos necesarios para crear y administrar cuentas profesionales, prestar soporte, proteger la plataforma y mantener la relación con cada negocio.",
                    "Cuando AgendaSalon aloja datos de clientes, citas o personas autorizadas, el responsable es el negocio que decide cómo gestiona su agenda. En ese ámbito AgendaSalon actúa como encargado del tratamiento y sigue sus instrucciones documentadas.",
                ],
            },
            {
                "heading": "Qué datos utilizamos",
                "paragraphs": [
                    "Podemos tratar nombre, teléfono y correo profesional; credenciales protegidas; negocio y permisos asociados; preferencias de configuración; comunicaciones de soporte; y registros técnicos necesarios para seguridad, trazabilidad y diagnóstico de incidencias.",
                    "No pedimos categorías especiales de datos para administrar una cuenta profesional. Los campos de la aplicación no deben utilizarse para incorporar información médica o especialmente protegida que no sea necesaria para gestionar una cita.",
                ],
            },
            {
                "heading": "Para qué y con qué base jurídica",
                "paragraphs": [
                    "Usamos los datos para tramitar el alta, autenticar a los usuarios, prestar las funciones contratadas, atender consultas, prevenir usos indebidos, conservar evidencias de aceptación y cumplir obligaciones legales. Estas operaciones se apoyan en medidas precontractuales, ejecución del contrato, cumplimiento de obligaciones e interés legítimo en mantener un servicio seguro.",
                    "Si en el futuro se ofrecieran comunicaciones comerciales opcionales, se solicitaría una elección separada. La aceptación de esta política no autoriza publicidad.",
                ],
            },
            {
                "heading": "Proveedores, acceso y transferencias",
                "paragraphs": [
                    "Los datos no se venden. Solo pueden acceder el personal autorizado y los proveedores imprescindibles para alojamiento, copias de seguridad, comunicaciones o mantenimiento, sujetos a deber de confidencialidad y a contratos adecuados.",
                    "Antes de realizar una transferencia internacional se comprobará que exista una decisión de adecuación, cláusulas contractuales tipo u otra garantía válida, y se documentarán las medidas complementarias que resulten necesarias.",
                ],
            },
            {
                "heading": "Conservación y seguridad",
                "paragraphs": [
                    "Los datos de cuenta se conservan durante la relación con el negocio. Después pueden mantenerse bloqueados durante los plazos necesarios para atender obligaciones o responsabilidades. Los registros técnicos tienen plazos más breves y proporcionados a su finalidad.",
                    "AgendaSalon aplica control de accesos, separación por negocio, protección de credenciales, trazabilidad, copias de seguridad y procedimientos de restauración. Ninguna medida elimina por completo el riesgo, por lo que las incidencias se investigan y documentan.",
                ],
            },
            {
                "heading": "Derechos y reclamaciones",
                "paragraphs": [
                    "Puedes solicitar acceso, rectificación, supresión, oposición, limitación o portabilidad escribiendo al correo de privacidad indicado. La solicitud debe permitir verificar tu identidad sin aportar más datos de los necesarios.",
                    "Si consideras que el tratamiento no se ha atendido correctamente, puedes reclamar ante la Agencia Española de Protección de Datos en www.aepd.es.",
                ],
            },
        ],
    },
    {
        "kind": "terms",
        "slug": "condiciones-servicio",
        "version": "2026.07.2",
        "title": "Condiciones del servicio",
        "lead": "Reglas de uso de AgendaSalon y responsabilidades básicas de la plataforma y de cada negocio.",
        "sections": [
            {
                "heading": "Objeto y alcance",
                "paragraphs": [
                    "AgendaSalon facilita herramientas para organizar servicios, horarios, profesionales, clientes y citas. El negocio conserva la dirección de su actividad, sus precios, la calidad del servicio que presta y la información que publica.",
                    "La disponibilidad de una función no sustituye las obligaciones legales, fiscales, laborales o profesionales que correspondan al negocio.",
                ],
            },
            {
                "heading": "Cuenta, permisos y seguridad",
                "paragraphs": [
                    "Las credenciales son personales. El negocio debe mantener actualizada la relación de profesionales autorizados, utilizar contraseñas seguras y avisar sin demora cuando sospeche de un acceso indebido.",
                    "Cada usuario debe actuar dentro de sus permisos. No está permitido acceder a datos de otro negocio, eludir controles, probar vulnerabilidades sin autorización o interferir en el funcionamiento del servicio.",
                ],
            },
            {
                "heading": "Uso adecuado de la información",
                "paragraphs": [
                    "Solo deben incorporarse los datos razonablemente necesarios para gestionar la relación con el cliente y sus citas. Las notas internas deben ser respetuosas, objetivas y operativas.",
                    "No deben registrarse diagnósticos, alergias, tratamientos médicos, opiniones discriminatorias ni otros datos especialmente protegidos. El negocio es responsable de formar a su equipo y corregir usos inadecuados.",
                ],
            },
            {
                "heading": "Continuidad, mantenimiento e incidencias",
                "paragraphs": [
                    "Pueden realizarse tareas de mantenimiento o cambios necesarios para seguridad y evolución del producto. Cuando sea razonable, se comunicarán las interrupciones relevantes con antelación.",
                    "AgendaSalon aplica medidas proporcionadas de disponibilidad, integridad y recuperación, pero no puede garantizar un servicio absolutamente ininterrumpido frente a fallos externos o situaciones de fuerza mayor.",
                ],
            },
            {
                "heading": "Suspensión y finalización",
                "paragraphs": [
                    "El acceso puede pausarse ante una petición del negocio, un incumplimiento grave, un riesgo de seguridad o una obligación legal. Siempre que sea posible se informará del motivo y de las medidas necesarias para restablecerlo.",
                    "Al terminar la relación se acordará la devolución, exportación, bloqueo o supresión de los datos conforme a las instrucciones del responsable, al acuerdo de encargo y a las obligaciones aplicables.",
                ],
            },
            {
                "heading": "Cambios y contacto",
                "paragraphs": [
                    "Una modificación material genera una versión nueva y puede requerir una nueva aceptación. Las versiones anteriores se conservan para acreditar qué texto estuvo vigente en cada momento.",
                    "Las dudas sobre estas condiciones pueden enviarse a los datos de contacto que figuran en la documentación legal de la plataforma.",
                ],
            },
        ],
    },
    {
        "kind": "data_processing",
        "slug": "encargo-tratamiento",
        "version": "2026.07.2",
        "title": "Acuerdo de encargo de tratamiento",
        "lead": "Condiciones aplicables cuando AgendaSalon trata datos de clientes y citas siguiendo las instrucciones de un negocio.",
        "sections": [
            {
                "heading": "Partes, objeto y duración",
                "paragraphs": [
                    "El negocio identificado en su perfil legal actúa como responsable del tratamiento. AgendaSalon actúa como encargado para alojar, organizar y poner a disposición los datos necesarios para la gestión de clientes y citas.",
                    "El encargo permanece vigente mientras se presta el servicio y durante las operaciones necesarias para devolver, bloquear o suprimir los datos al finalizar.",
                ],
            },
            {
                "heading": "Personas, datos y operaciones",
                "paragraphs": [
                    "El tratamiento puede afectar a clientes, contactos autorizados y profesionales, e incluir datos identificativos y de contacto, servicios reservados, citas, asistencia, autorizaciones y trazabilidad operativa.",
                    "Las operaciones comprenden recogida, registro, consulta, organización, conservación, comunicación al propio negocio, copia de seguridad, recuperación y supresión. No se prevé tratar categorías especiales de datos.",
                ],
            },
            {
                "heading": "Instrucciones y deber de confidencialidad",
                "paragraphs": [
                    "AgendaSalon tratará los datos únicamente para prestar el servicio y conforme a instrucciones documentadas. Si una instrucción pudiera infringir la normativa, lo comunicará al negocio antes de ejecutarla, salvo prohibición legal.",
                    "Las personas con acceso autorizado estarán sujetas a un deber de confidencialidad y recibirán acceso limitado a las funciones necesarias para su trabajo.",
                ],
            },
            {
                "heading": "Medidas de seguridad y brechas",
                "paragraphs": [
                    "Se aplican medidas proporcionadas al riesgo, entre ellas autenticación, aislamiento por negocio, control de permisos, registro de acciones relevantes, protección de credenciales, copias de seguridad y capacidad de restauración.",
                    "Una brecha que afecte a datos del negocio se comunicará sin dilación indebida con la información disponible sobre naturaleza, alcance, consecuencias previsibles y medidas adoptadas, para que el responsable pueda cumplir sus obligaciones.",
                ],
            },
            {
                "heading": "Subencargados y transferencias",
                "paragraphs": [
                    "AgendaSalon podrá utilizar proveedores necesarios para infraestructura, copias o comunicaciones, imponiéndoles obligaciones equivalentes. Los cambios relevantes se documentarán y el negocio podrá formular una objeción fundada.",
                    "No se realizarán transferencias internacionales sin un mecanismo válido y, cuando proceda, una evaluación de las garantías del país y del proveedor.",
                ],
            },
            {
                "heading": "Asistencia al responsable",
                "paragraphs": [
                    "AgendaSalon colaborará razonablemente en solicitudes de derechos, evaluaciones de impacto, consultas previas, auditorías proporcionadas y demostración del cumplimiento, respetando la seguridad y confidencialidad de otros negocios.",
                    "El negocio debe trasladar instrucciones claras, mantener actualizados sus datos de contacto y no utilizar la plataforma para información que exceda la finalidad ordinaria de gestión de citas.",
                ],
            },
            {
                "heading": "Destino de los datos",
                "paragraphs": [
                    "Finalizado el servicio, los datos se devolverán o suprimirán según las instrucciones del negocio, salvo que una obligación legal exija conservarlos bloqueados. Las copias de seguridad seguirán su ciclo seguro de retención hasta su eliminación.",
                ],
            },
        ],
    },
    {
        "kind": "customer_privacy",
        "slug": "privacidad-clientes",
        "version": "2026.07.2",
        "title": "Privacidad de clientes y reservas",
        "lead": "Cómo utiliza el negocio tus datos para crear una ficha, gestionar una cuenta y atender tus citas.",
        "sections": [
            {
                "heading": "Quién es responsable",
                "paragraphs": [
                    "El negocio identificado en esta página decide para qué y cómo utiliza los datos de sus clientes y actúa como responsable. AgendaSalon proporciona la aplicación y trata esos datos por cuenta del negocio.",
                    "Para cuestiones sobre una cita, una ficha o el ejercicio de derechos debes dirigirte al correo de privacidad del negocio. AgendaSalon atenderá las instrucciones que reciba del responsable.",
                ],
            },
            {
                "heading": "Qué datos se utilizan",
                "paragraphs": [
                    "Se utilizan nombre, teléfono y, cuando se facilita, correo electrónico; credenciales protegidas de la cuenta; citas y servicios solicitados; asistencia o cancelación; y autorizaciones para reservar por otra persona.",
                    "Las notas deben limitarse a información práctica para atender la cita. No deben incluir diagnósticos, alergias, tratamientos médicos ni otros datos especialmente protegidos.",
                ],
            },
            {
                "heading": "Para qué y por qué podemos hacerlo",
                "paragraphs": [
                    "Los datos permiten crear y localizar tu ficha, preparar y confirmar citas, evitar solapes, avisar de cambios, gestionar la cuenta online, mantener un historial operativo y atender tus solicitudes.",
                    "El tratamiento se basa en las medidas precontractuales y en la relación de servicio que solicitas. La casilla del formulario acredita que recibiste esta información; no convierte la gestión de la cita en publicidad ni autoriza comunicaciones comerciales.",
                ],
            },
            {
                "heading": "Quién puede acceder",
                "paragraphs": [
                    "Pueden acceder el negocio y sus profesionales autorizados, dentro de las funciones que necesiten para gestionar la agenda. AgendaSalon y sus proveedores técnicos acceden únicamente cuando resulta necesario para prestar, proteger o mantener el servicio.",
                    "Los datos no se venden ni se ceden para publicidad de terceros. Una comunicación distinta de las necesarias para la cita requeriría una base jurídica propia y una información separada.",
                ],
            },
            {
                "heading": "Cuánto tiempo se conservan",
                "paragraphs": [
                    "El criterio concreto del negocio aparece junto a sus datos de identidad. De forma general, la información se conserva mientras exista relación con el salón y después durante los plazos necesarios para atender obligaciones o responsabilidades.",
                    "Cuando procede la supresión, algunos datos pueden quedar bloqueados y fuera del uso ordinario hasta que finalicen esos plazos.",
                ],
            },
            {
                "heading": "Tus derechos",
                "paragraphs": [
                    "Puedes solicitar acceso, rectificación, supresión, oposición, limitación o portabilidad mediante el correo indicado. Si tienes cuenta, también puedes registrar una solicitud desde esta página.",
                    "El negocio puede pedir información proporcionada para verificar tu identidad. Si no recibes una respuesta adecuada, puedes reclamar ante la Agencia Española de Protección de Datos en www.aepd.es.",
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


def migrate_evidence_and_publish_documents(apps, schema_editor):
    LegalAcceptance = apps.get_model("legal", "LegalAcceptance")
    CustomerPrivacyEvidence = apps.get_model("legal", "CustomerPrivacyEvidence")
    LegalDocument = apps.get_model("legal", "LegalDocument")

    channel_by_context = {
        "client_registration": "online_registration",
        "client_invitation": "client_invitation",
        "booking_confirmation": "booking",
    }
    for acceptance in LegalAcceptance.objects.filter(client_access__isnull=False).select_related(
        "client_access"
    ):
        CustomerPrivacyEvidence.objects.get_or_create(
            document_id=acceptance.document_id,
            business_id=acceptance.business_id,
            business_client_id=acceptance.client_access.business_client_id,
            client_access_id=acceptance.client_access_id,
            event_type="acknowledged",
            channel=channel_by_context.get(acceptance.context, "other"),
            defaults={
                "recorded_by_id": None,
                "document_hash_snapshot": acceptance.document_hash_snapshot,
                "legal_context_snapshot": acceptance.legal_context_snapshot,
                "occurred_at": acceptance.accepted_at,
            },
        )

    published_at = timezone.now()
    for document in DOCUMENTS:
        LegalDocument.objects.filter(kind=document["kind"], is_active=True).update(is_active=False)
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
    for document in DOCUMENTS:
        LegalDocument.objects.filter(kind=document["kind"], version="2026.07.2").update(
            is_active=False
        )
        LegalDocument.objects.filter(kind=document["kind"], version="2026.07").update(
            is_active=True
        )


class Migration(migrations.Migration):
    dependencies = [
        ("legal", "0003_customerprivacyevidence"),
    ]

    operations = [
        migrations.RunPython(migrate_evidence_and_publish_documents, unpublish_documents),
    ]
