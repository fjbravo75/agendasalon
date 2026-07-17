# Operación segura de AgendaSalon

Este documento define el contrato técnico y las operaciones del despliegue. La
demo académica quedó publicada el 14 de julio de 2026 en
`https://agendasalon.brvsoftwarestudio.com`; los comandos siguen requiriendo una
ejecución deliberada y no se activan por leer este documento.

Estado al cierre operativo de P1: el SHA funcional
`105531945452b5529be6891ee47034c164e804f3` está desplegado y aceptado en
producción. La integración pasó por las PR #7 (merge `c4f60c8`) y #8 (merge
`1055319`); las ejecuciones de CI `29573943958` y `29574584566` finalizaron
correctamente.

El despliegue se protegió con la copia fría
`agendasalon-20260717T105047Z` y el snapshot
`pre-agendasalon-p1-robustez-2026-07-17-1051Z`, ID `237297105`, acción
`3295909145`, creado el 17 de julio de 2026 a las 10:51:55 UTC. La copia
posterior verificada es `agendasalon-20260717T105901Z`.

La comprobación posterior conservó exactamente 2 negocios, 3 usuarios, 8
clientes, 4 accesos, 23 citas, 5 sesiones, outbox vacío y ninguna solicitud de
alta. Los libros legales mantuvieron sus correspondencias 6/6 y 8/8; las 23
citas históricas conservaron a `null` su referencia pública. Servicios y
temporizadores quedaron activos; la primera ejecución automática del correo
tras el rearme, a las 11:11:27 UTC, terminó correctamente con 0 procesados,
enviados, reprogramados, fallidos y cancelados. La aceptación pública se limitó
a GET y consultas de solo lectura, sin crear datos ni dejar residuo.

## Perfil de producción

La aplicación debe arrancar con `config.settings.prod`. WSGI y ASGI usan ese
perfil por defecto y detienen el arranque cuando falta alguna de estas variables:

- `DJANGO_SECRET_KEY`;
- `DJANGO_ALLOWED_HOSTS`;
- `DJANGO_DATABASE_URL`;
- `AGENDA_BACKUP_HMAC_KEY`, secreto aleatorio independiente del destino de copias.

`AGENDA_BACKUP_SCHEDULE_CONFIGURED=1` declara en el panel que el operador ha
instalado y comprobado la programación. No activa ninguna tarea por sí solo y no
debe usarse si el temporizador real no está habilitado.

La configuración legal se elige de forma explícita:

- demo académica sin actividad comercial: `AGENDA_PLATFORM_LEGAL_DEMO=1`, con
  `AGENDA_PLATFORM_LEGAL_NAME`, `AGENDA_PLATFORM_PRIVACY_EMAIL` y
  `AGENDA_PLATFORM_WEBSITE`; NIF y domicilio deben quedar ausentes o vacíos;
- actividad comercial: `AGENDA_PLATFORM_LEGAL_DEMO=0`, con los cinco datos
  legales completos y reales, incluidos `AGENDA_PLATFORM_TAX_ID` y
  `AGENDA_PLATFORM_LEGAL_ADDRESS`.

El arranque falla si el indicador tiene un valor ambiguo, si faltan datos
obligatorios del modo elegido o si una demo intenta introducir NIF o domicilio.
El modo académico solo cambia la identidad mostrada: conserva `DEBUG=False`,
PostgreSQL, redirección HTTPS, cookies seguras, secretos y el resto del perfil de
producción.

Si existe un proxy inverso, sus direcciones deben declararse de forma explícita
en `DJANGO_TRUSTED_PROXY_IPS`, separadas por comas. Solo entonces AgendaSalon
consulta `X-Forwarded-For`; recorre la cadena desde el proxy más cercano y no
confía en una cabecera enviada directamente por el cliente.

