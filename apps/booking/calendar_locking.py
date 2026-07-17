from dataclasses import dataclass

from django.db import transaction

from apps.booking.models import BusinessCalendarSettings, WorkLine
from apps.businesses.models import Business


@dataclass(frozen=True)
class LockedBusinessCalendar:
    business: Business
    work_lines_by_id: dict[int, WorkLine]
    settings: BusinessCalendarSettings


def lock_business_calendar(business) -> LockedBusinessCalendar:
    """Bloquea una agenda completa con un orden único para evitar carreras."""
    database_connection = transaction.get_connection()
    if not database_connection.in_atomic_block:
        raise RuntimeError("El bloqueo de calendario requiere una transacción atómica.")

    # La sincronización BOE toma SHARE sobre este registro antes de enumerar
    # negocios. Declarar ROW EXCLUSIVE al inicio mantiene el mismo orden global:
    # las mutaciones en curso terminan antes del snapshot y las nuevas esperan.
    # Es compatible entre operaciones ordinarias y no afecta a SQLite.
    if database_connection.vendor == "postgresql":
        table_name = database_connection.ops.quote_name(Business._meta.db_table)
        with database_connection.cursor() as cursor:
            cursor.execute(f"LOCK TABLE {table_name} IN ROW EXCLUSIVE MODE")

    work_lines_by_id = {
        line.pk: line
        for line in WorkLine.objects.select_for_update()
        .filter(business_id=business.pk)
        .order_by("pk")
    }

    # Business es el mutex estable: existe incluso si aún falta la fila opcional
    # de ajustes. Todas las rutas cooperantes llegan aquí después de las líneas.
    locked_business = Business.objects.select_for_update().get(pk=business.pk)
    calendar_settings, _created = BusinessCalendarSettings.objects.get_or_create(
        business=locked_business
    )
    calendar_settings = BusinessCalendarSettings.objects.select_for_update().get(
        pk=calendar_settings.pk
    )
    return LockedBusinessCalendar(
        business=locked_business,
        work_lines_by_id=work_lines_by_id,
        settings=calendar_settings,
    )
