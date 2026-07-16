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
| María López · madre que reserva para Lucas | `600111201` |
| Lucía Gómez · cliente con cuenta propia | `600111202` |
| Daniel Vega · cuidador que reserva para Rosa | `600111204` |
| Cliente de Barbería Norte | `600222201` |

Son credenciales exclusivamente demostrativas y no deben reutilizarse en un
despliegue real. Cada ejecución de `seed_demo` restaura estas credenciales y
elimina cualquier cambio de contraseña obligatorio de las cuentas internas de
demostración, para que el escenario académico siga siendo reproducible.

Los personajes, relaciones y citas comprobables de Peluquería Mari se describen
en [`docs/SUPUESTOS_USO_DEMO.md`](docs/SUPUESTOS_USO_DEMO.md). Son datos
ficticios preparados para evaluación y no corresponden a personas reales.

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
accesos cliente, servicios dentro de cita, festivos y una cola transaccional de
correo para activaciones, verificaciones y avisos de citas.

También incluye acceso visual, plantillas base, redirección por rol y resolución
del negocio activo del profesional autenticado.

La entrada profesional `/profesional/` funciona como agenda operativa de la
jornada: muestra datos que entiende el profesional, líneas de trabajo, huecos
recomendados, estado del salón y un vacío accionable cuando no hay citas.
Las citas cuya hora ya terminó aparecen como pendientes de cierre: el
profesional puede registrar si fueron atendidas o si el cliente no se presentó,
también mediante una selección múltiple. El paso del tiempo nunca marca una
cita automáticamente como atendida. Una cita confirmada no se puede cerrar como
atendida ni como no presentada mientras no haya llegado su `ends_at`; el inicio
de la cita, por sí solo, no libera ese tramo ni habilita su cierre.

El motor de citas por duración total ya existe como servicio de dominio en
`apps/booking/slot_engine.py`. Calcula disponibilidad diaria por líneas, días de
mes con hueco real para una duración concreta, sugerencias cuando el día elegido
no tiene capacidad suficiente y puntuación inicial para recomendar huecos que
compactan la agenda. El intervalo configurado por cada negocio es una invariante:
las duraciones de servicios, las sumas de servicios y los ajustes manuales deben
ser múltiplos compatibles. El cambio de intervalo se rechaza si dejaría servicios
activos incompatibles, y los datos heredados incoherentes producen un error
controlado en lugar de ofrecer un hueco imposible.

La confirmación de citas pasa por `apps/booking/services.py`, que revalida el
hueco justo antes de crear `Appointment` y `AppointmentService`.
La confirmación y las mutaciones profesionales que pueden retirar capacidad
—horarios, cierres, líneas y aplicación de festivos— comparten un orden estable
de bloqueos y se vuelven a comprobar dentro de la transacción. Así, una operación
concurrente no puede confirmar una cita a la vez que otra deja inválida su línea
o su calendario. La sincronización global BOE se trata por separado y figura
entre los endurecimientos P1.
La creación profesional continúa en la ficha de la cita recién creada. La
confirmación pública termina en un justificante ligado a la sesión, el negocio y
la cuenta cliente, recuperable durante una hora sin convertir el MVP en un panel
cliente completo.

El mapa mensual de `Nueva cita` conserva la estructura real del calendario:
semana de lunes a domingo, encabezados visibles y celdas vacías antes y después
de los días del mes. La cuadrícula mantiene siete columnas también en móvil, con
densidad adaptada y sin desplazar cada mes a una semana ficticia.

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
Las sugerencias alternativas trasladan el instante y la línea exactos del hueco
elegido; no vuelven a resolver la selección por hora ni sustituyen una línea por
otra de forma silenciosa.
La navegación móvil mantiene visibles `Resumen`, `Agenda` y `Nueva cita`; los
destinos secundarios quedan bajo `Más`, con soporte de teclado y sin scroll
horizontal.

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
peso y mantener estable la revisión visual. Los fondos estándar activos del
acceso interno y Barbería Norte también se sirven como WebP: conservan 1672 ×
941 px y pesan aproximadamente 70 y 97 KB.

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
Cuando una cuenta puede reservar para familiares o personas autorizadas, el
asistente conserva quién solicita la cita y quién recibirá el servicio. La cita
guarda instantáneas del nombre y de la relación del solicitante para que el
historial siga siendo comprensible aunque esa relación cambie después.
En `Nueva cita`, las opciones de solicitante se regeneran al cambiar de cliente.
Si se modifica cliente, canal, fecha, servicios o duración tras buscar, los
huecos anteriores se ocultan y dejan de poder confirmarse hasta repetir la
búsqueda; cambiar solo el solicitante sincroniza la confirmación sin recalcular
disponibilidad.

El catálogo profesional `/profesional/servicios/` contiene la lista dentro de
su propio panel: muestra cinco servicios completos y, solo desde el sexto,
activa desplazamiento vertical interno. La altura se adapta a las filas reales
para no cortar contenido en escritorio ni móvil. La regla se aplica a todos los
negocios sin alterar la edición, la pausa o la activación de servicios.

