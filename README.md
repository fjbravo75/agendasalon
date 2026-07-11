# AgendaSalon

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

## Puesta en marcha local

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
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

La pantalla Django del flujo profesional está disponible en
`/profesional/citas/nueva/`. Permite seleccionar cliente, canal, varios
servicios y día; calcula la duración total; muestra calendario mensual,
disponibilidad por líneas, hueco recomendado y sugerencias. La confirmación
final se resuelve mediante POST protegido y revalidación del hueco. Los campos
obligatorios usan asterisco y leyenda conjunta; la búsqueda parcial conserva
canal y día por defecto sin mostrar avisos rojos redundantes. La indicación de
servicios permanece visible porque desbloquea el cálculo de disponibilidad.

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
innecesarios. El historial completo admite filtros y paginación por cursor.

También existe una semilla de demostración reproducible:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo
```

Por defecto crea la semana de demostración `2026-07-06`, `Peluquería Mari`,
servicios, horarios, tres líneas, clientes, accesos cliente, citas, cierres, un
festivo de demostración y un día sin hueco para una cita de 180 minutos. El
comando puede ejecutarse varias veces sin duplicar los registros principales.

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
- `/profesional/horarios/`: disponibilidad, cierres y líneas.
- `/profesional/ajustes/`: modo del panel e imagen pública del negocio.
- `/clientes/profesional/`: fichas de cliente.
- `/superadmin/dashboard/`: estado general de AgendaSalon.
- `/superadmin/dashboard/datos/`: datos JSON protegidos del cuadro de mando.
- `/superadmin/negocios/`: alta y gestión de negocios y accesos profesionales.
- `/superadmin/negocios/<id>/actividad/`: historial filtrable de un negocio.
- `/reservar/<slug>/`: reserva online híbrida.
- `/clientes/<slug>/entrar/`: acceso cliente por negocio.
- `/clientes/<slug>/registro/`: alta cliente por negocio.
- `/clientes/<slug>/activar/`: activación limpia tras validar una invitación.

Cada cliente llega mediante la URL del negocio concreto. Por ejemplo, la
demostración utiliza `/reservar/peluqueria-mari/` y
`/reservar/barberia-norte/`; AgendaSalon no muestra un selector global de
salones ni enlaza el acceso profesional desde las pantallas cliente.

Verificación actual:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
npm.cmd run check
```

La última verificación completa deja la batería en 198 pruebas Django y
operativas, además de 17 pruebas frontend correctas. La compatibilidad con
PostgreSQL 17 se verificó en el bloque de producción, incluida una prueba
concurrente real. El build de producción, `pip-audit` y `npm audit` también se
han verificado sin vulnerabilidades conocidas.
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
procesos.
