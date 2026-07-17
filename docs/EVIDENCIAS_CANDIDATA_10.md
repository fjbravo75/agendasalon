# Evidencias técnicas para evaluación

Este índice separa hechos reproducibles, validaciones pendientes de entorno y trabajo futuro. No convierte una intención en un resultado.

## Evidencias cerradas

| Área | Evidencia | Resultado |
|---|---|---|
| Motor de huecos | [`evidence/slot-engine/README.md`](evidence/slot-engine/README.md) y JSON reproducible | 31 solicitudes aceptadas en ambas estrategias; la política optimizada reduce de 120 a 0 los minutos en restos menores de 30 minutos en los cuatro escenarios fijados |
| Escalabilidad del dashboard | `apps/dashboards/api.py` y presupuesto en `test_superadmin_dashboard_api.py` | Métricas agrupadas por relación, sin producto cartesiano; presupuesto constante con 15 negocios |
| Seguridad | Escaneo independiente Codex Security sobre `eb7e3a4f...` | 210 archivos inventariados, 35 revisiones profundas, 11 candidatos validados y 0 vulnerabilidades reportables tras política final |
| BOE | `apps/holidays/services.py` y pruebas | Redirecciones deshabilitadas, host inicial permitido y cuerpo limitado a 2 MiB |
| Acceso cliente | `apps/customers/services.py` | Rama de cuenta ausente ejecuta una comprobación Argon2 ficticia para reducir enumeración temporal |
| Copias | `ops/backup_restore.py` | CLI exige una clave separada y autentica el manifiesto mediante HMAC-SHA-256 antes de restaurar |

## Evidencia comprobada en el despliegue académico

- HTTPS, redirección HTTP, cookies `Secure` y HSTS inicial sobre el dominio real;
  `preload` permanece desactivado de forma deliberada hasta estabilizar dominio
  y subdominios.
- PostgreSQL, Nginx y Gunicorn activos, con cero unidades fallidas en la
  aceptación final.
- Copias locales autenticadas, retención 7/4/6, control de frescura y
  temporizadores operativos verificados.
- P2 publicada y aceptada con 596 pruebas backend en SQLite y PostgreSQL 17, 34
  pruebas frontend y 85 % de cobertura de ramas.

## Evidencia que todavía necesita operación o escala real

- RTO/RPO medidos desde una copia cifrada almacenada fuera del Droplet y un
  simulacro integral de restauración desde ese destino.
- Monitorización y alertas centralizadas para disponibilidad, errores, correo,
  tareas, BOE, copias y rendimiento; la vigilancia local actual no sustituye
  esa capa.
- Prueba de carga sobre la infraestructura elegida, no solo sobre el portátil
  local o la matriz funcional de CI.

## Evidencia humana pendiente

La validación con profesionales reales no se ha ejecutado todavía. El protocolo, consentimiento mínimo y hoja de observación están en [`validation-professionals/README.md`](validation-professionals/README.md). Los campos de resultados permanecen vacíos hasta disponer de participantes reales.