El acceso cliente final está disponible en `/clientes/<slug>/entrar/`, con alta
separada en `/clientes/<slug>/registro/`.

La identidad digital de una cuenta cliente es el correo normalizado, único y
verificado dentro del negocio. El teléfono se conserva como dato de contacto y
solo puede usarse como compatibilidad de acceso cuando identifica una única
cuenta verificada; ante una coincidencia ambigua, AgendaSalon no elige una
cuenta por orden interno.

El registro público crea una ficha nueva y no reclama fichas profesionales a
partir del nombre o del teléfono. Que esos datos ya aparezcan en el negocio no
bloquea el alta ni revela si pertenecen a otra persona; las respuestas sensibles
son genéricas. La gestión profesional sí conserva su regla propia de
unicidad/reutilización para una ficha activa con el mismo nombre y teléfono
normalizados, evitando duplicados operativos en mostrador. Desde esa ficha el
profesional puede emitir una invitación privada que caduca en 24 horas, funciona
una sola vez y activa exactamente el registro seleccionado. Su token aleatorio
solo se guarda como resumen.

Tanto el alta pública como la invitación dejan el acceso pendiente y sin una
contraseña utilizable. Abrir el enlace de correo mediante GET solo muestra y
valida el paso: no confirma el correo ni cambia credenciales. La persona crea su
contraseña y confirma el correo mediante un POST con CSRF. Los enlaces quedan
ligados al negocio, la cuenta, el correo y la huella vigente de la credencial;
consumirlos o cambiar esa credencial invalida su reutilización.
En una alta pública, la ficha permanece inactiva y el acceso conserva
`is_pending_public_registration=True` hasta ese POST final. Así, un registro
incompleto no contamina la reutilización de fichas del panel profesional y no se
activa si el negocio ha pausado entretanto las nuevas reservas.

Las contraseñas nuevas usan Argon2id y los hashes PBKDF2 anteriores se actualizan
después de un acceso correcto. Los intentos de acceso se limitan por identidad e
IP sin guardar esos identificadores en claro. La sesión cliente rota al entrar y
salir, caduca tras una hora sin actividad y conserva una huella opaca de la
contraseña vigente. Cambiar la contraseña invalida las sesiones anteriores.
La recuperación de una cuenta cliente acepta un correo verificado, responde
siempre de forma genérica y, cuando corresponde, envía un enlace limitado al
negocio que caduca en 60 minutos. El enlace deja de servir tras el primer cambio
de contraseña.

Los accesos profesionales creados por el superadministrador reciben un correo de
activación de un solo uso. Desde ese enlace, cada persona crea su propia
contraseña y verifica el correo antes de entrar en la operativa. Las cuentas
internas anteriores que todavía no tengan un correo verificado deben completarlo
desde `/cuenta/correo/`. Después, profesionales y superadministradores pueden
cambiar su contraseña desde `Mi cuenta`: el cambio comprueba la contraseña
actual, conserva la sesión presente e invalida las demás sesiones. El mecanismo
anterior de contraseña temporal se mantiene solo como compatibilidad para
cuentas heredadas o intervenciones administrativas controladas.

Las cuentas cliente también verifican su correo antes de reservar. Cambiar el
correo canónico desde la gestión profesional retira esa verificación, deja la
contraseña anterior sin uso y cierra las sesiones vinculadas; la cuenta solo
recupera la operativa digital después de verificar la nueva dirección y crear
otra clave. El alta, el reenvío de verificación y la recuperación de contraseña
aplican esperas y límites por correo, teléfono e IP según el flujo, con mensajes
genéricos que no confirman si una cuenta existe.

Al confirmar una cita, AgendaSalon prepara una confirmación y, si queda margen
suficiente, un recordatorio para 24 horas antes. El envío se gestiona mediante
una cola persistente con deduplicación de filas: en desarrollo usa la consola y en producción
se ha preparado Brevo mediante SMTP con STARTTLS por el puerto 2525. El dominio
y el remitente están autenticados y una prueba directa desde Django fue entregada;
desde el 14 de julio de 2026 el código de outbox, sus migraciones y el
temporizador de cinco minutos están desplegados y verificados en la aplicación
pública.
La deduplicación evita encolar dos veces el mismo hecho, pero el worker todavía
no dispone de lease y recuperación de trabajos interrumpidos; por eso la entrega
exactamente una vez permanece como endurecimiento P1.
Los formularios que crean o cambian direcciones de envío rechazan dominios
locales y reservados. En la interfaz, `sent` se presenta como `Aceptado por el
servicio de correo`: la aceptación SMTP no se confunde con entrega o lectura en
la bandeja del destinatario.

