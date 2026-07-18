# Operación segura de AgendaSalon

Este documento define el contrato técnico y las operaciones del despliegue. La
demo académica quedó publicada el 14 de julio de 2026 en
`https://agendasalon.brvsoftwarestudio.com`; los comandos siguen requiriendo una
ejecución deliberada y no se activan por leer este documento.

Estado vigente: la versión funcional desplegada en producción corresponde a
`714a2a22a154b102f31140bc935c4e987c0a5d7e`. La ejecución de CI de esa versión,
`29625418697`, finalizó correctamente en sus cuatro trabajos. `main` puede
contener commits documentales posteriores sin cambios ejecutables. P0, P1 y P2 se
conservan como hitos históricos trazables; sus recuentos no describen el
escenario actual.

La aceptación vigente conserva 2 negocios, 3 cuentas internas, 28 servicios,
36 fichas de cliente, 11 accesos cliente, 4 relaciones de representación y 90
citas. La regeneración manual aceptada el 18 de julio de 2026 utilizó la fecha
base `2026-07-18`, el identificador
`682f8572-de61-4140-b1f5-41a2118b233a` y la huella semántica
`72d5cef99921795738b707ff02009364110fb1bbdc59d16c4ef7131cc9eb93c0`.

Como antecedente, el despliegue de P2 se protegió con la copia fría
`agendasalon-20260717T150928Z`, conservada fuera de la retención automática, y
el snapshot `pre-agendasalon-p2-experiencia-2026-07-17-1512Z`, ID `237312606`,
acción `3296249201`, creado el 17 de julio de 2026 a las 15:12:36 UTC con el
Droplet apagado. Debe conservarse al menos hasta el
`2026-07-21T15:12:36Z`. La copia posterior autenticada, verificada y validada
con `pg_restore --list` es `agendasalon-20260717T153403Z`.

Como antecedente del despliegue P2, se aplicaron exclusivamente
`holidays.0005_holidayappointmentreview` y
`customers.0015_businessclientaccess_public_registration_expires_at`. La
primera purga controlada no encontró candidatas y la limpieza retiró cinco
sesiones caducadas. La comprobación final conservó exactamente 2 negocios, 3
usuarios, 8 clientes, 4 accesos y 23 citas; quedaron 2 sesiones activas y 0
caducadas, outbox vacío, 0 solicitudes de alta, 0 altas públicas pendientes y
0 revisiones de citas en festivo. Gunicorn, Nginx, PostgreSQL y los cinco
temporizadores operativos quedaron activos y habilitados, sin unidades fallidas
ni errores nuevos en el diario. La aceptación pública se limitó a GET y
consultas de solo lectura, sin crear datos ni dejar residuo.

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

## Regeneración controlada de la demo académica

`refresh_demo` no es un comando de mantenimiento general ni debe ejecutarse
contra datos reales. El proceso solo continúa si coinciden todas estas barreras:

- perfil `config.settings.prod`, `DEBUG=False`, PostgreSQL y modo legal de demo;
- confirmación explícita `--confirm-full-reset`;
- base, usuario, host, puerto, web, directorio de medios y marcador de
  quiescencia iguales a los valores esperados del servidor;
- conjunto de tablas exactamente conocido, migraciones al día y ninguna otra
  conexión cliente a la base;
- `AGENDA_DEMO_REFRESH_ENABLED=1`, correo transaccional desactivado y
  `AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL=1`;
- catálogo BOE íntegro para todos los años que atraviesa la ventana temporal.

La unidad root `agendasalon-demo-refresh.service` ejecuta el orquestador
versionado `ops/run_demo_refresh.sh`. Este verifica la última copia canónica
existente, detiene Gunicorn y los temporizadores capaces de escribir, mueve los
medios a una cuarentena reversible y baja privilegios antes de llamar a Django.
La limpieza y la siembra se realizan dentro de una transacción con bloqueos
PostgreSQL. Tras confirmar el recibo y el escenario, crea y verifica una nueva
copia canónica limpia. El correo usa un backend nulo durante todo el proceso. Si
base de datos y medios no quedan reconciliados, la aplicación no se reabre como
si el refresco hubiera terminado bien.

Se eliminan los datos mutables de producto y cualquier cuenta interna ajena a
las tres identidades canónicas. Se conservan sus filas de usuario, los documentos
legales publicados, la foto BOE válida, el historial de copias y los recibos de
regeneración. El postflight exige además outbox, solicitudes, sesiones, grupos,
medios personalizados y otros residuos de evaluación a cero antes de aceptar la
huella del escenario.

