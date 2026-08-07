# AgendaSalon

Una aplicación web para organizar las citas, los clientes y el trabajo diario
de peluquerías, barberías y pequeños salones de belleza.

![Pantalla de acceso profesional de AgendaSalon](.github/assets/acceso-profesional-agendasalon.webp)

**[Abrir la demostración](https://agendasalon.brvsoftwarestudio.com)** ·
**[Ver el vídeo del proyecto](https://youtu.be/b9vQOPy3WPc)**

## Qué es AgendaSalon

AgendaSalon reúne en un mismo sistema la agenda del equipo, la reserva por
Internet, la gestión de clientes y la administración del negocio.

El proyecto parte de una situación habitual: muchas citas siguen llegando por
teléfono, WhatsApp o en el propio establecimiento, mientras otras personas
prefieren reservar online. AgendaSalon reúne la disponibilidad de todos esos
canales para evitar solapamientos y no ofrecer horas que ya están ocupadas.

AgendaSalon se ha desarrollado como Proyecto Fin de Máster en Desarrollo Full
Stack.

## Funcionalidades principales

- Agenda diaria y mensual organizada por líneas de trabajo.
- Creación asistida de citas con uno o varios servicios.
- Reserva pública con una comprobación final de la disponibilidad.
- Gestión de clientes, accesos y personas autorizadas.
- Configuración de servicios, horarios, cierres y festivos.
- Administración general de negocios y profesionales.
- Modos claro y oscuro y personalización visual de cada negocio.
- Separación de los datos por negocio, permisos según el tipo de usuario y
  registro de la actividad.

## Demostración académica

La aplicación está desplegada en
[agendasalon.brvsoftwarestudio.com](https://agendasalon.brvsoftwarestudio.com).
Las cuentas de acceso y los datos utilizados en la demostración son ficticios.
AgendaSalon no está implantado actualmente en ninguno de los establecimientos
que aparecen en ella.

Las credenciales preparadas para la evaluación no se publican en este
repositorio. La academia las recibe dentro de la memoria técnica del Proyecto
Fin de Máster.

Con las credenciales incluidas en la memoria, el evaluador puede recorrer tres
usos principales:

- gestionar una cita desde el panel profesional;
- realizar una reserva como cliente;
- revisar negocios y profesionales desde la administración general.

## Tecnologías y arquitectura

| Área | Tecnología |
| --- | --- |
| Backend | Python 3.12 y Django 5.2 LTS |
| Base de datos | SQLite en desarrollo y PostgreSQL en integración y producción |
| Interfaz | Plantillas Django, React 19 y Vite 8 |
| Producción | Gunicorn, Nginx y HTTPS |
| Calidad | Ruff, pruebas Django y React, cobertura e integración continua |

La mayor parte de la aplicación está construida con Django. React se utiliza
en la agenda profesional y en el panel de administración general, donde
facilita la consulta de información y el cambio entre vistas.

La disponibilidad se calcula siempre en el servidor. Antes de guardar una
cita, tanto la reserva pública como el panel profesional vuelven a comprobar
que el horario sigue libre.

## Puesta en marcha local

### Requisitos

- Python 3.12.
- Node.js 20.19 o 22.12 en adelante.
- Git.

Para trabajar en local no es necesario instalar PostgreSQL: el perfil de
desarrollo utiliza SQLite de forma predeterminada.

### Instalación

```bash
git clone https://github.com/fjbravo75/agendasalon.git
cd agendasalon

python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt

npm ci
npm run build

python manage.py migrate
```

En PowerShell, la línea `source .venv/bin/activate` se sustituye por:

```powershell
.\.venv\Scripts\Activate.ps1
```

### Configuración de la demostración local

Antes de crear los datos de ejemplo hay que definir tres contraseñas propias,
de al menos 16 caracteres, mediante estas variables de entorno:

```text
AGENDA_DEMO_SUPERADMIN_PASSWORD
AGENDA_DEMO_MARI_PASSWORD
AGENDA_DEMO_NORTE_PASSWORD
```

En Bash se define una variable con `export NOMBRE="valor"`. En PowerShell se
utiliza `$env:NOMBRE="valor"`. El archivo [`.env.example`](.env.example)
describe el resto de opciones disponibles, pero no contiene credenciales
utilizables.

Después se puede crear el escenario ficticio y arrancar la aplicación:

```bash
python manage.py seed_demo
python manage.py runserver
```

La aplicación quedará disponible en
[`http://127.0.0.1:8000/`](http://127.0.0.1:8000/).

## Comprobaciones

Las verificaciones principales se ejecutan con:

```bash
ruff check .
python manage.py check --settings=config.settings.test
python manage.py makemigrations --check --dry-run --settings=config.settings.test
coverage run manage.py test --settings=config.settings.test
coverage report
npm run check
```

GitHub Actions ejecuta automáticamente estas comprobaciones sobre SQLite y
PostgreSQL 17.

## Estructura del repositorio

- `apps/`: funcionalidades del backend organizadas por áreas de negocio.
- `config/`: configuración y rutas de Django.
- `frontend/`: componentes y pruebas de las interfaces React.
- `templates/`: páginas renderizadas por Django.
- `static/`: estilos, JavaScript e imágenes de la aplicación.
- `ops/`: scripts de apoyo para el despliegue y las tareas de mantenimiento.

## Autoría y uso

Proyecto desarrollado y mantenido por
[@fjbravo75](https://github.com/fjbravo75) como Proyecto Fin de Máster en
Desarrollo Full Stack.

El repositorio se publica como evidencia académica y no incorpora actualmente
una licencia de código abierto. Su publicación no concede por sí sola permiso
para reutilizar, distribuir o crear trabajos derivados del código.