AgendaSalon incorpora una capa de privacidad operativa, no solo informativa.
Los documentos legales se publican por versión y huella; cada negocio completa
la identidad del responsable y acepta el encargo de tratamiento antes de poder
recoger nuevos datos. El registro y la invitación informan de la privacidad del
negocio; la casilla no premarcada se confirma en el POST final del enlace, junto
con la verificación del correo y la creación de contraseña. Abrir ese enlace por
GET no modifica la cuenta. Las cuentas cliente pueden
registrar solicitudes de derechos y el negocio documenta su seguimiento desde
`/legal/profesional/`. Las altas rápidas realizadas por un profesional desde
Clientes o Nueva cita exigen indicar el canal utilizado y confirmar que se ha
facilitado la información; la evidencia conserva documento, versión, huella,
fecha, actor y persona informada. El superadministrador puede consultar por
negocio el estado vigente y el historial de aceptaciones sin mezclar la
aceptación contractual del salón con la información recibida por sus clientes.
Si cambia la versión o la huella del documento aplicable, la cuenta debe dejar
una nueva constancia antes de confirmar otra reserva; una aceptación antigua no
se traslada automáticamente. La privacidad del negocio y el ejercicio de
derechos siguen accesibles cuando el negocio o su reserva pública están pausados.
Esa continuidad legal no reactiva el catálogo, la reserva ni el registro de
nuevas cuentas.

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

El acceso interno ofrece además una vía para negocios que todavía no tienen
cuenta. `/solicitar-alta/` recoge una solicitud mínima, informa de que aún no se
ha creado ningún acceso y la entrega a una bandeja privada del
superadministrador. Desde allí puede registrarse el seguimiento y reutilizar el
alta existente para crear el negocio y su primer profesional. Los datos privados
de contacto no se publican automáticamente.

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
- `/entrar/`: acceso canónico de profesionales y superadministración.
- `/cuenta/entrar/`: compatibilidad; redirige a `/entrar/`.
- `/cuenta/seguridad/`: cambio obligatorio o voluntario de contraseña interna.
- `/solicitar-alta/`: solicitud pública previa al alta de un negocio.
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
- `/superadmin/negocios/solicitudes/`: revisión y conversión de solicitudes de alta.
- `/superadmin/ajustes/`: tema de administración e imagen del acceso interno.
- `/superadmin/negocios/<id>/actividad/`: historial filtrable de un negocio.
- `/reservar/<slug>/`: reserva online híbrida.
- `/reservar/<slug>/confirmada/`: justificante autenticado de la reserva recién
  confirmada.
- `/clientes/<slug>/entrar/`: acceso cliente por negocio.
- `/clientes/<slug>/registro/`: alta cliente por negocio.
- `/clientes/<slug>/activar/`: activación limpia tras validar una invitación.
- `/clientes/<slug>/verificar-correo/`: espera y reenvío controlado de la
  verificación cliente.
- `/clientes/<slug>/recuperar-contrasena/`: recuperación cliente con respuesta
  genérica y enlace de 60 minutos.
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

La última verificación local deja la batería en 396 pruebas Django con resultado
correcto y nueve omisiones, además de 29 pruebas frontend: 17 unitarias y 12 de
componentes React. La cobertura con ramas es del 83 % y el umbral automatizado
impide bajar del 82 %. El build Vite, Ruff, `manage.py check`, la comprobación de
migraciones, `git diff --check`, `pip-audit`, `npm audit` y `pip check` finalizaron
sin incidencias. La QA visual se ejecutó en una copia desechable y la base
canónica permaneció intacta.

Estas cifras corresponden al bloque P0 verificado localmente el 16 de julio de
2026. La publicación no se deduce de esta evidencia local: debe acreditarse para
el SHA exacto mediante CI y el registro operativo del despliegue. La matriz de CI
ejecuta la batería sobre SQLite y PostgreSQL 17,
incluida la concurrencia real. Ruff, el build de producción, `pip-audit`,
`npm audit` y `pip check` forman parte de esas puertas. GitHub Actions reproduce lint,
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
operativa habitual de la plataforma. Las escrituras directas de Django Admin
sobre modelos de agenda no pasan necesariamente por los servicios y mutex del
producto; restringirlas o encauzarlas queda como P1.

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

La memoria técnica desplegada, con casos de uso, capturas y diagramas basados en
la aplicación real, está disponible en
[`docs/memoria/Memoria_tecnica_AgendaSalon.docx`](docs/memoria/Memoria_tecnica_AgendaSalon.docx).

## Calendario nacional BOE

AgendaSalon mantiene un único catálogo de festivos nacionales para todos los
negocios. El superadministrador puede sincronizar un año desde `Ajustes`, y cada
negocio decide en `Horarios` si esas fechas cierran su agenda.

La trazabilidad visible distingue las cargas locales de demostración de las
sincronizaciones oficiales del BOE. La última sincronización muestra el momento
real de finalización y descarta defensivamente cualquier registro fechado en el
futuro, de modo que una carga demo nunca puede ocultar una operación oficial
recién ejecutada.

La misma operación está disponible por consola:

```bash
python manage.py sync_national_holidays --year 2026
```

La sincronización registra la referencia oficial, reconcilia cambios sin
duplicar fechas y contabiliza citas futuras afectadas sin cancelarlas ni
moverlas. La reconciliación global es atómica sobre el catálogo, pero todavía no
adquiere el mutex de cada negocio ni ofrece resolución explícita por cita; ese
endurecimiento queda como P1.