El temporizador `agendasalon-demo-refresh.timer` está definido para las
`04:05 Europe/Madrid`, con `Persistent=false`, `AccuracySec=1min` y sin retraso
aleatorio. Quedó habilitado y activo el 18 de julio de 2026 a las 04:06, una vez
pasada la ventana de hoy, y systemd fijó la siguiente ejecución para el 19 de
julio a las 04:05. Hasta observar su resultado real en systemd y en el recibo
de PostgreSQL, solo consta una aceptación manual; no una ejecución automática.

La comprobación de estado se realiza sin lanzar el refresco:

```bash
systemctl is-enabled agendasalon-demo-refresh.timer
systemctl list-timers --all agendasalon-demo-refresh.timer
systemctl show agendasalon-demo-refresh.service -p Result -p ExecMainStatus
journalctl -u agendasalon-demo-refresh.service -n 100 --no-pager
```

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

Estado histórico de referencia del despliegue inicial de correo:

- commit de aplicación: `ed509e2e59fa1721ef9abf3951951cc8bf999547`;
- migraciones aplicadas: `accounts.0005`, `customers.0010` y
  `notifications.0002`;
- `agendasalon-email.timer`: habilitado y activo;
- comando de outbox verificado: 0 procesados, 0 enviados y 0 pendientes o
  fallidos;
- accesos sin verificar ligados a fichas de origen `other`: 0 en aquella
  fotografía inicial;
- cuentas y citas demo preservadas sin ejecutar de nuevo `seed_demo`.

Las migraciones y el SHA actuales de producción se comprueban siempre en el
preflight del despliegue correspondiente; esta referencia histórica no sustituye
esa comprobación.

### Caducidad de altas públicas pendientes

Las altas públicas que todavía no han verificado el correo caducan lógicamente
a las 48 horas de su creación o del último enlace realmente encolado que renueve
el plazo. Desde ese instante el token y el correo pendiente dejan de ser válidos.
No significa que nombre, teléfono y
grafo asociado se borren físicamente en ese mismo segundo: la eliminación se
intenta en una pasada posterior del temporizador y solo cuando puede hacerse sin
dañar actividad legítima. Un correo que permanece con un `lease` de envío activo
no renueva el plazo. Las cuentas verificadas, las fichas profesionales y
cualquier identidad con citas, evidencias o actividad ajena al propio registro
quedan fuera de esta purga automática y requieren conservación o revisión según
su finalidad.

La pantalla pendiente y sus acciones mantienen la misma respuesta visible para
un correo nuevo, pendiente o ya existente. El registro no precarga ni modifica
datos de una identidad previa. La corrección de nombre y teléfono solo aparece
al abrir un token de verificación válido, cuando ya se ha demostrado la posesión
del correo; esa dirección se muestra como identidad inmutable.

Si una reserva de envío `PROCESSING` ya ha caducado, la primera pasada la marca
como cancelada y limpia su `lease`, pero conserva el alta durante esa ejecución.
La siguiente pasada puede eliminarla. Así, un trabajador rezagado puede
comprobar que ya no posee la reserva antes de que desaparezca el grafo asociado.
Por tanto, las 48 horas son el límite de validez lógica, no un máximo físico de
conservación: el intervalo del temporizador, un envío activo, la doble pasada de
un `lease` caducado o una excepción de seguridad pueden retrasar o impedir la
eliminación automática.

#### Precondición de migración y backfill

La producción P1 aceptada ya tiene aplicadas `customers.0014` y
`holidays.0004`. P2 añade `customers.0015`, además de `holidays.0005`. El
preflight se ejecuta todavía sobre el árbol P1: debe demostrar en el servidor,
sin apoyarse solo en la documentación, que ambas migraciones P1 están aplicadas
y que `migrate --plan` no propone ninguna pendiente. El árbol P1 aún no puede
conocer los ficheros de migración de P2.

Todos los comandos manuales de esta ventana se ejecutan desde
`/var/www/agendasalon/app`, con el archivo protegido cargado sin imprimirlo y
con el perfil de producción indicado también en cada llamada:

