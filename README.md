# AgendaSalon

[![CI](https://github.com/fjbravo75/agendasalon/actions/workflows/ci.yml/badge.svg)](https://github.com/fjbravo75/agendasalon/actions/workflows/ci.yml)

AgendaSalon es una aplicación web para organizar las citas de peluquerías,
barberías y pequeños salones de belleza. Reúne en un mismo sistema la agenda
del equipo, la reserva online, la gestión de clientes y la administración del
negocio.

El proyecto es el entregable técnico de un Proyecto Fin de Máster en Desarrollo
Full Stack. Su núcleo está construido con Django y PostgreSQL; React se utiliza
en dos interfaces concretas donde aporta una interacción más rica: la agenda
profesional y el cuadro de mando del superadministrador.

**[Abrir la demostración](https://agendasalon.brvsoftwarestudio.com)** ·
**[Consultar la documentación](docs/README.md)** ·
**[Ver las evidencias técnicas](docs/EVIDENCIAS_CANDIDATA_10.md)**

![Agenda profesional de AgendaSalon](docs/memoria/capturas/03_agenda_react.png)

## Qué resuelve

AgendaSalon parte de una situación habitual: muchas citas siguen llegando por
teléfono, WhatsApp o mostrador, mientras otras personas prefieren reservar por
Internet. Ambos canales necesitan consultar la misma disponibilidad y no
pueden competir por un hueco que ya ha dejado de existir.

La aplicación mantiene un único motor de agenda para los dos recorridos. El
profesional puede preparar una cita desde su panel y el cliente puede explorar
servicios, días y horas sin registrarse. En los dos casos, el hueco se revalida
en el momento de confirmar para evitar solapamientos o reservas obsoletas.

## Funcionalidades principales

- **Agenda profesional:** vista diaria y mensual por líneas de trabajo, con
  citas, cierres, festivos, huecos disponibles y alternativas recomendadas.
- **Nueva cita asistida:** selección de cliente, canal y varios servicios con
  cálculo de duración, búsqueda de disponibilidad y confirmación protegida.
- **Reserva online híbrida:** el visitante elige primero servicios y hora; solo
  se identifica o crea su cuenta antes de revisar y confirmar.
- **Gestión de clientes:** fichas profesionales, cuentas cliente, invitaciones,
  verificación de correo, recuperación de contraseña y representación de
  familiares o personas autorizadas.
- **Operación del salón:** servicios, horarios, cierres, líneas de trabajo,
  festivos nacionales y revisión manual de citas pendientes de cierre.
- **Administración SaaS:** alta y pausa de negocios, gestión de profesionales,
  solicitudes de incorporación y visión global del estado de la plataforma.
- **Personalización visual:** modos claro y oscuro e imágenes propias para la
  reserva pública y los accesos internos.
- **Seguridad y trazabilidad:** aislamiento por negocio, permisos por rol,
  protección CSRF, límites de acceso, cola transaccional de correo, evidencias
  legales y registros de actividad.

La reserva pública ofrece un recorrido independiente para cada negocio:

![Reserva online de AgendaSalon](docs/memoria/capturas/05_reserva_publica.png)

## Arquitectura y tecnologías

| Área | Tecnología |
| --- | --- |
| Backend | Python 3.12 y Django 5.2 LTS |
| Base de datos | SQLite en desarrollo; PostgreSQL 17 en CI y producción |
| Interfaz | Plantillas Django y CSS, con React 19 y Vite 8 en dos islas |
| Imágenes | Pillow 12, saneado de metadatos y recodificación WebP |
| Contraseñas | Argon2id como algoritmo preferente |
| Producción | Gunicorn, Nginx y HTTPS con Let's Encrypt |
| Calidad | Ruff, pruebas Django, pruebas frontend, cobertura y auditorías de dependencias |

La mayor parte del producto permanece en Django para conservar una arquitectura
directa y defendible. React no sustituye los controles del servidor: consume
endpoints de solo lectura y las operaciones que modifican la agenda continúan
pasando por formularios y servicios de dominio protegidos.

## Demostración pública

La versión académica está disponible en
[agendasalon.brvsoftwarestudio.com](https://agendasalon.brvsoftwarestudio.com).
No representa una actividad comercial ni utiliza datos de personas reales.

La contraseña de las cuentas demostrativas es `DemoAgendaSalon2026!`.

| Perfil | Teléfono |
| --- | --- |
| Profesional de Peluquería Mari | `+34600111001` |
| Profesional de Barbería Norte | `+34600222001` |
| María López, reserva para su hijo Lucas | `600111201` |
| Lucía Gómez, cliente con cuenta propia | `600111202` |
| Daniel Vega, reserva para Rosa | `600111204` |
| Cliente de Barbería Norte | `600222201` |

La cuenta de superadministración utiliza credenciales privadas que no se
publican en el repositorio. Los personajes, relaciones y recorridos preparados
para la evaluación se explican en
[`docs/SUPUESTOS_USO_DEMO.md`](docs/SUPUESTOS_USO_DEMO.md).

## Puesta en marcha local

### Requisitos

- Python 3.12.
- Node.js 20.19 o 22.12 en adelante.
- Git.

El desarrollo local utiliza SQLite de forma predeterminada, por lo que no es
necesario instalar PostgreSQL para explorar el proyecto.

### Instalación en PowerShell

```powershell
git clone https://github.com/fjbravo75/agendasalon.git
cd agendasalon

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

npm.cmd ci
npm.cmd run build

.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py runserver
```

La aplicación quedará disponible en
[`http://127.0.0.1:8000/`](http://127.0.0.1:8000/). Django utiliza
`config.settings.dev` por defecto. El archivo [`.env.example`](.env.example)
documenta las variables necesarias para otros perfiles de ejecución.

Si se necesita una fecha demo reproducible, la semilla acepta una fecha base:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo --base-date 2026-07-13
```

Cada ejecución reconstruye los datos ficticios de los dos negocios para evitar
que las citas y notificaciones se acumulen entre pruebas.

## Verificación

Las comprobaciones principales pueden ejecutarse con:

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\coverage.exe run manage.py test
.\.venv\Scripts\coverage.exe report
npm.cmd run check
```

La integración continua repite las puertas de backend sobre SQLite y
PostgreSQL 17, valida el frontend, audita dependencias y revisa el historial en
busca de secretos. El detalle de resultados, cobertura, concurrencia y pruebas
manuales se mantiene en el
[`índice de evidencias`](docs/EVIDENCIAS_CANDIDATA_10.md), evitando convertir
este README en una bitácora de cada entrega.

## Documentación

- [`docs/README.md`](docs/README.md): índice de la documentación técnica.
- [`docs/EVIDENCIAS_CANDIDATA_10.md`](docs/EVIDENCIAS_CANDIDATA_10.md): pruebas
  reproducibles, evidencia verificada y límites pendientes.
- [`docs/OPERACION_PRODUCCION.md`](docs/OPERACION_PRODUCCION.md): despliegue,
  correo, copias, restauración y regeneración controlada de la demo.
- [`docs/SEGURIDAD_Y_PROTECCION_DE_DATOS.md`](docs/SEGURIDAD_Y_PROTECCION_DE_DATOS.md):
  arquitectura de seguridad, controles y riesgos residuales.
- [`docs/validation-professionals/README.md`](docs/validation-professionals/README.md):
  protocolo preparado para validación con profesionales reales.
- [`docs/memoria/Memoria_tecnica_AgendaSalon.docx`](docs/memoria/Memoria_tecnica_AgendaSalon.docx):
  memoria técnica completa del Proyecto Fin de Máster.

## Estado y alcance

AgendaSalon dispone de una demostración académica funcional con PostgreSQL,
HTTPS y datos ficticios reproducibles. El repositorio conserva evidencia de
calidad, seguridad, despliegue y recuperación, pero distingue con claridad lo
que ya está comprobado de aquello que todavía exige infraestructura externa,
carga representativa o sesiones con profesionales reales.

El proyecto no se presenta como un servicio comercial listo para operar con
datos reales. Antes de ese paso sería necesario cerrar, entre otros aspectos,
la validación profesional, la estrategia externa de copias y las condiciones
organizativas y legales de explotación.

## Autoría y uso

Proyecto desarrollado y mantenido por
[@fjbravo75](https://github.com/fjbravo75) como Proyecto Fin de Máster en
Desarrollo Full Stack.

El repositorio se publica como evidencia académica y actualmente no incorpora
una licencia de código abierto. Su publicación en GitHub no concede por sí sola
permiso para reutilizar, distribuir o crear trabajos derivados del código.
