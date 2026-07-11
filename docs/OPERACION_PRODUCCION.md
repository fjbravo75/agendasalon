# Operación segura de AgendaSalon

Este documento define el contrato técnico previo al despliegue. No activa
ningún servidor ni servicio externo.

## Perfil de producción

La aplicación debe arrancar con `config.settings.prod`. WSGI y ASGI usan ese
perfil por defecto y detienen el arranque cuando falta alguna de estas variables:

- `DJANGO_SECRET_KEY`;
- `DJANGO_ALLOWED_HOSTS`;
- `DJANGO_DATABASE_URL`.

Si existe un proxy inverso, sus direcciones deben declararse de forma explícita
en `DJANGO_TRUSTED_PROXY_IPS`, separadas por comas. Solo entonces AgendaSalon
consulta `X-Forwarded-For`; recorre la cadena desde el proxy más cercano y no
confía en una cabecera enviada directamente por el cliente.

La base de datos de producción debe ser PostgreSQL. Formato esperado:

```text
postgresql://usuario:contraseña@servidor:5432/agendasalon?sslmode=require
```

La URL y el resto de secretos deben vivir en el gestor de variables del
servidor, nunca en Git, el README, una unidad de servicio ni la línea de
comandos.

## Objetivos iniciales de continuidad

Para la demo del PFM se fija un objetivo inicial y revisable:

- RPO: hasta 24 horas de datos;
- RTO: restauración operativa en menos de 2 horas;
- retención: 7 copias diarias, 4 semanales y 6 mensuales;
- destino: almacenamiento cifrado distinto del servidor de aplicación;
- alcance: base de datos PostgreSQL y directorio `media`.

Antes de una explotación comercial deben revisarse estos valores según volumen,
coste y compromisos con clientes.

Los contadores de intentos fallidos se conservan de forma seudonimizada. La
tarea operativa diaria debe retirar los que lleven 30 días inactivos:

```bash
python manage.py prune_security_throttles --days 30
```

## Crear y verificar una copia

Con `DJANGO_DATABASE_URL` disponible en el entorno y las herramientas cliente
de PostgreSQL instaladas:

```bash
python ops/backup_restore.py backup \
  --backup-root /var/backups/agendasalon \
  --media-root /srv/agendasalon/media

python ops/backup_restore.py verify \
  --backup-dir /var/backups/agendasalon/agendasalon-AAAAMMDDTHHMMSSZ
```

Cada copia contiene:

- `database.dump`, generado por `pg_dump` en formato personalizado;
- `media.tar.gz`;
- `manifest.json`, sin credenciales y con sumas SHA-256.

La copia solo se considera válida si el comando de verificación termina
correctamente y el conjunto se replica a un destino externo cifrado.

## Restaurar en un entorno limpio

La restauración destruye el contenido de la base de datos de destino. Debe
ejecutarse primero contra una base vacía de ensayo, con la aplicación detenida y
una URL que apunte expresamente a ese destino:

```bash
export DJANGO_DATABASE_URL='postgresql://usuario:contraseña@servidor:5432/agendasalon_restore?sslmode=require'

python ops/backup_restore.py restore \
  --backup-dir /var/backups/agendasalon/agendasalon-AAAAMMDDTHHMMSSZ \
  --media-target /srv/agendasalon/media-restaurada \
  --confirm-restore
```

Si el destino de media contiene archivos, la herramienta se detiene. La opción
`--replace-media` mueve primero el contenido anterior a una carpeta fechada de
reversión; no lo elimina silenciosamente.

Después de restaurar deben ejecutarse:

```bash
python manage.py migrate --check
python manage.py check --deploy
python manage.py shell -c "from apps.businesses.models import Business; print(Business.objects.count())"
```

También deben compararse los recuentos principales, abrir una imagen restaurada
y recorrer acceso, agenda y reserva pública antes de reabrir el servicio.

## Ensayo realizado

El 11 de julio de 2026 se ejecutó un ensayo local aislado con PostgreSQL 17:

- migraciones completas;
- semilla demo;
- `pg_dump` de la base original;
- restauración mediante `pg_restore` en otra base limpia;
- comparación final: 2 negocios, 19 citas y 7 clientes en origen y destino;
- 172 pruebas correctas sobre PostgreSQL, incluida concurrencia real de estados.

El ensayo valida el procedimiento técnico. Antes del despliegue falta elegir y
probar el destino externo cifrado que conservará las copias programadas.
