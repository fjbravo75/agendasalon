# AgendaSalon

[![CI](https://github.com/fjbravo75/agendasalon/actions/workflows/ci.yml/badge.svg)](https://github.com/fjbravo75/agendasalon/actions/workflows/ci.yml)

AgendaSalon es el entregable técnico del Proyecto Fin de Máster: un SaaS con
Django como núcleo para peluquerías, barberías y pequeños salones de belleza.

El MVP se centra en un motor único de citas optimizadas. El profesional puede
crear citas desde llamadas, WhatsApp o mostrador. En la reserva online, el
visitante explora servicios y huecos sin cuenta; solo entra o se registra al
revisar y confirmar la hora elegida. Ambos canales usan la misma lógica de
disponibilidad, puntuación y revalidación.

## Stack

- Python 3.12
- Django 5.2 LTS
- Node.js 20.19 o 22.12 en adelante para compilar el frontend
- SQLite en desarrollo local
- PostgreSQL obligatorio en producción
- Plantillas Django y CSS para la mayor parte del producto
- React 19 y Vite 8 para dos islas acotadas: agenda profesional y cuadro de
  mando del superadministrador
- Pillow 12 para validar y procesar las imágenes públicas subidas por los negocios
- Argon2id para el hashing preferente de contraseñas

## Evidencias destacadas

- [Benchmark reproducible del motor de huecos](docs/evidence/slot-engine/README.md): en cuatro escenarios deterministas, mantiene 31 solicitudes aceptadas y reduce de 120 a 0 los minutos atrapados en restos menores de 30 minutos.
- [Índice de evidencias para evaluación](docs/EVIDENCIAS_CANDIDATA_10.md): seguridad, escalabilidad, límites y pruebas que todavía necesitan despliegue o participantes reales.
- [Protocolo de validación con profesionales](docs/validation-professionals/README.md): preparado, pero expresamente marcado como pendiente hasta realizar sesiones reales.

## Puesta en marcha local

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm.cmd install
npm.cmd run build
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py runserver
```

La aplicación queda disponible en `http://127.0.0.1:8000/`. El proyecto usa
por defecto `config.settings.dev` para desarrollo.

## Accesos de demostración

La semilla local utiliza la contraseña `DemoAgendaSalon2026!` para estas
cuentas:

| Perfil | Teléfono |
| --- | --- |
| Superadministrador | `+34910000001` |
| Profesional de Peluquería Mari | `+34600111001` |
| Profesional de Barbería Norte | `+34600222001` |
| Cliente de Peluquería Mari | `600111201` |
| Cliente de Barbería Norte | `600222201` |

Son credenciales exclusivamente demostrativas y no deben reutilizarse en un
despliegue real.

## Estado actual

La demostración académica está publicada en
[`https://agendasalon.brvsoftwarestudio.com`](https://agendasalon.brvsoftwarestudio.com).
Funciona con `DEBUG=False`, PostgreSQL, Nginx, Gunicorn por socket interno y HTTPS
Let's Encrypt. El entorno muestra de forma explícita que no existe actividad
comercial y utiliza `agendasalon@brvsoftwarestudio.com` como contacto real.

Base Django creada con configuración separada por entorno, usuario personalizado
interno desde el inicio, núcleo de modelos SaaS/agenda y entrada autenticada por
teléfono normalizado.

Incluye negocios, pertenencias profesionales, servicios, disponibilidad, cierres,
líneas de trabajo, fichas de cliente, contactos autorizados, citas,
accesos cliente, servicios dentro de cita, festivos y notificaciones internas
simuladas.

También incluye acceso visual, plantillas base, redirección por rol y resolución
del negocio activo del profesional autenticado.

La entrada profesional `/profesional/` funciona como agenda operativa de la
jornada: muestra datos que entiende el profesional, líneas de trabajo, huecos
recomendados, estado del salón y un vacío accionable cuando no hay citas.
Las citas cuya hora ya terminó aparecen como pendientes de cierre: el
profesional puede registrar si fueron atendidas o si el cliente no se presentó,
también mediante una selección múltiple. El paso del tiempo nunca marca una
cita automáticamente como atendida.

El motor de citas por duración total ya existe como servicio de dominio en
`apps/booking/slot_engine.py`. Calcula disponibilidad diaria por líneas, días de
mes con hueco real para una duración concreta, sugerencias cuando el día elegido
no tiene capacidad suficiente y puntuación inicial para recomendar huecos que
compactan la agenda.

La confirmación de citas pasa por `apps/booking/services.py`, que revalida el
hueco justo antes de crear `Appointment` y `AppointmentService`.

La primera capa JSON para la agenda React profesional está disponible mediante
dos endpoints de solo lectura. Ambos exigen sesión profesional, resuelven el
negocio desde la pertenencia activa del usuario y no aceptan un identificador de
negocio enviado por el navegador. El endpoint diario reúne citas, líneas,
cierres, festivos, huecos válidos, recomendación, sugerencias y corte temporal;
el endpoint mensual expone la disponibilidad real para una duración concreta.
La creación y el cierre de citas continúan en los POST protegidos de Django.

La primera isla React está integrada en `/profesional/agenda/`. Permite cambiar
la duración, navegar por meses, seleccionar días, leer la jornada por líneas,
ver citas con altura proporcional, distinguir cierres y festivos, elegir un
hueco real y continuar en `Nueva cita` conservando la línea y la hora. En móvil
las líneas se consultan por segmentos para evitar una parrilla comprimida.

La segunda isla React está integrada en `/superadmin/dashboard/` sobre un
endpoint global de solo lectura reservado al superadministrador. Resume salud y
configuración por negocio, citas que deben cerrar los equipos profesionales,
reserva online, actividad de catorce días, estados y canales. Permite buscar y
filtrar negocios sin dar acceso al flujo de reserva ni mover mutaciones fuera de
los formularios Django protegidos. La actividad global no expone nombres de
clientes ni datos de contacto.

Cada negocio dispone de `/profesional/ajustes/`. Desde esa pantalla el equipo
puede elegir modo claro u oscuro para todo su panel profesional y subir una
imagen JPG, PNG o WebP para personalizar la reserva online, el acceso cliente y
el registro cliente. La reserva pública mantiene una preferencia independiente:
adapta automáticamente su luminosidad al modo claro u oscuro del dispositivo
del cliente. La imagen se valida, se orienta y se recodifica como WebP
sin EXIF ni metadatos; el lado mayor queda limitado a 2400 px. La entrada no
puede superar 5 MB ni 16 millones de píxeles y la compresión utiliza un perfil
WebP equilibrado para no ocupar de forma desproporcionada los procesos web. Si
se retira, AgendaSalon recupera automáticamente la imagen estándar de salón o
barbería. El fondo estándar de salón se sirve como WebP optimizado para reducir
peso y mantener estable la revisión visual. El acceso interno de profesionales
mantiene su imagen propia.

El superadministrador dispone de `/superadmin/ajustes/`. Su tema claro u oscuro
se aplica al dashboard, la gestión de negocios y la propia configuración, sin
alterar el modo elegido por cada salón. También puede seleccionar una de las
tres imágenes internas estándar o subir imágenes propias saneadas para el
acceso común de profesionales y superadministradores. Estas imágenes no se
utilizan en la reserva ni en las cuentas de clientes.

Al cerrar una sesión interna, la confirmación conserva el modo claro u oscuro
que estuviera activo. Después del cierre solo permanece esa preferencia visual;
no se conserva identidad, negocio, rol ni permisos.

La pantalla Django del flujo profesional está disponible en
`/profesional/citas/nueva/`. Permite seleccionar cliente, canal, varios
servicios y día; calcula la duración total; muestra calendario mensual,
disponibilidad por líneas, hueco recomendado y sugerencias. La confirmación
final se resuelve mediante POST protegido y revalidación del hueco. Los campos
obligatorios usan asterisco y leyenda conjunta; la búsqueda parcial conserva
canal y día por defecto sin mostrar avisos rojos redundantes. La indicación de
servicios permanece visible porque desbloquea el cálculo de disponibilidad.

El catálogo profesional `/profesional/servicios/` contiene la lista dentro de
su propio panel: muestra cinco servicios completos y, solo desde el sexto,
activa desplazamiento vertical interno. La altura se adapta a las filas reales
para no cortar contenido en escritorio ni móvil. La regla se aplica a todos los
negocios sin alterar la edición, la pausa o la activación de servicios.

El acceso cliente final está disponible en `/clientes/<slug>/entrar/`, con alta
separada en `/clientes/<slug>/registro/`.

El registro público solo crea fichas nuevas. Si el teléfono ya pertenece a una
ficha del negocio, AgendaSalon no la vincula ni revela su identidad. El
profesional puede crear desde esa ficha una invitación privada que caduca en 24
horas, solo funciona una vez y activa exactamente el registro seleccionado. El
token no se guarda en claro.

Las contraseñas nuevas usan Argon2id y los hashes PBKDF2 anteriores se actualizan
después de un acceso correcto. Los intentos de acceso se limitan por identidad e
IP sin guardar esos identificadores en claro. La sesión cliente rota al entrar y
salir, y caduca tras una hora sin actividad.

AgendaSalon incorpora una capa de privacidad operativa, no solo informativa.
Los documentos legales se publican por versión y huella; cada negocio completa
la identidad del responsable y acepta el encargo de tratamiento antes de poder
recoger nuevos datos. El registro y las invitaciones de clientes muestran la
privacidad del negocio con una casilla no premarcada. Las cuentas cliente pueden
registrar solicitudes de derechos y el negocio documenta su seguimiento desde
`/legal/profesional/`. Las altas rápidas realizadas por un profesional desde
Clientes o Nueva cita exigen indicar el canal utilizado y confirmar que se ha
facilitado la información; la evidencia conserva documento, versión, huella,
fecha, actor y persona informada. El superadministrador puede consultar por
negocio el estado vigente y el historial de aceptaciones sin mezclar la
aceptación contractual del salón con la información recibida por sus clientes.

La reserva online está disponible en `/reservar/<slug>/`. Permite al cliente
elegir servicios, ver duración, precio y opciones recomendadas sin sesión. Al
elegir una hora guarda un borrador temporal, solicita acceso cliente y recupera
una revisión final antes de confirmar. La cita solo se crea mediante POST
protegido, tras revalidar el hueco, y queda vinculada a su ficha de cliente.

El superadministrador dispone de un panel de estado y de una gestión propia de
negocios. Puede dar de alta un salón con su primer acceso profesional, editarlo,
pausarlo o reactivarlo, gestionar profesionales y activar o detener la reserva
online sin borrar el historial. Esta administración no entra en el recorrido de
reserva del cliente.

La ficha de cada negocio incorpora un historial de actividad de solo lectura.
Registra cambios reales de estado: citas creadas, canceladas o cerradas;
servicios, horarios, cierres y líneas modificados; y cambios de negocio,
reserva pública o accesos profesionales. Cada movimiento conserva fecha,
categoría, responsable y canal, sin guardar contraseñas ni datos personales
innecesarios. La ficha resume seis movimientos y el historial completo admite
filtros y paginación numerada de diez en diez.

El listado profesional de clientes presenta seis fichas por página y conserva
búsqueda y estado al navegar. En el dashboard global, la actividad reciente
mantiene seis movimientos visibles y desplaza el resto dentro del mismo panel.

También existe una semilla de demostración reproducible:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo
```

Si no se indica fecha, sitúa la demostración en el lunes operativo actual o
siguiente. Crea `Peluquería Mari` y `Barbería Norte` con servicios, horarios,
clientes, accesos y citas de distintos estados; añade cierres, un festivo de
demostración y un día sin hueco para una cita de 180 minutos. Cada ejecución
reinicia las citas y notificaciones de ambos negocios demo para evitar datos
caducados o acumulados. Para una fecha reproducible puede usarse
`seed_demo --base-date 2026-07-13`.

## Rutas principales

- `/`: redirección al acceso interno, sin directorio público de negocios.
- `/cuenta/entrar/`: acceso de profesionales y superadministración.
- `/cuenta/desconectado/`: confirmación de cierre de la sesión interna.
- `/profesional/`: agenda operativa de la jornada.
- `/profesional/agenda/`: agenda profesional interactiva.
- `/profesional/agenda/datos/`: datos JSON protegidos de una jornada.
- `/profesional/agenda/mes/`: disponibilidad mensual JSON protegida.
- `/profesional/citas/nueva/`: asistente de nueva cita.
- `/profesional/citas/pendientes/`: revisión completa de citas pendientes de cierre.
- `/profesional/servicios/`: catálogo profesional.
- `/profesional/horarios/`: disponibilidad, cierres, líneas y aplicación del
  calendario nacional sincronizado.
- `/profesional/ajustes/`: modo del panel e imagen pública del negocio.
- `/clientes/profesional/`: fichas de cliente.
- `/superadmin/dashboard/`: estado general de AgendaSalon.
- `/superadmin/dashboard/datos/`: datos JSON protegidos del cuadro de mando.
- `/superadmin/negocios/`: alta y gestión de negocios y accesos profesionales.
- `/superadmin/ajustes/`: tema de administración e imagen del acceso interno.
- `/superadmin/negocios/<id>/actividad/`: historial filtrable de un negocio.
- `/reservar/<slug>/`: reserva online híbrida.
- `/clientes/<slug>/entrar/`: acceso cliente por negocio.
- `/clientes/<slug>/registro/`: alta cliente por negocio.
- `/clientes/<slug>/activar/`: activación limpia tras validar una invitación.
- `/legal/`: documentación legal vigente de la plataforma.
- `/legal/negocios/<slug>/privacidad/`: privacidad y ejercicio de derechos ante
  el negocio responsable.
- `/legal/profesional/alta/`: identidad legal y aceptación inicial del negocio.
- `/legal/profesional/`: evidencias y seguimiento de solicitudes de clientes.

Cada cliente llega mediante la URL del negocio concreto. Por ejemplo, la
demostración utiliza `/reservar/peluqueria-mari/` y
`/reservar/barberia-norte/`; AgendaSalon no muestra un selector global de
salones ni enlaza el acceso profesional desde las pantallas cliente.

Verificación actual:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\coverage.exe run manage.py test
.\.venv\Scripts\coverage.exe report
npm.cmd run check
.\.venv\Scripts\ruff.exe check .
```

La última verificación completa deja la batería en 276 pruebas Django y
operativas, además de 21 pruebas frontend: 17 unitarias y 4 de componentes
React. La cobertura con ramas es del 82 % y el umbral automatizado impide bajar
de ese valor. La matriz de CI ejecuta la batería sobre SQLite y PostgreSQL 17,
incluida la concurrencia real. Ruff, el build de producción, `pip-audit`,
`npm audit` y `pip check` finalizaron sin incidencias. GitHub Actions reproduce lint,
cobertura, SQLite, PostgreSQL, frontend, auditorías y detección de secretos en
cada `push` a `main` y en cada pull request.
También se puede ejecutar por dominios:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.booking
.\.venv\Scripts\python.exe manage.py test apps.customers
.\.venv\Scripts\python.exe manage.py test apps.accounts apps.businesses apps.dashboards apps.core apps.holidays apps.notifications
```

## Perfil de producción y continuidad

WSGI y ASGI arrancan con `config.settings.prod` y fallan si faltan secreto,
hosts o `DJANGO_DATABASE_URL`. El desarrollo local continúa usando
`config.settings.dev` y SQLite mediante `manage.py`.

La identidad legal tiene dos modos excluyentes. En una demostración académica
sin actividad comercial, `AGENDA_PLATFORM_LEGAL_DEMO=1` exige nombre visible,
correo de contacto y web, obliga a dejar vacíos NIF y domicilio y evita
mostrarlos en las páginas legales. En modo comercial, el valor debe ser `0` y
producción sigue exigiendo la identidad legal completa y real. Esta distinción
no modifica `DEBUG=False`, PostgreSQL, HTTPS, cookies seguras ni la gestión de
secretos.

Las respuestas incorporan una política CSP. Las rutas de producto solo permiten
scripts del mismo origen; Django Admin conserva una excepción inline limitada a
su propio prefijo. Las capacidades de navegador no utilizadas quedan
deshabilitadas mediante `Permissions-Policy` y los recursos propios usan CORP
`same-origin`. El perfil de producción añade `upgrade-insecure-requests`.

El panel propio `/superadmin/` es la administración funcional de AgendaSalon.
`/admin/` es una herramienta técnica interna de Django: exige una cuenta activa
con `is_staff`, aplica permisos por modelo y concede acceso total únicamente a
superusuarios. Los profesionales no pueden entrar. La semilla local reúne ambos
papeles en la cuenta demo para facilitar la evaluación, pero en producción deben
usarse cuentas técnicas personales, con privilegios mínimos y separadas de la
operativa habitual de la plataforma.

El procedimiento de PostgreSQL, copia de base de datos y media, verificación y
restauración está documentado en
[`docs/OPERACION_PRODUCCION.md`](docs/OPERACION_PRODUCCION.md). La herramienta
operativa no acepta la URL de base de datos por línea de comandos: la lee de la
variable `DJANGO_DATABASE_URL` para no exponer credenciales en la lista de
procesos. El manifiesto de cada copia se autentica además con HMAC-SHA-256 usando
`AGENDA_BACKUP_HMAC_KEY`, una clave independiente que no viaja con los artefactos.

El dashboard del superadministrador incorpora un resumen de continuidad y la
vista de solo lectura `/superadmin/continuidad/`. El registro muestra únicamente
metadatos operativos seguros: estado, fecha, alcance, integridad, destino
declarado y tamaño; nunca expone rutas, credenciales ni archivos descargables.
Las ejecuciones destinadas a alimentar ese historial deben iniciarse con:

```bash
python manage.py backup_agendasalon \
  --backup-root /var/backups/agendasalon \
  --media-root /var/www/agendasalon/shared/media \
  --destination external_encrypted
```

La demo pública conserva copias locales autenticadas y verificadas mediante una
tarea diaria de systemd. La tarea aplica la retención 7/4/6 después de cada
copia y otro temporizador comprueba diariamente que exista una copia válida de
menos de 36 horas. Una vigilancia local adicional avisa a Fran si falla la
programación, la integridad, la frescura o el espacio en disco. El destino
externo cifrado sigue pendiente y la interfaz no simula esa protección.

La matriz académica de controles, las evidencias reproducibles y los riesgos
que deben cerrarse durante el despliegue están reunidos en
[`docs/SEGURIDAD_Y_PROTECCION_DE_DATOS.md`](docs/SEGURIDAD_Y_PROTECCION_DE_DATOS.md).

La memoria técnica previa al despliegue, con capturas y diagramas basados en la
aplicación real, está disponible en
[`docs/memoria/Memoria_tecnica_AgendaSalon.docx`](docs/memoria/Memoria_tecnica_AgendaSalon.docx).

## Calendario nacional BOE

AgendaSalon mantiene un único catálogo de festivos nacionales para todos los
negocios. El superadministrador puede sincronizar un año desde `Ajustes`, y cada
negocio decide en `Horarios` si esas fechas cierran su agenda.

La misma operación está disponible por consola:

```bash
python manage.py sync_national_holidays --year 2026
```

La sincronización registra la referencia oficial, reconcilia cambios sin
duplicar fechas y avisa de citas futuras afectadas sin cancelarlas ni moverlas.
