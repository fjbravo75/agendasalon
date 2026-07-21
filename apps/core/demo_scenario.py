"""Escenario canónico y determinista de la demostración académica.

Este módulo no conoce Django ni realiza escrituras. Expone únicamente datos y
funciones puras para que ``seed_demo`` y el futuro refresco nocturno compartan
una sola fuente de verdad comprobable.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Iterable


BUSINESS_MARI = "mari"
BUSINESS_NORTE = "norte"
DEMO_PASSWORDS = {
    BUSINESS_MARI: "AgendaSalonDemo2",
    BUSINESS_NORTE: "AgendaSalonDemo3",
}
DEMO_ADVISORY_LOCK_ID = 4_147_326_341_001

# Valores persistidos por los ``TextChoices`` de Django. Se mantienen en este
# módulo puro para que tanto el seed manual como el refresco automático partan
# de la misma apariencia canónica sin importar el estado previo de la demo.
CANONICAL_PROFESSIONAL_THEMES = {
    BUSINESS_MARI: "dark",
    BUSINESS_NORTE: "light",
}
CANONICAL_PLATFORM_SETTINGS = {
    "admin_theme": "light",
    "login_image_preset": "agendasalon",
    "notification_email": "",
    "notification_email_normalized": "",
    "notification_email_verified_at": None,
    "notifications_enabled": True,
    "notify_continuity": True,
    "notify_demo_refresh": True,
    "notify_signup_requests": True,
    "notify_email_failures": True,
}

STATUS_CONFIRMED = "confirmada"
STATUS_CANCELLED = "cancelada"
STATUS_COMPLETED = "completada"
STATUS_NO_SHOW = "no_presentada"

CHANNEL_PHONE = "telefono"
CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_EMAIL = "email"
CHANNEL_FRONT_DESK = "mostrador"
CHANNEL_PUBLIC_WEB = "web_publica"

RELATIONSHIP_SELF = "titular"
RELATIONSHIP_MOTHER = "madre"
RELATIONSHIP_DAUGHTER = "hija"
RELATIONSHIP_CAREGIVER = "cuidador"
RELATIONSHIP_FATHER = "padre"


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    key: str
    business: str
    name: str
    description: str
    duration_minutes: int
    price_amount: Decimal
    color_hex: str
    is_active: bool
    display_order: int


@dataclass(frozen=True, slots=True)
class ClientSpec:
    key: str
    business: str
    full_name: str
    phone: str
    internal_notes: str
    is_active: bool = True
    source: str = "imported_demo"


@dataclass(frozen=True, slots=True)
class AccessSpec:
    business: str
    client_key: str
    email: str
    password: str
    is_active: bool = True
    email_verified: bool = True


@dataclass(frozen=True, slots=True)
class RelationshipSpec:
    business: str
    representative_key: str
    beneficiary_key: str
    relationship: str
    notes: str


@dataclass(frozen=True, slots=True)
class AppointmentSpec:
    key: str
    business: str
    day_token: str
    line_number: int
    start_time: str
    client_key: str
    service_keys: tuple[str, ...]
    status: str
    channel: str
    requester_key: str = ""
    requester_relationship: str = ""
    duration_minutes: int | None = None
    duration_adjustment_reason: str = ""
    cancellation_reason: str = ""


def _service(
    key: str,
    business: str,
    name: str,
    description: str,
    duration: int,
    price: str,
    color: str,
    active: bool,
    order: int,
) -> ServiceSpec:
    return ServiceSpec(
        key=key,
        business=business,
        name=name,
        description=description,
        duration_minutes=duration,
        price_amount=Decimal(price),
        color_hex=color,
        is_active=active,
        display_order=order,
    )


MARI_SERVICES = (
    _service(
        "wash",
        BUSINESS_MARI,
        "Lavado y preparación",
        "Lavado adaptado al cabello y preparación para el servicio posterior.",
        15,
        "8.00",
        "#0F8F82",
        True,
        1,
    ),
    _service(
        "cut",
        BUSINESS_MARI,
        "Corte mujer",
        "Corte personalizado según el estilo, el largo y la forma del cabello.",
        30,
        "22.00",
        "#4F6FB5",
        True,
        2,
    ),
    _service(
        "dry",
        BUSINESS_MARI,
        "Secado y peinado",
        "Secado con acabado y peinado para el día a día.",
        30,
        "20.00",
        "#D96F5D",
        True,
        3,
    ),
    _service(
        "long_style",
        BUSINESS_MARI,
        "Peinado cabello largo",
        "Secado y peinado trabajado para cabello largo o abundante.",
        45,
        "28.00",
        "#C66A88",
        True,
        4,
    ),
    _service(
        "fringe",
        BUSINESS_MARI,
        "Corte flequillo",
        "Repaso y ajuste del flequillo sin corte completo.",
        15,
        "8.00",
        "#7A8DBD",
        True,
        5,
    ),
    _service(
        "roots",
        BUSINESS_MARI,
        "Color raíces",
        "Aplicación de color en raíces con lavado y control del tiempo de exposición.",
        75,
        "38.00",
        "#B85F7B",
        True,
        6,
    ),
    _service(
        "full_color",
        BUSINESS_MARI,
        "Color completo",
        "Coloración completa del cabello con lavado y acabado básico.",
        90,
        "50.00",
        "#A94F6C",
        True,
        7,
    ),
    _service(
        "toner",
        BUSINESS_MARI,
        "Baño de color/matiz",
        "Matización o baño de color para refrescar el tono y el brillo.",
        45,
        "30.00",
        "#D1849A",
        True,
        8,
    ),
    _service(
        "highlights",
        BUSINESS_MARI,
        "Mechas clásicas",
        "Trabajo de mechas tradicionales con matización y lavado.",
        120,
        "70.00",
        "#D9A441",
        True,
        9,
    ),
    _service(
        "balayage",
        BUSINESS_MARI,
        "Balayage/babylights",
        "Técnica de iluminación personalizada con matización y acabado.",
        150,
        "95.00",
        "#B9855A",
        True,
        10,
    ),
    _service(
        "hydration",
        BUSINESS_MARI,
        "Tratamiento hidratante intensivo",
        "Tratamiento reparador para aportar hidratación, suavidad y brillo.",
        30,
        "25.00",
        "#4D9A88",
        True,
        11,
    ),
    _service(
        "event_style",
        BUSINESS_MARI,
        "Recogido/peinado evento",
        "Peinado o recogido trabajado para celebraciones y ocasiones especiales.",
        60,
        "45.00",
        "#8A63A8",
        True,
        12,
    ),
    _service(
        "straightening",
        BUSINESS_MARI,
        "Alisado orgánico",
        "Servicio de alisado de larga duración con diagnóstico previo.",
        180,
        "160.00",
        "#6F7A87",
        False,
        13,
    ),
    _service(
        "perm",
        BUSINESS_MARI,
        "Moldeado permanente",
        "Moldeado duradero para crear ondas o volumen con acabado personalizado.",
        120,
        "65.00",
        "#947B6B",
        False,
        14,
    ),
)

NORTE_SERVICES = (
    _service(
        "classic",
        BUSINESS_NORTE,
        "Corte clásico",
        "Corte masculino clásico con acabado y peinado.",
        30,
        "18.00",
        "#2F6F73",
        True,
        1,
    ),
    _service(
        "fade",
        BUSINESS_NORTE,
        "Degradado/fade",
        "Degradado trabajado con ajuste de contornos y acabado.",
        45,
        "20.00",
        "#5079BD",
        True,
        2,
    ),
    _service(
        "scissor",
        BUSINESS_NORTE,
        "Corte tijera/cabello largo",
        "Corte principalmente a tijera para cabello medio o largo.",
        45,
        "22.00",
        "#6F7A87",
        True,
        3,
    ),
    _service(
        "clipper",
        BUSINESS_NORTE,
        "Rapado máquina",
        "Rapado uniforme a máquina con repaso de contornos.",
        15,
        "12.00",
        "#64748B",
        True,
        4,
    ),
    _service(
        "child",
        BUSINESS_NORTE,
        "Corte infantil",
        "Corte para menores con atención adaptada y acabado sencillo.",
        30,
        "15.00",
        "#4E83A8",
        True,
        5,
    ),
    _service(
        "senior",
        BUSINESS_NORTE,
        "Corte mayores 65",
        "Corte clásico para clientes mayores de 65 años.",
        30,
        "14.00",
        "#7B8B7A",
        True,
        6,
    ),
    _service(
        "beard",
        BUSINESS_NORTE,
        "Arreglo/perfilado barba",
        "Recorte, definición de contornos y acabado de barba.",
        30,
        "12.00",
        "#8F6B4A",
        True,
        7,
    ),
    _service(
        "beard_ritual",
        BUSINESS_NORTE,
        "Ritual barba toalla caliente",
        "Preparación con toalla caliente, arreglo y cuidado final de la barba.",
        45,
        "18.00",
        "#A06F3C",
        True,
        8,
    ),
    _service(
        "classic_shave",
        BUSINESS_NORTE,
        "Afeitado clásico",
        "Afeitado tradicional con preparación de la piel y acabado calmante.",
        30,
        "18.00",
        "#C08A4D",
        True,
        9,
    ),
    _service(
        "head_shave",
        BUSINESS_NORTE,
        "Afeitado cabeza",
        "Afeitado completo de cabeza con preparación y cuidado final.",
        30,
        "16.00",
        "#8B7355",
        True,
        10,
    ),
    _service(
        "maintenance",
        BUSINESS_NORTE,
        "Contornos/mantenimiento",
        "Repaso rápido de patillas, nuca y contornos entre cortes.",
        15,
        "8.00",
        "#5C7A72",
        True,
        11,
    ),
    _service(
        "brows",
        BUSINESS_NORTE,
        "Diseño/perfilado cejas",
        "Repaso y perfilado natural de cejas.",
        15,
        "6.00",
        "#8274C9",
        True,
        12,
    ),
    _service(
        "grey",
        BUSINESS_NORTE,
        "Camuflaje canas",
        "Matización discreta de canas para un acabado natural.",
        45,
        "25.00",
        "#596579",
        True,
        13,
    ),
    _service(
        "color",
        BUSINESS_NORTE,
        "Color/mechas",
        "Coloración o mechas masculinas con diagnóstico previo.",
        120,
        "40.00",
        "#9A6B72",
        False,
        14,
    ),
)

SERVICES = MARI_SERVICES + NORTE_SERVICES


def _client(
    key: str, business: str, name: str, phone: str, notes: str, active: bool = True
) -> ClientSpec:
    return ClientSpec(key, business, name, phone, notes, active)


MARI_CLIENTS = (
    _client(
        "maria",
        BUSINESS_MARI,
        "María López",
        "600111201",
        "Prefiere citas por la mañana. Gestiona también las citas de su hijo Lucas.",
    ),
    _client(
        "lucia",
        BUSINESS_MARI,
        "Lucía Gómez",
        "600111202",
        "Suele combinar varios servicios en una misma visita.",
    ),
    _client(
        "carmen",
        BUSINESS_MARI,
        "Carmen Ruiz",
        "600111203",
        "Agradece confirmar la duración antes de cerrar la cita.",
    ),
    _client(
        "daniel",
        BUSINESS_MARI,
        "Daniel Vega",
        "600111204",
        "Cuidador habitual de Rosa Martín; también acude como cliente.",
    ),
    _client(
        "rosa",
        BUSINESS_MARI,
        "Rosa Martín",
        "600111205",
        "Daniel gestiona habitualmente sus reservas.",
    ),
    _client(
        "lucas",
        BUSINESS_MARI,
        "Lucas López",
        "",
        "Menor. Su madre, María López, gestiona sus citas.",
    ),
    _client(
        "elena",
        BUSINESS_MARI,
        "Elena Sánchez",
        "600111206",
        "Utiliza habitualmente la reserva online.",
    ),
    _client(
        "patricia",
        BUSINESS_MARI,
        "Patricia Moreno",
        "600111207",
        "Prefiere las citas a última hora de la tarde.",
    ),
    _client(
        "sofia",
        BUSINESS_MARI,
        "Sofía Hernández",
        "600111208",
        "Suele reservar servicios de color y tratamiento.",
    ),
    _client("beatriz", BUSINESS_MARI, "Beatriz Navarro", "600111209", "Prefiere huecos de mañana."),
    _client(
        "natalia",
        BUSINESS_MARI,
        "Natalia Romero",
        "600111210",
        "Consulta con antelación los peinados para eventos.",
    ),
    _client(
        "isabel",
        BUSINESS_MARI,
        "Isabel Torres",
        "600111211",
        "Gestiona también las citas de su madre Teresa.",
    ),
    _client(
        "teresa",
        BUSINESS_MARI,
        "Teresa García",
        "600111212",
        "Su hija Isabel puede reservar en su nombre.",
    ),
    _client(
        "noelia",
        BUSINESS_MARI,
        "Noelia Castro",
        "600111213",
        "Suele pedir mechas y mantenimiento de color.",
    ),
    _client(
        "raquel",
        BUSINESS_MARI,
        "Raquel Jiménez",
        "600111214",
        "Prefiere coordinar las citas por WhatsApp.",
    ),
    _client(
        "marta",
        BUSINESS_MARI,
        "Marta Alonso",
        "600111215",
        "Utiliza la reserva online y suele elegir tratamiento.",
    ),
    _client(
        "irene",
        BUSINESS_MARI,
        "Irene Vidal",
        "600111216",
        "Alterna corte y mantenimiento de color.",
    ),
    _client(
        "claudia",
        BUSINESS_MARI,
        "Claudia Ferrer",
        "600111217",
        "Cabello largo; agradece confirmar el tiempo previsto.",
    ),
    _client(
        "alicia", BUSINESS_MARI, "Alicia Molina", "600111218", "Suele reservar color completo."
    ),
    _client("paula", BUSINESS_MARI, "Paula Reyes", "600111219", "Prefiere las citas de tarde."),
    _client(
        "adriana",
        BUSINESS_MARI,
        "Adriana Ortega",
        "600111220",
        "Consulta peinados y recogidos para celebraciones.",
    ),
    _client(
        "nuria",
        BUSINESS_MARI,
        "Nuria Blanco",
        "600111221",
        "Ficha pausada; se conserva su historial anterior.",
        False,
    ),
)

NORTE_CLIENTS = (
    _client(
        "javier",
        BUSINESS_NORTE,
        "Javier Martín",
        "600222201",
        "Suele combinar corte y arreglo de barba.",
    ),
    _client(
        "marcos", BUSINESS_NORTE, "Marcos Ruiz", "600222202", "Prefiere última hora de la tarde."
    ),
    _client(
        "alvaro",
        BUSINESS_NORTE,
        "Álvaro Santos",
        "600222203",
        "Utiliza habitualmente la reserva online.",
    ),
    _client(
        "sergio", BUSINESS_NORTE, "Sergio Prieto", "600222204", "Alterna rapado y corte clásico."
    ),
    _client(
        "miguel",
        BUSINESS_NORTE,
        "Miguel Campos",
        "600222205",
        "Cliente habitual del corte para mayores de 65.",
    ),
    _client(
        "carlos",
        BUSINESS_NORTE,
        "Carlos Domínguez",
        "600222206",
        "Suele reservar el ritual de barba.",
    ),
    _client(
        "diego",
        BUSINESS_NORTE,
        "Diego Suárez",
        "600222207",
        "Prefiere degradado y contacto por WhatsApp.",
    ),
    _client(
        "hugo", BUSINESS_NORTE, "Hugo Lozano", "600222208", "Alterna arreglo de barba y afeitado."
    ),
    _client(
        "andres",
        BUSINESS_NORTE,
        "Andrés Gil",
        "600222209",
        "Consulta servicios de camuflaje de canas.",
    ),
    _client(
        "ruben",
        BUSINESS_NORTE,
        "Rubén Peña",
        "600222210",
        "Suele pedir mantenimiento entre cortes.",
    ),
    _client(
        "oscar",
        BUSINESS_NORTE,
        "Óscar Cabrera",
        "600222211",
        "Gestiona también las reservas de su hijo Nico.",
    ),
    _client(
        "nico",
        BUSINESS_NORTE,
        "Nico Cabrera",
        "600222212",
        "Su padre Óscar puede reservar en su nombre.",
    ),
    _client("tomas", BUSINESS_NORTE, "Tomás León", "600222213", "Prefiere corte clásico."),
    _client(
        "ivan",
        BUSINESS_NORTE,
        "Iván Acosta",
        "600222214",
        "Conserva un servicio histórico de color actualmente pausado.",
    ),
)

CLIENTS = MARI_CLIENTS + NORTE_CLIENTS

ACCESSES = (
    AccessSpec(BUSINESS_MARI, "maria", "maria.lopez@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_MARI]),
    AccessSpec(BUSINESS_MARI, "lucia", "lucia.gomez@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_MARI]),
    AccessSpec(BUSINESS_MARI, "daniel", "daniel.vega@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_MARI]),
    AccessSpec(BUSINESS_MARI, "elena", "elena.sanchez@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_MARI]),
    AccessSpec(BUSINESS_MARI, "sofia", "sofia.hernandez@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_MARI]),
    AccessSpec(BUSINESS_MARI, "isabel", "isabel.torres@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_MARI]),
    AccessSpec(BUSINESS_MARI, "marta", "marta.alonso@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_MARI]),
    AccessSpec(BUSINESS_NORTE, "javier", "javier.martin@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_NORTE]),
    AccessSpec(BUSINESS_NORTE, "alvaro", "alvaro.santos@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_NORTE]),
    AccessSpec(BUSINESS_NORTE, "carlos", "carlos.dominguez@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_NORTE]),
    AccessSpec(BUSINESS_NORTE, "oscar", "oscar.cabrera@demo.agendasalon.local", DEMO_PASSWORDS[BUSINESS_NORTE]),
)

RELATIONSHIPS = (
    RelationshipSpec(
        BUSINESS_MARI,
        "maria",
        "lucas",
        RELATIONSHIP_MOTHER,
        "Su madre gestiona las citas presenciales, telefónicas y online.",
    ),
    RelationshipSpec(
        BUSINESS_MARI,
        "daniel",
        "rosa",
        RELATIONSHIP_CAREGIVER,
        "Su cuidador habitual puede reservar citas en su nombre.",
    ),
    RelationshipSpec(
        BUSINESS_MARI,
        "isabel",
        "teresa",
        RELATIONSHIP_DAUGHTER,
        "Su hija puede reservar citas en su nombre.",
    ),
    RelationshipSpec(
        BUSINESS_NORTE,
        "oscar",
        "nico",
        RELATIONSHIP_FATHER,
        "Su padre puede reservar citas en su nombre.",
    ),
)


def _a(
    key: str,
    business: str,
    day: str,
    line: int,
    start: str,
    client: str,
    services: str | tuple[str, ...],
    status: str,
    channel: str,
    requester: str = "",
    relationship: str = "",
    *,
    duration: int | None = None,
    adjustment: str = "",
    cancellation: str = "",
) -> AppointmentSpec:
    service_keys = (services,) if isinstance(services, str) else services
    return AppointmentSpec(
        key=key,
        business=business,
        day_token=day,
        line_number=line,
        start_time=start,
        client_key=client,
        service_keys=service_keys,
        status=status,
        channel=channel,
        requester_key=requester,
        requester_relationship=relationship,
        duration_minutes=duration,
        duration_adjustment_reason=adjustment,
        cancellation_reason=cancellation,
    )


MARI_APPOINTMENTS = (
    # 22 atendidas.
    _a(
        "MC01",
        BUSINESS_MARI,
        "P20",
        1,
        "09:00",
        "elena",
        "straightening",
        STATUS_COMPLETED,
        CHANNEL_PHONE,
    ),
    _a(
        "MC02",
        BUSINESS_MARI,
        "P20",
        2,
        "09:00",
        "maria",
        "cut",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "maria",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MC03", BUSINESS_MARI, "P20", 3, "09:00", "carmen", "roots", STATUS_COMPLETED, CHANNEL_PHONE
    ),
    _a(
        "MC04",
        BUSINESS_MARI,
        "P20",
        2,
        "10:00",
        "patricia",
        "long_style",
        STATUS_COMPLETED,
        CHANNEL_WHATSAPP,
    ),
    _a(
        "MC05",
        BUSINESS_MARI,
        "P16",
        1,
        "09:00",
        "lucia",
        "balayage",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "lucia",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MC06",
        BUSINESS_MARI,
        "P16",
        2,
        "09:00",
        "sofia",
        "toner",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "sofia",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MC07",
        BUSINESS_MARI,
        "P16",
        3,
        "09:00",
        "raquel",
        "cut",
        STATUS_COMPLETED,
        CHANNEL_FRONT_DESK,
    ),
    _a(
        "MC08",
        BUSINESS_MARI,
        "P16",
        2,
        "10:00",
        "marta",
        "hydration",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "marta",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MC09",
        BUSINESS_MARI,
        "P12",
        1,
        "16:00",
        "beatriz",
        "full_color",
        STATUS_COMPLETED,
        CHANNEL_PHONE,
    ),
    _a(
        "MC10",
        BUSINESS_MARI,
        "P12",
        2,
        "16:00",
        "natalia",
        "event_style",
        STATUS_COMPLETED,
        CHANNEL_FRONT_DESK,
    ),
    _a(
        "MC11",
        BUSINESS_MARI,
        "P12",
        3,
        "16:00",
        "isabel",
        "dry",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "isabel",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MC12",
        BUSINESS_MARI,
        "P12",
        3,
        "17:00",
        "teresa",
        "cut",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "isabel",
        RELATIONSHIP_DAUGHTER,
    ),
    _a(
        "MC13",
        BUSINESS_MARI,
        "P8",
        1,
        "09:00",
        "noelia",
        "highlights",
        STATUS_COMPLETED,
        CHANNEL_WHATSAPP,
    ),
    _a(
        "MC14",
        BUSINESS_MARI,
        "P8",
        2,
        "09:00",
        "claudia",
        "long_style",
        STATUS_COMPLETED,
        CHANNEL_WHATSAPP,
    ),
    _a(
        "MC15",
        BUSINESS_MARI,
        "P8",
        3,
        "09:00",
        "alicia",
        "hydration",
        STATUS_COMPLETED,
        CHANNEL_FRONT_DESK,
    ),
    _a("MC16", BUSINESS_MARI, "P8", 2, "10:00", "paula", "toner", STATUS_COMPLETED, CHANNEL_EMAIL),
    _a("MC17", BUSINESS_MARI, "P5", 1, "16:00", "nuria", "perm", STATUS_COMPLETED, CHANNEL_PHONE),
    _a(
        "MC18",
        BUSINESS_MARI,
        "P5",
        2,
        "16:00",
        "irene",
        "cut",
        STATUS_COMPLETED,
        CHANNEL_FRONT_DESK,
    ),
    _a(
        "MC19",
        BUSINESS_MARI,
        "P5",
        3,
        "16:00",
        "rosa",
        "long_style",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "daniel",
        RELATIONSHIP_CAREGIVER,
    ),
    _a("MC20", BUSINESS_MARI, "P2", 1, "09:00", "carmen", "roots", STATUS_COMPLETED, CHANNEL_PHONE),
    _a(
        "MC21",
        BUSINESS_MARI,
        "P2",
        2,
        "09:00",
        "maria",
        ("wash", "dry"),
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "maria",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MC22",
        BUSINESS_MARI,
        "P2",
        3,
        "09:00",
        "lucas",
        "cut",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "maria",
        RELATIONSHIP_MOTHER,
    ),
    # 4 ausencias.
    _a(
        "MN01",
        BUSINESS_MARI,
        "P18",
        1,
        "18:00",
        "patricia",
        "long_style",
        STATUS_NO_SHOW,
        CHANNEL_WHATSAPP,
    ),
    _a("MN02", BUSINESS_MARI, "P14", 2, "17:00", "raquel", "cut", STATUS_NO_SHOW, CHANNEL_PHONE),
    _a(
        "MN03",
        BUSINESS_MARI,
        "P10",
        1,
        "10:00",
        "noelia",
        "hydration",
        STATUS_NO_SHOW,
        CHANNEL_EMAIL,
    ),
    _a(
        "MN04",
        BUSINESS_MARI,
        "P6",
        3,
        "16:00",
        "daniel",
        "hydration",
        STATUS_NO_SHOW,
        CHANNEL_PHONE,
    ),
    # 5 cancelaciones.
    _a(
        "MX01",
        BUSINESS_MARI,
        "P19",
        2,
        "16:00",
        "elena",
        "roots",
        STATUS_CANCELLED,
        CHANNEL_EMAIL,
        cancellation="Imprevisto personal; llamará para elegir otra fecha.",
    ),
    _a(
        "MX02",
        BUSINESS_MARI,
        "P15",
        1,
        "10:00",
        "sofia",
        "balayage",
        STATUS_CANCELLED,
        CHANNEL_WHATSAPP,
        cancellation="La clienta prefiere aplazar el cambio de color.",
    ),
    _a(
        "MX03",
        BUSINESS_MARI,
        "P11",
        3,
        "18:00",
        "teresa",
        "cut",
        STATUS_CANCELLED,
        CHANNEL_PHONE,
        cancellation="La familia reorganiza la cita.",
    ),
    _a(
        "MX04",
        BUSINESS_MARI,
        "P7",
        2,
        "17:00",
        "marta",
        "event_style",
        STATUS_CANCELLED,
        CHANNEL_PUBLIC_WEB,
        "marta",
        RELATIONSHIP_SELF,
        cancellation="La clienta solicitó online cambiar la fecha; el equipo canceló la cita para gestionar la reprogramación.",
    ),
    _a(
        "MX05",
        BUSINESS_MARI,
        "P3",
        1,
        "09:00",
        "claudia",
        "highlights",
        STATUS_CANCELLED,
        CHANNEL_PHONE,
        cancellation="Cambio de disponibilidad avisado por teléfono.",
    ),
    # 4 confirmadas ya transcurridas, pendientes de cierre profesional.
    _a(
        "MP01",
        BUSINESS_MARI,
        "P1",
        1,
        "09:00",
        "maria",
        ("wash", "cut", "dry"),
        STATUS_CONFIRMED,
        CHANNEL_PHONE,
    ),
    _a(
        "MP02",
        BUSINESS_MARI,
        "P1",
        2,
        "09:00",
        "lucia",
        "roots",
        STATUS_CONFIRMED,
        CHANNEL_WHATSAPP,
    ),
    _a(
        "MP03",
        BUSINESS_MARI,
        "P1",
        3,
        "09:00",
        "carmen",
        "hydration",
        STATUS_CONFIRMED,
        CHANNEL_EMAIL,
    ),
    _a(
        "MP04",
        BUSINESS_MARI,
        "P1",
        3,
        "10:00",
        "beatriz",
        "cut",
        STATUS_CONFIRMED,
        CHANNEL_FRONT_DESK,
    ),
    # 19 futuras; Carmen queda deliberadamente fuera de este bloque.
    _a(
        "MF01",
        BUSINESS_MARI,
        "F0",
        1,
        "09:00",
        "maria",
        "cut",
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "maria",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MF02",
        BUSINESS_MARI,
        "F0",
        2,
        "09:00",
        "lucas",
        "cut",
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "maria",
        RELATIONSHIP_MOTHER,
    ),
    _a(
        "MF03",
        BUSINESS_MARI,
        "F0",
        3,
        "09:00",
        "patricia",
        "roots",
        STATUS_CONFIRMED,
        CHANNEL_PHONE,
    ),
    _a(
        "MF04",
        BUSINESS_MARI,
        "F0",
        1,
        "10:00",
        "lucia",
        "toner",
        STATUS_CONFIRMED,
        CHANNEL_WHATSAPP,
    ),
    _a(
        "MF05",
        BUSINESS_MARI,
        "F0",
        2,
        "10:00",
        "rosa",
        "cut",
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "daniel",
        RELATIONSHIP_CAREGIVER,
        duration=45,
        adjustment="Margen adicional previsto para una atención pausada.",
    ),
    _a(
        "MF06",
        BUSINESS_MARI,
        "F1",
        2,
        "09:00",
        "sofia",
        "balayage",
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "sofia",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MF07",
        BUSINESS_MARI,
        "F1",
        3,
        "09:00",
        "marta",
        ("wash", "cut", "dry"),
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "marta",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MF08",
        BUSINESS_MARI,
        "F2",
        1,
        "09:00",
        "natalia",
        "balayage",
        STATUS_CONFIRMED,
        CHANNEL_PHONE,
    ),
    _a(
        "MF09",
        BUSINESS_MARI,
        "F2",
        1,
        "12:00",
        "noelia",
        "highlights",
        STATUS_CONFIRMED,
        CHANNEL_WHATSAPP,
    ),
    _a(
        "MF10",
        BUSINESS_MARI,
        "F2",
        1,
        "16:00",
        "raquel",
        "highlights",
        STATUS_CONFIRMED,
        CHANNEL_FRONT_DESK,
    ),
    _a(
        "MF11",
        BUSINESS_MARI,
        "F2",
        1,
        "18:30",
        "beatriz",
        "full_color",
        STATUS_CONFIRMED,
        CHANNEL_PHONE,
    ),
    _a(
        "MF12",
        BUSINESS_MARI,
        "F2",
        2,
        "09:00",
        "alicia",
        "full_color",
        STATUS_CONFIRMED,
        CHANNEL_EMAIL,
    ),
    _a(
        "MF13",
        BUSINESS_MARI,
        "F2",
        2,
        "11:00",
        "claudia",
        "highlights",
        STATUS_CONFIRMED,
        CHANNEL_EMAIL,
    ),
    _a(
        "MF14",
        BUSINESS_MARI,
        "F2",
        2,
        "13:30",
        "irene",
        "cut",
        STATUS_CONFIRMED,
        CHANNEL_FRONT_DESK,
    ),
    _a(
        "MF15",
        BUSINESS_MARI,
        "F2",
        2,
        "16:00",
        "paula",
        "roots",
        STATUS_CONFIRMED,
        CHANNEL_WHATSAPP,
    ),
    _a(
        "MF16",
        BUSINESS_MARI,
        "F2",
        2,
        "17:45",
        "elena",
        "highlights",
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "elena",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MF17",
        BUSINESS_MARI,
        "F2",
        3,
        "09:00",
        "daniel",
        ("full_color", "cut", "dry"),
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "daniel",
        RELATIONSHIP_SELF,
    ),
    _a(
        "MF18",
        BUSINESS_MARI,
        "F2",
        3,
        "16:00",
        "teresa",
        ("wash", "roots", "cut", "dry", "hydration"),
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "isabel",
        RELATIONSHIP_DAUGHTER,
    ),
    _a(
        "MF19",
        BUSINESS_MARI,
        "F12",
        3,
        "16:00",
        "adriana",
        "event_style",
        STATUS_CONFIRMED,
        CHANNEL_FRONT_DESK,
    ),
)

NORTE_APPOINTMENTS = (
    # 15 atendidas.
    _a(
        "NC01",
        BUSINESS_NORTE,
        "P20",
        1,
        "10:00",
        "javier",
        "classic",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "javier",
        RELATIONSHIP_SELF,
    ),
    _a(
        "NC02", BUSINESS_NORTE, "P20", 2, "10:00", "marcos", "fade", STATUS_COMPLETED, CHANNEL_PHONE
    ),
    _a(
        "NC03",
        BUSINESS_NORTE,
        "P16",
        1,
        "16:00",
        "alvaro",
        "scissor",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "alvaro",
        RELATIONSHIP_SELF,
    ),
    _a(
        "NC04",
        BUSINESS_NORTE,
        "P16",
        2,
        "16:00",
        "sergio",
        "clipper",
        STATUS_COMPLETED,
        CHANNEL_FRONT_DESK,
    ),
    _a(
        "NC05",
        BUSINESS_NORTE,
        "P12",
        1,
        "10:00",
        "miguel",
        "senior",
        STATUS_COMPLETED,
        CHANNEL_PHONE,
    ),
    _a(
        "NC06",
        BUSINESS_NORTE,
        "P12",
        2,
        "10:00",
        "carlos",
        "beard_ritual",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "carlos",
        RELATIONSHIP_SELF,
    ),
    _a(
        "NC07",
        BUSINESS_NORTE,
        "P8",
        1,
        "17:00",
        "diego",
        "fade",
        STATUS_COMPLETED,
        CHANNEL_WHATSAPP,
    ),
    _a(
        "NC08",
        BUSINESS_NORTE,
        "P8",
        2,
        "17:00",
        "hugo",
        "beard",
        STATUS_COMPLETED,
        CHANNEL_FRONT_DESK,
    ),
    _a(
        "NC09",
        BUSINESS_NORTE,
        "P5",
        1,
        "10:00",
        "andres",
        "classic_shave",
        STATUS_COMPLETED,
        CHANNEL_EMAIL,
    ),
    _a(
        "NC10",
        BUSINESS_NORTE,
        "P5",
        2,
        "10:00",
        "ruben",
        "head_shave",
        STATUS_COMPLETED,
        CHANNEL_PHONE,
    ),
    _a(
        "NC11",
        BUSINESS_NORTE,
        "P3",
        1,
        "16:00",
        "oscar",
        "maintenance",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "oscar",
        RELATIONSHIP_SELF,
    ),
    _a(
        "NC12",
        BUSINESS_NORTE,
        "P3",
        2,
        "16:00",
        "nico",
        "child",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "oscar",
        RELATIONSHIP_FATHER,
    ),
    _a(
        "NC13",
        BUSINESS_NORTE,
        "P2",
        1,
        "10:00",
        "tomas",
        "brows",
        STATUS_COMPLETED,
        CHANNEL_FRONT_DESK,
    ),
    _a(
        "NC14",
        BUSINESS_NORTE,
        "P2",
        2,
        "10:00",
        "javier",
        "grey",
        STATUS_COMPLETED,
        CHANNEL_PUBLIC_WEB,
        "javier",
        RELATIONSHIP_SELF,
    ),
    _a("NC15", BUSINESS_NORTE, "P1", 1, "16:00", "ivan", "color", STATUS_COMPLETED, CHANNEL_PHONE),
    # 2 ausencias.
    _a(
        "NN01",
        BUSINESS_NORTE,
        "P18",
        1,
        "18:00",
        "marcos",
        "fade",
        STATUS_NO_SHOW,
        CHANNEL_WHATSAPP,
    ),
    _a("NN02", BUSINESS_NORTE, "P10", 2, "16:30", "diego", "beard", STATUS_NO_SHOW, CHANNEL_PHONE),
    # 4 cancelaciones.
    _a(
        "NX01",
        BUSINESS_NORTE,
        "P19",
        1,
        "10:00",
        "sergio",
        "clipper",
        STATUS_CANCELLED,
        CHANNEL_PHONE,
        cancellation="Cambio de turno laboral.",
    ),
    _a(
        "NX02",
        BUSINESS_NORTE,
        "P15",
        2,
        "18:00",
        "carlos",
        "beard_ritual",
        STATUS_CANCELLED,
        CHANNEL_PUBLIC_WEB,
        "carlos",
        RELATIONSHIP_SELF,
        cancellation="El cliente solicitó online cambiar la fecha; el profesional canceló la cita para reprogramarla.",
    ),
    _a(
        "NX03",
        BUSINESS_NORTE,
        "P11",
        1,
        "17:00",
        "hugo",
        "classic",
        STATUS_CANCELLED,
        CHANNEL_WHATSAPP,
        cancellation="Cambio de disponibilidad avisado con antelación.",
    ),
    _a(
        "NX04",
        BUSINESS_NORTE,
        "P7",
        2,
        "18:00",
        "ruben",
        "classic_shave",
        STATUS_CANCELLED,
        CHANNEL_EMAIL,
        cancellation="Prefiere elegir otra fecha.",
    ),
    # 3 confirmadas ya transcurridas.
    _a(
        "NP01",
        BUSINESS_NORTE,
        "P1",
        1,
        "10:00",
        "javier",
        "classic",
        STATUS_CONFIRMED,
        CHANNEL_FRONT_DESK,
    ),
    _a("NP02", BUSINESS_NORTE, "P1", 2, "10:00", "marcos", "fade", STATUS_CONFIRMED, CHANNEL_PHONE),
    _a(
        "NP03",
        BUSINESS_NORTE,
        "P1",
        1,
        "11:00",
        "oscar",
        "beard",
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "oscar",
        RELATIONSHIP_SELF,
    ),
    # 12 futuras.
    _a(
        "NF01",
        BUSINESS_NORTE,
        "F0",
        1,
        "10:00",
        "javier",
        ("classic", "beard"),
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "javier",
        RELATIONSHIP_SELF,
    ),
    _a(
        "NF02",
        BUSINESS_NORTE,
        "F0",
        2,
        "10:00",
        "alvaro",
        "fade",
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "alvaro",
        RELATIONSHIP_SELF,
    ),
    _a(
        "NF03",
        BUSINESS_NORTE,
        "F0",
        1,
        "11:00",
        "nico",
        "child",
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "oscar",
        RELATIONSHIP_FATHER,
    ),
    _a(
        "NF04",
        BUSINESS_NORTE,
        "F0",
        2,
        "11:00",
        "carlos",
        "beard_ritual",
        STATUS_CONFIRMED,
        CHANNEL_PUBLIC_WEB,
        "carlos",
        RELATIONSHIP_SELF,
    ),
    _a(
        "NF05",
        BUSINESS_NORTE,
        "F0",
        1,
        "16:00",
        "marcos",
        "scissor",
        STATUS_CONFIRMED,
        CHANNEL_WHATSAPP,
    ),
    _a(
        "NF06",
        BUSINESS_NORTE,
        "F0",
        2,
        "16:00",
        "sergio",
        "clipper",
        STATUS_CONFIRMED,
        CHANNEL_FRONT_DESK,
    ),
    _a(
        "NF07",
        BUSINESS_NORTE,
        "F1",
        1,
        "10:00",
        "miguel",
        "senior",
        STATUS_CONFIRMED,
        CHANNEL_PHONE,
    ),
    _a(
        "NF08",
        BUSINESS_NORTE,
        "F1",
        2,
        "10:00",
        "diego",
        "fade",
        STATUS_CONFIRMED,
        CHANNEL_WHATSAPP,
    ),
    _a(
        "NF09",
        BUSINESS_NORTE,
        "F1",
        1,
        "11:00",
        "hugo",
        "classic_shave",
        STATUS_CONFIRMED,
        CHANNEL_EMAIL,
    ),
    _a("NF10", BUSINESS_NORTE, "F1", 2, "11:00", "andres", "grey", STATUS_CONFIRMED, CHANNEL_PHONE),
    _a(
        "NF11",
        BUSINESS_NORTE,
        "F2",
        1,
        "17:00",
        "ruben",
        ("classic", "brows"),
        STATUS_CONFIRMED,
        CHANNEL_FRONT_DESK,
    ),
    _a(
        "NF12",
        BUSINESS_NORTE,
        "F12",
        2,
        "18:00",
        "tomas",
        "classic",
        STATUS_CONFIRMED,
        CHANNEL_PHONE,
    ),
)

APPOINTMENTS = MARI_APPOINTMENTS + NORTE_APPOINTMENTS


def previous_business_days(
    anchor_date: date,
    count: int,
    *,
    excluded_dates: Iterable[date] = (),
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4),
) -> tuple[date, ...]:
    """Devuelve P1..Pn, desde el día hábil anterior más cercano."""

    excluded = frozenset(excluded_dates)
    result: list[date] = []
    cursor = anchor_date - timedelta(days=1)
    while len(result) < count:
        if cursor.weekday() in weekdays and cursor not in excluded:
            result.append(cursor)
        cursor -= timedelta(days=1)
    return tuple(result)


def future_business_days(
    anchor_date: date,
    now: datetime,
    count: int,
    *,
    first_opening_time: time = time(9, 0),
    excluded_dates: Iterable[date] = (),
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4),
) -> tuple[date, ...]:
    """Devuelve F0..Fn manteniendo todas las citas por delante de ``now``.

    El reset de las 04:05 puede usar hoy como F0. Una ejecución manual una vez
    iniciada la jornada comienza en el siguiente día hábil para que los conteos
    de citas futuras sigan siendo deterministas.
    """

    if count < 0:
        raise ValueError("count no puede ser negativo.")
    excluded = frozenset(excluded_dates)
    cursor = max(anchor_date, now.date())
    now_clock = time(now.hour, now.minute, now.second, now.microsecond)
    if cursor == now.date() and now_clock >= first_opening_time:
        cursor += timedelta(days=1)

    result: list[date] = []
    while len(result) < count:
        if cursor.weekday() in weekdays and cursor not in excluded:
            result.append(cursor)
        cursor += timedelta(days=1)
    return tuple(result)


def resolve_day_token(
    token: str,
    *,
    past_days: tuple[date, ...],
    future_days: tuple[date, ...],
) -> date:
    """Resuelve un token P1..Pn o F0..Fn sobre calendarios ya calculados."""

    if not isinstance(token, str) or len(token) < 2:
        raise ValueError(f"Token de día no válido: {token!r}. Debe tener formato P1..Pn o F0..Fn.")

    prefix = token[0]
    raw_index = token[1:]
    if prefix not in {"P", "F"} or not raw_index.isascii() or not raw_index.isdigit():
        raise ValueError(f"Token de día no válido: {token!r}. Debe tener formato P1..Pn o F0..Fn.")

    ordinal = int(raw_index)
    if prefix == "P":
        if ordinal < 1 or ordinal > len(past_days):
            available = (
                f"El rango disponible es P1..P{len(past_days)}."
                if past_days
                else "No hay tokens pasados disponibles."
            )
            raise ValueError(f"Token pasado fuera de rango: {token!r}. {available}")
        return past_days[ordinal - 1]

    if ordinal >= len(future_days):
        available = (
            f"El rango disponible es F0..F{len(future_days) - 1}."
            if future_days
            else "No hay tokens futuros disponibles."
        )
        raise ValueError(f"Token futuro fuera de rango: {token!r}. {available}")
    return future_days[ordinal]


EXPECTED_STATUS_COUNTS = {
    BUSINESS_MARI: Counter(
        {
            STATUS_COMPLETED: 22,
            STATUS_NO_SHOW: 4,
            STATUS_CANCELLED: 5,
            STATUS_CONFIRMED: 23,
        }
    ),
    BUSINESS_NORTE: Counter(
        {
            STATUS_COMPLETED: 15,
            STATUS_NO_SHOW: 2,
            STATUS_CANCELLED: 4,
            STATUS_CONFIRMED: 15,
        }
    ),
}

EXPECTED_CHANNEL_COUNTS = {
    BUSINESS_MARI: Counter(
        {
            CHANNEL_PHONE: 13,
            CHANNEL_PUBLIC_WEB: 18,
            CHANNEL_WHATSAPP: 9,
            CHANNEL_FRONT_DESK: 8,
            CHANNEL_EMAIL: 6,
        }
    ),
    BUSINESS_NORTE: Counter(
        {
            CHANNEL_PHONE: 10,
            CHANNEL_PUBLIC_WEB: 12,
            CHANNEL_WHATSAPP: 5,
            CHANNEL_FRONT_DESK: 6,
            CHANNEL_EMAIL: 3,
        }
    ),
}

EXPECTED_TEMPORAL_CONFIRMED = {
    BUSINESS_MARI: {"past": 4, "future": 19},
    BUSINESS_NORTE: {"past": 3, "future": 12},
}

BUSINESS_SHIFTS = {
    BUSINESS_MARI: ((9 * 60, 14 * 60), (16 * 60, 20 * 60)),
    BUSINESS_NORTE: ((10 * 60, 14 * 60), (16 * 60, 21 * 60)),
}


def _clock_minutes(value: str) -> int:
    parsed = time.fromisoformat(value)
    return parsed.hour * 60 + parsed.minute


def appointment_duration_minutes(spec: AppointmentSpec) -> int:
    if spec.duration_minutes is not None:
        return spec.duration_minutes
    services_by_key = {(service.business, service.key): service for service in SERVICES}
    return sum(
        services_by_key[(spec.business, service_key)].duration_minutes
        for service_key in spec.service_keys
    )


def validate_scenario() -> dict[str, int]:
    """Valida el contrato completo sin base de datos y devuelve sus totales."""

    errors: list[str] = []
    service_keys = [(item.business, item.key) for item in SERVICES]
    client_keys = [(item.business, item.key) for item in CLIENTS]
    access_key_rows = [(item.business, item.client_key) for item in ACCESSES]
    access_email_rows = [
        (item.business, item.email.strip().casefold()) for item in ACCESSES if item.email.strip()
    ]
    services_by_key = {(item.business, item.key): item for item in SERVICES}
    clients_by_key = {(item.business, item.key): item for item in CLIENTS}
    access_keys = set(access_key_rows)
    relationship_keys = {
        (item.business, item.representative_key, item.beneficiary_key, item.relationship)
        for item in RELATIONSHIPS
    }

    if len(SERVICES) != 28 or len(MARI_SERVICES) != 14 or len(NORTE_SERVICES) != 14:
        errors.append("El catálogo debe contener 14 servicios por negocio.")
    if len(set(service_keys)) != len(service_keys):
        errors.append("Las claves de servicio deben ser únicas dentro de cada negocio.")
    if sum(item.is_active for item in MARI_SERVICES) != 12:
        errors.append("Peluquería Mari debe tener 12 servicios activos.")
    if sum(item.is_active for item in NORTE_SERVICES) != 13:
        errors.append("Barbería Norte debe tener 13 servicios activos.")
    if len(CLIENTS) != 36 or len(MARI_CLIENTS) != 22 or len(NORTE_CLIENTS) != 14:
        errors.append("El escenario debe contener 22 + 14 clientes.")
    if len(set(client_keys)) != len(client_keys):
        errors.append("Las claves de cliente deben ser únicas dentro de cada negocio.")
    if sum(not item.is_active for item in CLIENTS) != 1:
        errors.append("Debe existir exactamente una ficha inactiva.")
    if sum(not item.phone for item in CLIENTS) != 1:
        errors.append("Debe existir exactamente una ficha sin teléfono.")
    if Counter(item.business for item in ACCESSES) != Counter(
        {BUSINESS_MARI: 7, BUSINESS_NORTE: 4}
    ):
        errors.append("Los accesos online deben distribuirse 7 + 4.")
    if len(access_keys) != len(access_key_rows):
        errors.append("Cada cliente solo puede tener un acceso online por negocio.")
    if len(set(access_email_rows)) != len(access_email_rows):
        errors.append("Los correos de acceso deben ser únicos dentro de cada negocio.")
    for access in ACCESSES:
        access_key = (access.business, access.client_key)
        client = clients_by_key.get(access_key)
        if client is None:
            errors.append(f"Acceso {access.email!r}: cliente inexistente o de otro negocio.")
        elif not client.is_active:
            errors.append(f"Acceso {access.email!r}: la ficha de cliente está inactiva.")
        elif not client.phone.strip():
            errors.append(f"Acceso {access.email!r}: la ficha de cliente no tiene teléfono.")
        if not access.email.strip():
            errors.append(f"Acceso de {access_key}: correo vacío.")
        if not access.is_active:
            errors.append(f"Acceso {access.email!r}: debe estar activo.")
        if not access.email_verified:
            errors.append(f"Acceso {access.email!r}: el correo debe estar verificado.")
    if Counter(item.business for item in RELATIONSHIPS) != Counter(
        {BUSINESS_MARI: 3, BUSINESS_NORTE: 1}
    ):
        errors.append("Las relaciones representativas deben distribuirse 3 + 1.")
    if len(APPOINTMENTS) != 90 or len(MARI_APPOINTMENTS) != 54 or len(NORTE_APPOINTMENTS) != 36:
        errors.append("Las citas deben distribuirse 54 + 36.")
    if len({item.key for item in APPOINTMENTS}) != len(APPOINTMENTS):
        errors.append("Las claves de cita deben ser únicas.")

    for business in (BUSINESS_MARI, BUSINESS_NORTE):
        rows = [item for item in APPOINTMENTS if item.business == business]
        if Counter(item.status for item in rows) != EXPECTED_STATUS_COUNTS[business]:
            errors.append(f"Conteo de estados incorrecto para {business}.")
        if Counter(item.channel for item in rows) != EXPECTED_CHANNEL_COUNTS[business]:
            errors.append(f"Conteo de canales incorrecto para {business}.")
        temporal = {
            "past": sum(
                item.status == STATUS_CONFIRMED and item.day_token.startswith("P") for item in rows
            ),
            "future": sum(
                item.status == STATUS_CONFIRMED and item.day_token.startswith("F") for item in rows
            ),
        }
        if temporal != EXPECTED_TEMPORAL_CONFIRMED[business]:
            errors.append(f"Conteo temporal de confirmadas incorrecto para {business}.")

    adjusted_count = 0
    representative_web_count = 0
    intervals: dict[tuple[str, str, int], list[tuple[int, int, str]]] = {}
    for spec in APPOINTMENTS:
        client_key = (spec.business, spec.client_key)
        if client_key not in clients_by_key:
            errors.append(f"{spec.key}: cliente inexistente.")
        for service_key in spec.service_keys:
            service = services_by_key.get((spec.business, service_key))
            if service is None:
                errors.append(
                    f"{spec.key}: servicio {service_key!r} inexistente o de otro negocio."
                )
            elif not service.is_active and not spec.day_token.startswith("P"):
                errors.append(
                    f"{spec.key}: el servicio pausado {service_key!r} solo puede "
                    "aparecer en citas históricas P."
                )

        service_minutes = sum(
            services_by_key[(spec.business, key)].duration_minutes
            for key in spec.service_keys
            if (spec.business, key) in services_by_key
        )
        duration = appointment_duration_minutes(spec)
        if duration != service_minutes:
            adjusted_count += 1
            if not spec.duration_adjustment_reason.strip():
                errors.append(f"{spec.key}: ajuste de duración sin motivo.")
        elif spec.duration_adjustment_reason:
            errors.append(f"{spec.key}: motivo de ajuste sin diferencia de duración.")

        if spec.status == STATUS_CANCELLED and not spec.cancellation_reason.strip():
            errors.append(f"{spec.key}: cancelación sin motivo.")
        if spec.status != STATUS_CANCELLED and spec.cancellation_reason:
            errors.append(f"{spec.key}: motivo de cancelación en una cita no cancelada.")

        if spec.channel == CHANNEL_PUBLIC_WEB:
            if not spec.requester_key or not spec.requester_relationship:
                errors.append(f"{spec.key}: reserva web sin solicitante o relación.")
            if (spec.business, spec.requester_key) not in access_keys:
                errors.append(f"{spec.key}: solicitante web sin cuenta activa declarada.")
            if spec.requester_key == spec.client_key:
                if spec.requester_relationship != RELATIONSHIP_SELF:
                    errors.append(f"{spec.key}: reserva propia sin relación titular.")
            else:
                representative_web_count += 1
                relation = (
                    spec.business,
                    spec.requester_key,
                    spec.client_key,
                    spec.requester_relationship,
                )
                if relation not in relationship_keys:
                    errors.append(f"{spec.key}: representación web no autorizada.")
        elif spec.requester_key or spec.requester_relationship:
            errors.append(f"{spec.key}: una cita manual no debe declarar solicitante web.")

        if not (1 <= spec.line_number <= (3 if spec.business == BUSINESS_MARI else 2)):
            errors.append(f"{spec.key}: línea fuera del rango del negocio.")
        start = _clock_minutes(spec.start_time)
        end = start + duration
        if start % 15 or duration % 15:
            errors.append(f"{spec.key}: hora o duración fuera de la cadencia de 15 minutos.")
        if not any(
            shift_start <= start and end <= shift_end
            for shift_start, shift_end in BUSINESS_SHIFTS[spec.business]
        ):
            errors.append(f"{spec.key}: cita fuera del horario declarado.")
        intervals.setdefault((spec.business, spec.day_token, spec.line_number), []).append(
            (start, end, spec.key)
        )

    if adjusted_count != 1:
        errors.append("Debe existir exactamente una cita con duración ajustada.")
    if representative_web_count != 8:
        errors.append("Deben existir exactamente ocho reservas web representadas.")
    if sum(item.channel == CHANNEL_PUBLIC_WEB for item in APPOINTMENTS) != 30:
        errors.append("Deben existir exactamente 30 reservas web.")
    if sum(len(item.service_keys) for item in APPOINTMENTS) != 103:
        errors.append("El escenario debe contener 103 snapshots de servicio.")
    if any(
        item.business == BUSINESS_MARI
        and item.client_key == "carmen"
        and item.day_token.startswith("F")
        for item in APPOINTMENTS
    ):
        errors.append("Carmen no debe tener citas futuras en el escenario canónico.")

    for group, values in intervals.items():
        ordered = sorted(values)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous[1] > current[0]:
                errors.append(f"Solape en {group}: {previous[2]} y {current[2]}.")

    if errors:
        raise ValueError("Escenario demo inválido:\n- " + "\n- ".join(errors))

    return {
        "services": len(SERVICES),
        "clients": len(CLIENTS),
        "accesses": len(ACCESSES),
        "relationships": len(RELATIONSHIPS),
        "appointments": len(APPOINTMENTS),
        "appointment_services": sum(len(item.service_keys) for item in APPOINTMENTS),
        "public_web_appointments": sum(item.channel == CHANNEL_PUBLIC_WEB for item in APPOINTMENTS),
        "represented_web_appointments": representative_web_count,
    }


SCENARIO_COUNTS = validate_scenario()