La topología de despliegue debe impedir el acceso directo al proceso Django.
Producción declara `SECURE_PROXY_SSL_HEADER` para reconocer HTTPS cuando Nginx
termina TLS y comunica con Gunicorn mediante HTTP interno. Esta confianza solo
es segura si Nginx sobrescribe siempre `X-Forwarded-Proto` con su propio
`$scheme`, sin conservar un valor enviado por el cliente. El socket de Gunicorn
no debe quedar expuesto a Internet.

## Despliegue académico verificado

La instancia pública usa esta topología:

- Nginx termina TLS y sirve estáticos y medios;
- `gunicorn-agendasalon.service` está habilitado y comunica únicamente mediante
  `/run/agendasalon/gunicorn.sock`;
- PostgreSQL utiliza un rol y una base exclusivos de AgendaSalon;
- el entorno protegido vive fuera del repositorio y solo declara
  `127.0.0.1` como proxy de confianza;
- el certificado Let's Encrypt ECDSA vence el 12 de octubre de 2026 y dispone de
  renovación automática.

La validación pública recorrió el centro legal, el acceso profesional, el panel
profesional y la consulta de huecos de reserva en escritorio y 390 x 844 px. No
aparecieron errores de consola, errores de página, recursos fallidos ni
desbordamiento horizontal. HTTP redirige a HTTPS y las cabeceras CSP, HSTS,
`Permissions-Policy`, CORP, COOP, `nosniff` y política de referencia están
presentes.

El 14 de julio de 2026 se creó una primera copia local autenticada y verificada
de PostgreSQL y medios y se habilitó `backup-agendasalon.timer` con ejecución
diaria persistente. La misma unidad aplica después la retención 7/4/6 y
`check-agendasalon-backup.timer` comprueba diariamente que la copia más reciente
sea auténtica, íntegra y tenga menos de 36 horas. Una vigilancia local de Codex
revisa además temporizadores, resultados, frescura y espacio en disco. El
destino externo cifrado continúa pendiente, por lo que la continuidad completa
todavía no se declara cerrada.

La base de datos de producción debe ser PostgreSQL. Formato esperado:

```text
postgresql://usuario:contraseña@servidor:5432/agendasalon?sslmode=require
```

La URL y el resto de secretos deben vivir en el gestor de variables del
servidor, nunca en Git, el README, una unidad de servicio ni la línea de
comandos.

## Correo transaccional

AgendaSalon dispone de una cola persistente para activaciones profesionales,
verificaciones de correo, confirmaciones de cita y recordatorios programados para
24 horas antes. Una operación de negocio no se revierte porque el proveedor de
correo falle: el mensaje queda pendiente o registra un fallo controlado para
reintento y diagnóstico.

El proveedor elegido para producción es Brevo. La cuenta, el remitente
`AgendaSalon <agendasalon@brvsoftwarestudio.com>` y el dominio
`brvsoftwarestudio.com` están verificados. El subdominio de marca es
`correo.brvsoftwarestudio.com` y los registros CNAME, DKIM, DMARC y código de
verificación se publican en el DNS autoritativo de DigitalOcean.

DigitalOcean bloquea en sus droplets los puertos SMTP 25, 465 y 587. Por ese
motivo AgendaSalon utiliza el relay de Brevo por el puerto 2525 con STARTTLS:

```text
AGENDA_TRANSACTIONAL_EMAIL_ENABLED=1
EMAIL_HOST=smtp-relay.brevo.com
EMAIL_PORT=2525
EMAIL_HOST_USER=usuario-smtp-asignado-por-brevo
EMAIL_HOST_PASSWORD=secreto-fuera-de-git
DEFAULT_FROM_EMAIL=AgendaSalon <agendasalon@brvsoftwarestudio.com>
EMAIL_USE_TLS=1
EMAIL_USE_SSL=0
EMAIL_TIMEOUT=20
AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS=120
```

`EMAIL_TIMEOUT` acota cada operación del backend SMTP. La reserva del worker
debe durar más que ese valor; producción rechaza el arranque si
`AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS` no lo supera. Mientras el proveedor sigue
procesando el mensaje, el worker renueva esa reserva con una conexión separada y
solo el propietario vigente puede cerrar el intento. Una referencia estable
permite correlacionar reintentos, pero no promete entrega exactamente una vez:
si el proveedor acepta el mensaje y el proceso cae antes de guardar el resultado,
el siguiente intento puede repetirlo.