```bash
set -a
. /etc/agendasalon/agendasalon.env
set +a
export DJANGO_SETTINGS_MODULE=config.settings.prod
python manage.py shell --settings=config.settings.prod -c "from django.conf import settings; from django.db import connection; print({'settings': 'config.settings.prod', 'debug': settings.DEBUG, 'database_vendor': connection.vendor})"
```

La salida aceptable es `debug=False` y `database_vendor='postgresql'`. El plan
se comprueba antes y después de detener los escritores:

```bash
python manage.py showmigrations customers holidays booking businesses legal notifications --plan --settings=config.settings.prod
python manage.py migrate --plan --settings=config.settings.prod
```

En este primer plan, todavía con P1 instalado, no debe aparecer ninguna
migración pendiente. Tras instalar el SHA exacto de P2 dentro de la ventana
fría se repite el plan: solo entonces deben aparecer pendientes exclusivamente
`customers.0015_businessclientaccess_public_registration_expires_at` y
`holidays.0005_holidayappointmentreview`. Cualquier otra diferencia bloquea el
despliegue.

Antes de detener la ventana se comprueban también estos dos recuentos:

```bash
python manage.py shell --settings=config.settings.prod -c "from apps.customers.models import BusinessClientAccess; print({'pending_public': BusinessClientAccess.objects.filter(is_pending_public_registration=True, email_verified_at__isnull=True).count(), 'legacy_other': BusinessClientAccess.objects.filter(email_verified_at__isnull=True, business_client__source='other').count()})"
```

Ambos deben ser cero en la fotografía canónica P1. Si alguno no es cero, el
despliegue se detiene para inventariar esas filas; no se aplica el backfill ni se
ejecuta una purga automática sobre datos históricos sin revisión humana.

La fotografía previa, sin nombres, teléfonos, correos ni tokens, incluye
negocios, usuarios, clientes, accesos, citas, solicitudes de alta, outbox total y
`PROCESSING`, sesiones activas y caducadas, altas pendientes y filas legacy. Si
una alta pendiente existe, el inventario muestra únicamente sus identificadores
internos, negocio, ficha, `created_at`, actividad y estados para decidirla antes
de continuar.

El orden controlado es:

1. verificar SHA, CI, estado limpio, migraciones y recuentos P1;
2. detener Gunicorn, el temporizador de correo y cualquier otro escritor;
3. crear y verificar copia fría y snapshot;
4. repetir los recuentos con los escritores detenidos;
5. instalar el SHA exacto de P2 y verificar que el nuevo plan contiene
   únicamente `customers.0015` y `holidays.0005`;
6. aplicar únicamente `customers.0015` y `holidays.0005`;
7. comprobar `migrate --check`, `check --deploy` y que no quedan migraciones;
8. ejecutar la simulación de purga, su primera pasada real controlada y la
   limpieza de sesiones antes de habilitar los nuevos temporizadores;
9. rearmar escritores y completar la aceptación funcional, de datos y servicios.

La ventana fría no se deja a interpretación. Primero se impiden nuevas
ejecuciones y se espera a que cualquier `oneshot` ya iniciado termine; no se
interrumpe una copia o un envío a mitad:

```bash
systemctl disable --now agendasalon-email.timer backup-agendasalon.timer check-agendasalon-backup.timer
for service in agendasalon-email.service backup-agendasalon.service check-agendasalon-backup.service; do
  while systemctl is-active --quiet "$service"; do sleep 2; done
done
systemctl disable --now gunicorn-agendasalon.service
test "$(systemctl is-active gunicorn-agendasalon.service)" = "inactive"
for unit in gunicorn-agendasalon.service agendasalon-email.timer backup-agendasalon.timer check-agendasalon-backup.timer; do
  test "$(systemctl is-enabled "$unit")" = "disabled"
done
```

Con los escritores detenidos se repiten los recuentos, se ejecuta y verifica la
copia fría y se crea el snapshot. Antes de apagar puede lanzarse la copia manual
con `systemctl start backup-agendasalon.service`; su `Result` debe ser `success`
y su `ExecMainStatus`, `0`. Tras volver a encender el droplet se comprueba otra
vez que Gunicorn y los tres temporizadores siguen deshabilitados e inactivos.
Solo entonces se instala el SHA exacto de P2. Ya con sus ficheros de migración
presentes, el primer plan debe mostrar exclusivamente las dos migraciones P2;
si aparece cualquier otra, no se ejecuta ninguna:

```bash
python manage.py migrate --plan --settings=config.settings.prod
python manage.py migrate holidays 0005 --noinput --settings=config.settings.prod
python manage.py migrate customers 0015 --noinput --settings=config.settings.prod
python manage.py migrate --check --settings=config.settings.prod
python manage.py check --deploy --settings=config.settings.prod
python manage.py migrate --plan --settings=config.settings.prod
```

Se crea primero la tabla aditiva y vacía de revisiones de festivos; el backfill
de caducidades se ejecuta después. El último plan debe quedar vacío. No se
arranca el código P2 mientras una de las dos migraciones siga pendiente.

`customers.0015` añade el campo de caducidad y contiene un backfill defensivo
para otras instalaciones: si encontrara un alta que `0013` hubiese marcado como
pendiente, fijaría su caducidad en `created_at + 48 horas`. En la línea P1
aceptada ese conjunto es cero, por lo que el backfill no debe reescribir altas.

La comprobación manual sin borrado es:

```bash
python manage.py purge_expired_public_registrations --dry-run --batch-size 200 --settings=config.settings.prod
```

`--batch-size` limita acciones útiles por negocio: eliminaciones efectivas —o
purgables en simulación— y cancelaciones de reservas `PROCESSING` caducadas. Las
altas que se conservan por actividad, evidencias, envío activo u otra protección
se examinan y se contabilizan como omitidas, pero no consumen el lote. El
recorrido continúa por clave primaria, de modo que un prefijo de registros
protegidos no puede dejar indefinidamente sin revisar una alta purgable posterior.

La ejecución real omite `--dry-run`. Si la simulación informa de una sola
candidata en la fotografía canónica P1, se detiene el despliegue y se revisa. El
comando devuelve éxito aunque encuentre candidatas, por lo que su salida es una
puerta de aceptación, no un mensaje informativo que pueda ignorarse.

Las unidades versionadas `ops/systemd/agendasalon-registration-purge.service` y
`ops/systemd/agendasalon-registration-purge.timer` fijan expresamente el perfil
`config.settings.prod` y programan la purga cada quince minutos. La instalación
controlada es:

```bash
install -o root -g root -m 0644 ops/systemd/agendasalon-registration-purge.service /etc/systemd/system/
install -o root -g root -m 0644 ops/systemd/agendasalon-registration-purge.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/agendasalon-registration-purge.service /etc/systemd/system/agendasalon-registration-purge.timer
systemctl daemon-reload
systemctl disable --now agendasalon-registration-purge.timer
test "$(systemctl is-active agendasalon-registration-purge.timer)" = "inactive"
test "$(systemctl is-enabled agendasalon-registration-purge.timer)" = "disabled"
systemctl start agendasalon-registration-purge.service
systemctl show agendasalon-registration-purge.service -p Result -p ExecMainStatus
test "$(systemctl show agendasalon-registration-purge.service -p Result --value)" = "success"
test "$(systemctl show agendasalon-registration-purge.service -p ExecMainStatus --value)" = "0"
systemctl enable agendasalon-registration-purge.timer
systemctl start agendasalon-registration-purge.timer
```

La primera ejecución del servicio se hace con el temporizador aún deshabilitado.
Solo si termina con `Result=success`, `ExecMainStatus=0` y cero datos inesperados
se habilita el calendario. Un servicio `oneshot` queda normalmente `inactive`
entre pasadas; eso no es un fallo.

### Limpieza de sesiones caducadas

La sesión pendiente no conserva nombre ni teléfono, pero sí el correo enviado
por la propia persona para poder mostrar una respuesta genérica. Django no
elimina automáticamente de la tabla `django_session` las filas que ya han
caducado. Por eso esta limpieza se ejecuta de forma independiente:

```bash
python manage.py clearsessions --settings=config.settings.prod
```

Las unidades `ops/systemd/agendasalon-session-cleanup.service` y
`ops/systemd/agendasalon-session-cleanup.timer` fijan también el perfil de
producción y programan el comando a las 00:20, 06:20, 12:20 y 18:20, con
`Persistent=true`. No se acopla a la purga de altas pendientes: un fallo en una
tarea no debe bloquear la otra. Se instalan y verifican con el mismo patrón:

```bash
install -o root -g root -m 0644 ops/systemd/agendasalon-session-cleanup.service /etc/systemd/system/
install -o root -g root -m 0644 ops/systemd/agendasalon-session-cleanup.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/agendasalon-session-cleanup.service /etc/systemd/system/agendasalon-session-cleanup.timer
systemctl daemon-reload
systemctl disable --now agendasalon-session-cleanup.timer
test "$(systemctl is-active agendasalon-session-cleanup.timer)" = "inactive"
test "$(systemctl is-enabled agendasalon-session-cleanup.timer)" = "disabled"
systemctl start agendasalon-session-cleanup.service
systemctl show agendasalon-session-cleanup.service -p Result -p ExecMainStatus
test "$(systemctl show agendasalon-session-cleanup.service -p Result --value)" = "success"
test "$(systemctl show agendasalon-session-cleanup.service -p ExecMainStatus --value)" = "0"
systemctl enable agendasalon-session-cleanup.timer
systemctl start agendasalon-session-cleanup.timer
systemctl list-timers --all agendasalon-registration-purge.timer agendasalon-session-cleanup.timer
```

### Aceptación operativa de P2

Antes de abrir tráfico se repite la fotografía sin PII. Deben conservarse los
recuentos canónicos de negocios, usuarios, clientes, accesos, citas y solicitudes;
no puede haber altas pendientes sin caducidad, caducidades en accesos que ya no
estén pendientes, correos `PROCESSING` con un `lease` incoherente ni revisiones de
festivos recién creadas. Las sesiones activas se comparan por separado; las
caducadas son estado efímero y `clearsessions` debe retirarlas.

```bash
python manage.py shell --settings=config.settings.prod <<'PY'
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.utils import timezone
from apps.booking.models import Appointment
from apps.businesses.models import Business, BusinessSignupRequest
from apps.customers.models import BusinessClient, BusinessClientAccess
from apps.holidays.models import HolidayAppointmentReview
from apps.notifications.models import OutboundEmail

now = timezone.now()
print({
    "businesses": Business.objects.count(),
    "users": get_user_model().objects.count(),
    "clients": BusinessClient.objects.count(),
    "accesses": BusinessClientAccess.objects.count(),
    "appointments": Appointment.objects.count(),
    "signup_requests": BusinessSignupRequest.objects.count(),
    "outbox_total": OutboundEmail.objects.count(),
    "outbox_processing": OutboundEmail.objects.filter(status=OutboundEmail.Status.PROCESSING).count(),
    "active_leases": OutboundEmail.objects.filter(status=OutboundEmail.Status.PROCESSING, lease_expires_at__gt=now).count(),
    "sessions_active": Session.objects.filter(expire_date__gt=now).count(),
    "sessions_expired": Session.objects.filter(expire_date__lte=now).count(),
    "pending_without_expiry": BusinessClientAccess.objects.filter(is_pending_public_registration=True, email_verified_at__isnull=True, public_registration_expires_at__isnull=True).count(),
    "expiry_outside_pending": BusinessClientAccess.objects.filter(is_pending_public_registration=False, public_registration_expires_at__isnull=False).count(),
    "holiday_reviews": HolidayAppointmentReview.objects.count(),
})
PY
```

Justo antes de abrir tráfico, `pending_without_expiry`,
`expiry_outside_pending`, `holiday_reviews`, `outbox_processing` y
`active_leases` deben ser cero. El resto se contrasta con la fotografía previa;
en la demo canónica P1 eran 2 negocios, 3 usuarios, 8 clientes, 4 accesos, 23
citas y 0 solicitudes de alta y correos.

Después se rearman los servicios previos y se comprueban todos, incluidos los
nuevos:

```bash
systemctl enable gunicorn-agendasalon.service agendasalon-email.timer backup-agendasalon.timer check-agendasalon-backup.timer
systemctl start gunicorn-agendasalon.service agendasalon-email.timer backup-agendasalon.timer check-agendasalon-backup.timer
systemctl is-enabled gunicorn-agendasalon.service agendasalon-email.timer backup-agendasalon.timer check-agendasalon-backup.timer agendasalon-registration-purge.timer agendasalon-session-cleanup.timer
systemctl is-active gunicorn-agendasalon.service agendasalon-email.timer backup-agendasalon.timer check-agendasalon-backup.timer agendasalon-registration-purge.timer agendasalon-session-cleanup.timer
systemctl list-timers --all agendasalon-email.timer backup-agendasalon.timer check-agendasalon-backup.timer agendasalon-registration-purge.timer agendasalon-session-cleanup.timer
systemctl show agendasalon-registration-purge.service agendasalon-session-cleanup.service -p Result -p ExecMainStatus
systemctl --failed
journalctl -u gunicorn-agendasalon.service -u agendasalon-registration-purge.service -u agendasalon-session-cleanup.service --since "<inicio-despliegue>" --no-pager
```

