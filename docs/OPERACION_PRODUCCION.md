# Operación segura de AgendaSalon

Este documento define el contrato técnico y las operaciones del despliegue. La
demo académica quedó publicada el 14 de julio de 2026 en
`https://agendasalon.brvsoftwarestudio.com`; los comandos siguen requiriendo una
ejecución deliberada y no se activan por leer este documento.

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