La cancelación coordina el mismo estado. Un correo todavía pendiente se cancela
sin envío; si un worker ya lo reclama, la solicitud de cancelación queda
registrada sin robarle el `lease`. Una aceptación SMTP posterior se conserva como
enviada y un fallo posterior queda cancelado sin reintento. El latido reduce las
recuperaciones prematuras, pero SMTP mantiene un riesgo residual de entrega **al
menos una vez** ante una aceptación seguida de timeout o caída antes del commit.

La clave SMTP y la clave API se conservan en Windows Credential Manager bajo
los destinos `AgendaSalonBrevoSmtpKey` y `AgendaSalonBrevoApiKey`. El script
`ops/configure_brevo_smtp_production.ps1` lee la clave SMTP sin mostrarla y
actualiza el fichero protegido `/etc/agendasalon/agendasalon.env`, cuyo modo debe
seguir siendo `600`. Ninguna clave se almacena en Git, documentación o línea de
comandos.

Brevo restringe las claves SMTP y API a las direcciones autorizadas. Deben
permanecer en la lista la IP pública de administración y la IP del droplet de
AgendaSalon; cualquier cambio de infraestructura obliga a actualizar primero
esa lista para evitar una interrupción silenciosa del envío.

La validación del 14 de julio de 2026 se realizó desde Django en el droplet: el
backend SMTP devolvió un envío y Brevo registró el mismo mensaje como solicitado,
abierto y entregado. Esta prueba valida proveedor, puerto, TLS, autenticación,
remitente, DNS y restricción de IP. El despliegue posterior dejó aplicadas las
migraciones de outbox y activó el temporizador. Su ejecución manual de control
terminó con cero mensajes pendientes o fallidos, sin generar envíos ficticios.

TLS y SSL directo son excluyentes. Si se activa el correo y falta un dato SMTP,
el perfil de producción detiene el arranque para evitar una configuración a
medias. El procesamiento periódico se realiza con
`ops/systemd/agendasalon-email.timer`, que ejecuta cada cinco minutos:

```bash
python manage.py process_outbound_emails
```

Instalar o habilitar esa unidad, introducir credenciales o cambiar el entorno
público requiere una actuación de despliegue deliberada. En la demo publicada,
la unidad quedó instalada, habilitada y activa el 14 de julio de 2026; la mera
existencia de estos archivos en otros entornos no activa por sí sola el correo.

Estado de referencia del despliegue de correo:

- commit de aplicación: `ed509e2e59fa1721ef9abf3951951cc8bf999547`;
- migraciones aplicadas: `accounts.0005`, `customers.0010` y
  `notifications.0002`;
- `agendasalon-email.timer`: habilitado y activo;
- comando de outbox verificado: 0 procesados, 0 enviados y 0 pendientes o
  fallidos;
- cuentas y citas demo preservadas sin ejecutar de nuevo `seed_demo`.

## Cabeceras y contenido activo

AgendaSalon envía una CSP en todas las respuestas. Las rutas de producto solo
aceptan JavaScript del mismo origen. Los estilos y fuentes de Google se limitan a
`fonts.googleapis.com` y `fonts.gstatic.com`; ningún otro tercero queda
autorizado. Django Admin recibe una excepción de script inline limitada a
`/admin/` para conservar su funcionamiento. Producción añade
`upgrade-insecure-requests`. `Permissions-Policy` desactiva cámara, micrófono,
geolocalización, pagos, USB y Topics; CORP limita los recursos propios al mismo
origen.

En cada despliegue deben recorrerse reserva, agenda React, dashboard React y
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

Con `DJANGO_DATABASE_URL` y `AGENDA_BACKUP_HMAC_KEY` disponibles en el entorno y las herramientas cliente
de PostgreSQL instaladas:

```bash
python manage.py backup_agendasalon \
  --backup-root /var/backups/agendasalon \
  --media-root /var/www/agendasalon/shared/media \
  --destination external_encrypted

python ops/backup_restore.py verify \
  --backup-dir /var/backups/agendasalon/agendasalon-AAAAMMDDTHHMMSSZ
```

El comando de Django reutiliza el motor operativo, verifica la copia antes de
cerrar la ejecución y registra metadatos seguros para el panel
`/superadmin/continuidad/`. `--destination external_encrypted` declara que la
automatización ha replicado o replicará el conjunto en el almacenamiento externo
cifrado definido por el despliegue; no realiza por sí solo esa transferencia.
Hasta que esa integración exista debe usarse `--destination local` y el panel
seguirá mostrando que el destino externo está pendiente.

El registro conserva solo estado, tiempos, alcance, resultado de integridad,
tamaño y un código de fallo controlado. No guarda rutas de artefactos,
credenciales ni excepciones sin filtrar. La vista web es deliberadamente de solo
lectura: crear, descargar y restaurar copias continúa siendo una responsabilidad
operativa fuera del navegador.

Cada copia contiene:

- `database.dump`, generado por `pg_dump` en formato personalizado;
- `media.tar.gz`;
- `manifest.json`, sin credenciales, con sumas SHA-256 y una autenticación
  HMAC-SHA-256 anclada en una clave que no se almacena junto a la copia.

La copia local solo se considera válida si el comando de verificación termina
correctamente. La continuidad solo se considera protegida cuando, además, el
conjunto se replica y se comprueba en un destino externo cifrado.

En producción, `backup-agendasalon.timer` ejecuta la copia al menos una vez cada
24 horas. Su `ExecStartPost` aplica la retención de 7 representantes diarios, 4
semanales y 6 mensuales. Antes de seleccionar o borrar, verifica con HMAC y
SHA-256 todas las carpetas gestionadas; si encuentra una anomalía, falla sin
borrar ninguna. La simulación sin borrado es:

```bash
python -m ops.backup_restore retention \
  --backup-root /var/backups/agendasalon \
  --daily 7 --weekly 4 --monthly 6
```

El borrado requiere añadir `--apply`. En el servidor lo ejecuta únicamente la
unidad versionada en `ops/systemd/backup-agendasalon.service`.

`check-agendasalon-backup.timer` ejecuta cada día:

```bash
python -m ops.backup_restore health \
  --backup-root /var/backups/agendasalon \
  --max-age-hours 36
```

La comprobación falla ante ausencia, caducidad, fecha futura, autenticidad o
integridad incorrectas. La automatización local `Vigilar copias de AgendaSalon`
revisa el resultado y avisa a Fran si detecta un problema; no borra ni repara.
Todavía debe comprobarse periódicamente una restauración desde el futuro
destino externo. El estado `Protegido` del panel solo aparece después de una
ejecución externa correcta y reciente.

## Restaurar en un entorno limpio

La restauración destruye el contenido de la base de datos de destino. Debe
ejecutarse primero contra una base vacía de ensayo, con la aplicación detenida y
una URL que apunte expresamente a ese destino:

```bash
export DJANGO_DATABASE_URL='postgresql://usuario:contraseña@servidor:5432/agendasalon_restore?sslmode=require'

python ops/backup_restore.py restore \
  --backup-dir /var/backups/agendasalon/agendasalon-AAAAMMDDTHHMMSSZ \
  --media-target /var/www/agendasalon/shared/media-restaurada \
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

## Migraciones aditivas aplicadas en P1

El despliegue de P1 desde P0 aplicó únicamente estas cuatro operaciones y en
este orden exacto:

1. `booking.0007_appointment_public_confirmation_reference`;
2. `businesses.0012_businesssignuprequest_privacy_legal_context_snapshot`;
3. `legal.0007_legalacceptanceevent`;
4. `notifications.0004_outboundemail_delivery_lease`.

En una reproducción o recuperación, si aparece cualquier migración adicional,
falta alguna, el orden difiere o una parte de P1 ya figura aplicada, se aborta
la operación sin ejecutar `migrate`.
No se corrige un estado parcial con `--fake`, SQL manual ni migraciones por
aplicación: primero se diagnostica y, si procede, se restaura de forma completa.

En particular, `booking.0007_appointment_public_confirmation_reference` añade a
las citas una referencia UUID única y anulable para hacer idempotente la
confirmación pública. La operación no reescribe las citas existentes: las
reservas profesionales y todo el histórico conservan `NULL`.

Los borradores de sesión creados con P0 carecen de esa referencia: después del
despliegue se descartan de forma segura y vuelven a la búsqueda de hora. No deben
completarse, reconstruirse ni reproducirse manualmente.

Tras migrar y antes de reabrir los servicios se comprueba mediante consultas de
solo lectura que no cambian los recuentos de negocios, usuarios, clientes,
accesos ni citas; que no existen referencias públicas duplicadas; que los libros
legales y la outbox presentan recuentos y estados coherentes; y que el plan queda
completamente aplicado. En producción no se crea una cita de prueba, no se repite
un POST, no se fuerza un replay y no se altera ningún dato para «probar» P1.

La aceptación pública se limita a peticiones GET y superficies de solo lectura,
cabeceras, salud, estáticos y comparación de los recuentos tomados antes del
despliegue. Si una comprobación exigiera escribir, se realiza en una copia
desechable, nunca sobre la base canónica de producción.

## Reversión que cruce `booking.0006`

`booking.0006_enforce_appointment_outcomes` no dispone de una operación inversa
segura. Si una vuelta de versión debe cruzar ese límite, no se ejecuta
`migrate booking 0005` ni se marca la migración como `fake` para aparentar una
reversión.

El procedimiento autorizado es una restauración completa y coherente:

1. detener Gunicorn y todos los temporizadores o procesos que puedan escribir;
2. verificar autenticidad, integridad y fecha de la copia o snapshot elegido;
3. restaurar PostgreSQL y `media` como una sola unidad;
4. desplegar el SHA que corresponda exactamente a ese esquema y esos datos;
5. comprobar migraciones sin usar `fake`, recopilar estáticos y arrancar los
   servicios;
6. contrastar recuentos, sesiones, outbox, logs, salud y rutas HTTPS antes de
   reabrir escrituras.

Las migraciones P1, incluida `booking.0007`, son aditivas. `legal.0007` está
marcada expresamente como irreversible para impedir que una marcha atrás elimine
los libros de eventos y su histórico posterior al despliegue. No se debe intentar
sortear esa protección con `--fake` ni revertir P1 por aplicaciones sueltas: ante
una incidencia se restaura de forma completa y verificable la copia o el snapshot
previo.

## Ensayo realizado

El 11 de julio de 2026 se ejecutó un ensayo local aislado con PostgreSQL 17:

- migraciones completas;
- semilla demo;
- `pg_dump` de la base original;
- restauración mediante `pg_restore` en otra base limpia;
- comparación final: 2 negocios, 19 citas y 7 clientes en origen y destino;
- 172 pruebas correctas sobre PostgreSQL, incluida concurrencia real de estados.

El ensayo valida el procedimiento técnico. La programación local, la retención
7/4/6 y la vigilancia de frescura quedaron activas y verificadas en el
despliegue del 14 de julio de 2026. Falta elegir y probar el destino externo
cifrado que conservará otra copia fuera del Droplet.

Como comprobación posterior, el 12 de julio de 2026 se ejecutó la suite completa
de 240 pruebas sobre PostgreSQL 17 en un contenedor aislado. Incluyó migraciones,
la corrección histórica de minimización de trazas públicas, la concurrencia real
de citas y el presupuesto de consultas del dashboard. El resultado fue
correcto. Esta evidencia no sustituye la restauración ensayada el día anterior
ni autoriza por sí sola un despliegue.
