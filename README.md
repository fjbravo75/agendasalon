# AgendaSalon

AgendaSalon es el entregable tecnico del Proyecto Fin de Master: un SaaS
Django-first para peluquerias, barberias y pequenos salones de belleza.

El MVP se centra en una agenda profesional asistida. El profesional crea citas
desde llamadas, WhatsApp o mostrador; selecciona cliente y servicios; el sistema
calcula duracion total y muestra dias y huecos reales por lineas de trabajo.

## Stack inicial

- Python 3.12
- Django 5.2 LTS
- SQLite en desarrollo local
- React/Vite pendiente para dos islas: agenda profesional y dashboard
  superadministrador

## Puesta en marcha local

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py check
```

El proyecto usa por defecto `config.settings.dev` para desarrollo.

## Estado actual

Base Django creada con estructura de settings por entorno, usuario custom
interno desde el inicio y nucleo inicial de modelos SaaS/agenda.

Incluye negocios, pertenencias profesionales, servicios, disponibilidad, cierres,
lineas de trabajo, fichas de cliente, contactos autorizados, citas,
servicios dentro de cita, festivos y notificaciones internas simuladas.

Verificacion actual:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
```

## Alcance limpio

Este repositorio contiene el producto entregable. No incluye recursos internos
de Codex, bitacoras exploratorias ni system contexts visuales de trabajo.