La aceptación HTTP es de solo lectura: redirección de HTTP a HTTPS, respuesta
correcta de `/`, `/entrar/`, `/solicitar-alta/`, reserva pública y documentos
legales; la privacidad del negocio debe devolver `Cache-Control: no-store`.
También se comprueban el socket interno, Nginx y PostgreSQL. Por último se crea y
verifica una copia posterior autenticada y se confirma de nuevo el SHA exacto y
el árbol limpio.

```bash
curl -fsSI http://agendasalon.brvsoftwarestudio.com/
for path in / /entrar/ /solicitar-alta/ /reservar/peluqueria-mari/ /legal/ /legal/negocios/peluqueria-mari/privacidad/; do
  curl -fsS -o /dev/null -w "${path} %{http_code}\n" "https://agendasalon.brvsoftwarestudio.com${path}"
done
curl -fsS -D - -o /dev/null "https://agendasalon.brvsoftwarestudio.com/legal/negocios/peluqueria-mari/privacidad/"
test -S /run/agendasalon/gunicorn.sock
nginx -t
pg_isready
git rev-parse HEAD
git status --short
```

La primera respuesta debe redirigir con `301`; las rutas HTTPS deben responder
con `200` o con la redirección de acceso prevista para `/`, y la última ruta
legal debe incluir `Cache-Control: no-store`. Se usa GET real porque
`/solicitar-alta/` admite GET y POST, pero rechaza HEAD con `405`; ese `405` no
es un fallo de la pantalla. `git status --short` no debe devolver ninguna línea.

### Rollback de P2

Si cualquiera de los servicios o temporizadores falla, se detiene y deshabilita
antes de reabrir tráfico. Desde el momento en que se aplica una migración P2, el
procedimiento estándar de rollback es restaurar la protección previa completa;
no se bajan migraciones de forma automática. Tras abrir tráfico pueden existir
caducidades renovadas o revisiones profesionales que el reverso perdería, aunque
la purga no haya eliminado ninguna fila.

El rollback seguro exige detener y deshabilitar todos los escritores y
temporizadores, restaurar conjuntamente PostgreSQL y medios desde la copia fría
o el snapshot, volver al SHA P1 compatible y repetir la aceptación completa
antes de reabrir. Nunca se cruza hacia atrás `customers.0014`.

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

python -m ops.backup_restore verify \
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

La raíz `/var/backups/agendasalon` contiene solo copias ordinarias gestionadas
por esa retención. Los hitos que deban sobrevivir a la selección 7/4/6 se
guardan en `/var/backups/agendasalon-protected`, fuera del patrón gestionado,
con directorios `root:root` `0700` y archivos `root:root` `0600`. No se eliminan
automáticamente: cada uno requiere verificación y autorización expresa.

Esta separación se fijó después de una incidencia controlada durante P2. Una
copia nueva se creó y verificó, pero `ExecStartPost` no pudo retirar un hito P1
propiedad de `root` que todavía estaba dentro de la raíz ordinaria. No se borró
ninguna copia ni se afectó a la aplicación. Los hitos se verificaron, se movieron
a la raíz protegida y la unidad se repitió con `Result=success` y
`ExecMainStatus=0`. La regla operativa es no mezclar copias protegidas con la
raíz sobre la que el usuario `agendasalon` aplica retención.

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

python -m ops.backup_restore restore \
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

## Migraciones aditivas aplicadas en P2

El despliegue de P2 aplicó únicamente estas operaciones y en este orden:

1. `holidays.0005_holidayappointmentreview`;
2. `customers.0015_businessclientaccess_public_registration_expires_at`.

La primera crea la tabla vacía de confirmaciones profesionales sobre citas en
festivo. La segunda añade la caducidad de altas públicas y su backfill defensivo;
en la demo canónica no encontró altas pendientes que reescribir. Antes de abrir
tráfico, `migrate --check` y el plan quedaron vacíos, la purga informó 0
candidatas y los recuentos de negocio permanecieron invariantes. Los nuevos
temporizadores de purga y limpieza de sesiones se instalaron, verificaron y
ejecutaron manualmente antes de habilitarse.

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
