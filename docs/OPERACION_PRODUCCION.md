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

## Cabeceras y contenido activo

AgendaSalon envía una CSP en todas las respuestas. Las rutas de producto solo
aceptan JavaScript del mismo origen. Los estilos y fuentes de Google se limitan a
`fonts.googleapis.com` y `fonts.gstatic.com`; ningún otro tercero queda
autorizado. Django Admin recibe una excepción de script inline limitada a
`/admin/` para conservar su funcionamiento. Producción añade
`upgrade-insecure-requests`. `Permissions-Policy` desactiva cámara, micrófono,
geolocalización, pagos, USB y Topics; CORP limita los recursos propios al mismo
origen.

Antes de desplegar deben recorrerse reserva, agenda React, dashboard React y
Django Admin con la consola del navegador abierta. HSTS preload no se activa
hasta confirmar dominio definitivo, HTTPS estable y el periodo de HSTS exigido.

## Administración técnica

El panel `/superadmin/` pertenece al producto y gestiona negocios, accesos y
reserva pública. Django Admin, bajo `/admin/`, queda reservado para mantenimiento
y diagnóstico técnico. No sustituye los flujos funcionales ni debe enlazarse
desde las pantallas de profesionales o clientes.

Django permite entrar a una cuenta activa con `is_staff`; después aplica los
permisos de cada modelo. Un superusuario dispone de acceso completo. En
producción deben cumplirse estas reglas:

- cuentas técnicas personales, nunca compartidas;
- privilegios mínimos por modelo cuando no sea necesario un superusuario;
- separación entre administración funcional y mantenimiento técnico;
- acceso restringido por red, VPN o IP cuando la infraestructura lo permita;
- uso excepcional para diagnóstico, recuperación o corrección controlada, no
  para la operativa cotidiana.

La cuenta superadministradora de la semilla local reúne ambos papeles solo para
la demostración. Las pruebas automatizadas confirman que un profesional sin
`is_staff` no entra, que el personal técnico limitado no obtiene modelos sin
permiso y que el superusuario sí puede administrarlos.

## Medios públicos

Las imágenes públicas admiten JPG, PNG o WebP hasta 5 MB y 16 millones de
píxeles. Se orientan, se reducen a un máximo de 2400 px, se recodifican como WebP
estático con un perfil equilibrado y se guardan sin EXIF ni metadatos. El
despliegue debe añadir un límite moderado por cuenta o por ruta para la subida de
medios. Si el volumen real crece, el procesamiento deberá pasar a un trabajador
en segundo plano con concurrencia acotada.

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
