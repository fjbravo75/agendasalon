# AgendaSalon

[![CI](https://github.com/fjbravo75/agendasalon/actions/workflows/ci.yml/badge.svg)](https://github.com/fjbravo75/agendasalon/actions/workflows/ci.yml)

AgendaSalon es una aplicación web para organizar las citas de peluquerías,
barberías y pequeños salones de belleza. Reúne en un mismo sistema la agenda
del equipo, la reserva online, la gestión de clientes y la administración del
negocio.

El proyecto es el entregable técnico de un Proyecto Fin de Máster en Desarrollo
Full Stack. Su núcleo está construido con Django y PostgreSQL; React se utiliza
en la agenda profesional y en el cuadro de mando del superadministrador.

## Funcionalidades principales

- Agenda diaria y mensual por líneas de trabajo.
- Nueva cita asistida con varios servicios y búsqueda de disponibilidad.
- Reserva pública con revalidación del hueco en el momento de confirmar.
- Gestión de clientes, accesos y personas autorizadas.
- Servicios, horarios, cierres, festivos y líneas de trabajo.
- Administración de negocios y profesionales desde la plataforma.
- Modos claro y oscuro y personalización visual de cada negocio.
- Aislamiento por negocio, permisos por rol y trazabilidad de operaciones.

## Arquitectura y tecnologías

| Área | Tecnología |
| --- | --- |
| Backend | Python 3.12 y Django 5.2 LTS |
| Base de datos | SQLite en desarrollo; PostgreSQL 17 en integración y producción |
| Interfaz | Plantillas Django, React 19 y Vite 8 |
| Contraseñas | Argon2id como algoritmo preferente |
| Producción | Gunicorn, Nginx y HTTPS |
| Calidad | Ruff, pruebas Django, Vitest, cobertura y auditoría de dependencias |

La disponibilidad se calcula en el servidor para todos los canales. Tanto la
reserva pública como el panel profesional vuelven a comprobar el hueco antes de
crear una cita, evitando solapamientos y confirmaciones obsoletas.

## Demostración académica

La demostración está disponible en
[agendasalon.brvsoftwarestudio.com](https://agendasalon.brvsoftwarestudio.com).
Todos los negocios, personajes y datos del escenario son ficticios.

Las credenciales de evaluación no se publican en este repositorio. Se facilitan
a la academia dentro de la memoria técnica del Proyecto Fin de Máster.

## Puesta en marcha local

### Requisitos

- Python 3.12.
- Node.js 20.19 o 22.12 en adelante.
- Git.

El perfil de desarrollo utiliza SQLite de forma predeterminada, por lo que no
es necesario instalar PostgreSQL para explorar el proyecto.

### Backend

```bash
git clone https://github.com/fjbravo75/agendasalon.git
cd agendasalon

python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python manage.py migrate
```

En PowerShell, la activación del entorno virtual se realiza con:

```powershell
.\.venv\Scripts\Activate.ps1
```

Las tres contraseñas del escenario local se obtienen exclusivamente del
entorno. Antes de ejecutar la semilla hay que definir valores propios de al
menos 16 caracteres para estas variables:

```text
AGENDA_DEMO_SUPERADMIN_PASSWORD
AGENDA_DEMO_MARI_PASSWORD
AGENDA_DEMO_NORTE_PASSWORD
```

Después se puede crear el escenario ficticio y arrancar Django:

```bash
python manage.py seed_demo
python manage.py runserver
```

La aplicación quedará disponible en
[`http://127.0.0.1:8000/`](http://127.0.0.1:8000/). El archivo
[`.env.example`](.env.example) enumera las variables de configuración sin
incluir secretos utilizables.

### Frontend

```bash
npm ci
npm run build
```

Durante el desarrollo, Vite puede ejecutarse con `npm run dev`.

## Verificación

El perfil de pruebas genera credenciales efímeras en cada proceso y no depende
de las credenciales de la demostración:

```bash
ruff check .
python manage.py check --settings=config.settings.test
python manage.py makemigrations --check --dry-run --settings=config.settings.test
coverage run manage.py test --settings=config.settings.test
coverage report
npm run check
```

La integración continua repite las pruebas de backend sobre SQLite y
PostgreSQL 17, valida el frontend, audita las dependencias y revisa el historial
en busca de secretos.

## Estructura del repositorio

- `apps/`: módulos de negocio de Django.
- `config/`: configuración, rutas y perfiles de ejecución.
- `frontend/`: componentes React y pruebas de interfaz.
- `templates/`: páginas renderizadas por Django.
- `ops/`: utilidades operativas verificables.
- `tools/`: herramientas auxiliares de desarrollo y rendimiento.

## Autoría y uso

Proyecto desarrollado y mantenido por
[@fjbravo75](https://github.com/fjbravo75) como Proyecto Fin de Máster en
Desarrollo Full Stack.

El repositorio se publica como evidencia académica y no incorpora actualmente
una licencia de código abierto. Su publicación no concede por sí sola permiso
para reutilizar, distribuir o crear trabajos derivados del código.
