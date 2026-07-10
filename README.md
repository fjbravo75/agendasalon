# AgendaSalon

AgendaSalon es el entregable tecnico del Proyecto Fin de Master: un SaaS
Django-first para peluquerias, barberias y pequenos salones de belleza.

El MVP se centra en un motor unico de citas optimizadas. El profesional puede
crear citas desde llamadas, WhatsApp o mostrador. En la reserva online, el
visitante explora servicios y huecos sin cuenta; solo entra o se registra al
revisar y confirmar la hora elegida. Ambos canales usan la misma logica de
disponibilidad, scoring y revalidacion.

## Stack

- Python 3.12
- Django 5.2 LTS
- SQLite en desarrollo local
- Django templates y CSS para la superficie construida
- React/Vite previsto para dos islas acotadas: agenda profesional y dashboard
  superadministrador

## Puesta en marcha local

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py runserver
```

La aplicación queda disponible en `http://127.0.0.1:8000/`. El proyecto usa
por defecto `config.settings.dev` para desarrollo.

## Accesos demo

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

Base Django creada con estructura de settings por entorno, usuario custom
interno desde el inicio, nucleo inicial de modelos SaaS/agenda y entrada
autenticada por telefono normalizado.

Incluye negocios, pertenencias profesionales, servicios, disponibilidad, cierres,
lineas de trabajo, fichas de cliente, contactos autorizados, citas,
accesos cliente, servicios dentro de cita, festivos y notificaciones internas
simuladas.

Tambien incluye login visual, templates base, redireccion por rol y resolucion
del negocio activo del profesional autenticado.

La entrada profesional `/profesional/` funciona como agenda operativa de la
jornada: muestra datos que entiende el profesional, lineas de trabajo, huecos
recomendados, estado del salon y un vacio accionable cuando no hay citas.

El motor de citas por duracion total ya existe como servicio de dominio en
`apps/booking/slot_engine.py`. Calcula disponibilidad diaria por lineas, dias de
mes con hueco real para una duracion concreta, sugerencias cuando el dia elegido
no tiene capacidad suficiente y puntuacion inicial para recomendar huecos que
compactan la agenda.

La confirmacion de citas pasa por `apps/booking/services.py`, que revalida el
hueco justo antes de crear `Appointment` y `AppointmentService`.

La pantalla Django del flujo profesional esta disponible en
`/profesional/citas/nueva/`. Permite seleccionar cliente, canal, varios
servicios y dia; calcula la duracion total; muestra calendario mensual,
disponibilidad por lineas, hueco recomendado y sugerencias. La confirmacion
final se resuelve mediante POST protegido y revalidacion del hueco.

El acceso cliente final esta disponible en `/clientes/<slug>/entrar/`, con alta
separada en `/clientes/<slug>/registro/`.

La reserva online esta disponible en `/reservar/<slug>/`. Permite al cliente
elegir servicios, ver duracion, precio y opciones recomendadas sin sesion. Al
elegir una hora guarda un borrador temporal, solicita acceso cliente y recupera
una revision final antes de confirmar. La cita solo se crea mediante POST
protegido, tras revalidar el hueco, y queda vinculada a su ficha de cliente.

Tambien existe una semilla demo reproducible:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo
```

Por defecto crea la semana demo `2026-07-06`, `Peluquería Mari`, servicios,
horarios, tres lineas, clientes, accesos cliente, citas, cierres, festivo demo y
un dia sin hueco para una cita de 180 minutos. El comando puede ejecutarse varias
veces sin duplicar los registros principales.

## Rutas principales

- `/`: selector público de negocio.
- `/cuenta/entrar/`: acceso de profesionales y superadministración.
- `/profesional/`: agenda operativa de la jornada.
- `/profesional/citas/nueva/`: asistente de nueva cita.
- `/profesional/servicios/`: catálogo profesional.
- `/profesional/horarios/`: disponibilidad, cierres y líneas.
- `/clientes/profesional/`: fichas de cliente.
- `/reservar/<slug>/`: reserva online híbrida.
- `/clientes/<slug>/entrar/`: acceso cliente por negocio.
- `/clientes/<slug>/registro/`: alta cliente por negocio.

Verificacion actual:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

La última verificación local completa deja la suite en 104 tests OK. También se
puede ejecutar por dominios:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.booking
.\.venv\Scripts\python.exe manage.py test apps.customers
.\.venv\Scripts\python.exe manage.py test apps.accounts apps.businesses apps.dashboards apps.core apps.holidays apps.notifications
```

## Alcance limpio

Este repositorio contiene el producto entregable. No incluye recursos internos
de Codex, bitacoras exploratorias ni system contexts visuales de trabajo.
