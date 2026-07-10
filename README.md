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
- SQLite en desarrollo local
- Plantillas Django y CSS para la superficie construida
- React/Vite previsto para dos islas acotadas: agenda profesional y panel del
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

El motor de citas por duración total ya existe como servicio de dominio en
`apps/booking/slot_engine.py`. Calcula disponibilidad diaria por líneas, días de
mes con hueco real para una duración concreta, sugerencias cuando el día elegido
no tiene capacidad suficiente y puntuación inicial para recomendar huecos que
compactan la agenda.

La confirmación de citas pasa por `apps/booking/services.py`, que revalida el
hueco justo antes de crear `Appointment` y `AppointmentService`.

La pantalla Django del flujo profesional está disponible en
`/profesional/citas/nueva/`. Permite seleccionar cliente, canal, varios
servicios y día; calcula la duración total; muestra calendario mensual,
disponibilidad por líneas, hueco recomendado y sugerencias. La confirmación
final se resuelve mediante POST protegido y revalidación del hueco.

El acceso cliente final está disponible en `/clientes/<slug>/entrar/`, con alta
separada en `/clientes/<slug>/registro/`.

La reserva online está disponible en `/reservar/<slug>/`. Permite al cliente
elegir servicios, ver duración, precio y opciones recomendadas sin sesión. Al
elegir una hora guarda un borrador temporal, solicita acceso cliente y recupera
una revisión final antes de confirmar. La cita solo se crea mediante POST
protegido, tras revalidar el hueco, y queda vinculada a su ficha de cliente.

También existe una semilla de demostración reproducible:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo
```

Por defecto crea la semana de demostración `2026-07-06`, `Peluquería Mari`,
servicios, horarios, tres líneas, clientes, accesos cliente, citas, cierres, un
festivo de demostración y un día sin hueco para una cita de 180 minutos. El
comando puede ejecutarse varias veces sin duplicar los registros principales.

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

Verificación actual:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

La última verificación local completa deja la batería en 104 pruebas correctas.
También se puede ejecutar por dominios:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.booking
.\.venv\Scripts\python.exe manage.py test apps.customers
.\.venv\Scripts\python.exe manage.py test apps.accounts apps.businesses apps.dashboards apps.core apps.holidays apps.notifications
```

## Alcance limpio

Este repositorio contiene el producto entregable. No incluye recursos internos
de Codex, bitácoras exploratorias ni contextos visuales internos de trabajo.
