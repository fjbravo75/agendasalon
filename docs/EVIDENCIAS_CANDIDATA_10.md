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
| Escenario demo | `apps/core/demo_scenario.py`, `seed_demo` y pruebas de contrato | 2 negocios, 28 servicios, 36 clientes, 11 accesos, 4 relaciones y 90 citas con fechas relativas y recuentos exactos |
| Regeneración académica | `refresh_demo`, `apps/core/demo_integrity.py`, orquestador y unidades systemd | Guardas de entorno, bloqueo PostgreSQL, supresión SMTP, cuarentena de medios, rollback y postflight sin residuos |

## Evidencia comprobada en el despliegue académico

- HTTPS, redirección HTTP, cookies `Secure` y HSTS inicial sobre el dominio real;
  `preload` permanece desactivado de forma deliberada hasta estabilizar dominio
  y subdominios.
- PostgreSQL, Nginx y Gunicorn activos, con cero unidades fallidas en la
  aceptación final.
- Copias locales autenticadas y verificadas. La política 7/4/6 sigue definida,
  pero el temporizador periódico de copias está actualmente deshabilitado e
  inactivo y no se presenta como una protección automática vigente.
- Versión funcional desplegada en producción:
  `545c5618fe915e91b022db70b2c77a75ab2d13ec`; la CI de `main` está correcta.
- Aceptación final de la regeneración manual con fecha base `2026-07-18`,
  solicitud `f3a7d392-b728-4206-908c-36ae2320d951` y huella semántica
  `f53e8ba21674fce64ed4944f90a1d359e717207e8bf4270529506b740a4fcdd8`.
- Postflight canónico: 3 usuarios, 2 negocios, 28 servicios —25 activos—, 36
  clientes, 90 citas y 8 festivos nacionales BOE de 2026; outbox, sesiones,
  throttles y residuos de evaluación a cero.
- Correo transaccional y avisos operativos activos. En la plataforma y en
  Barbería Norte, Brevo aceptó una vez el enlace de verificación y una vez el
  correo de prueba de cada ámbito. La aceptación del proveedor no se presenta
  como prueba de lectura por el destinatario.
- El despachador de regeneración manual está habilitado y activo. La unidad
  diaria de las 04:05 permanece instalada como antecedente operativo, pero está
  deshabilitada e inactiva. HTTPS respondió 200 y systemd no dejó unidades
  fallidas.
- Como antecedente, P2 publicada y aceptada con 596 pruebas backend en SQLite y
  PostgreSQL 17, 34 pruebas frontend y 85 % de cobertura de ramas.

## Evidencia que todavía necesita operación o escala real

- RTO/RPO medidos desde una copia cifrada almacenada fuera del Droplet y un
  simulacro integral de restauración desde ese destino.
- Monitorización y alertas centralizadas para disponibilidad, errores, correo,
  tareas, BOE, copias y rendimiento; la vigilancia local actual no sustituye
  esa capa.
- Prueba de carga sobre la infraestructura elegida, no solo sobre el portátil
  local o la matriz funcional de CI.
- Reactivación deliberada de la programación automática de copias si se decide
  volver a asumir ese ciclo operativo; el temporizador está hoy deshabilitado.

## Incidencia controlada durante la aceptación

La primera solicitud manual, `eab1c586-eef7-43db-87b8-a0cb417f9d9c`, se
interrumpió porque una conexión de monitorización externa coincidió con la fase
que exigía quiescencia total de PostgreSQL. La transacción no hizo commit,
PostgreSQL revirtió los cambios y el sistema quedó cerrado de forma segura, con
Gunicorn y los escritores detenidos. La recuperación se realizó bajo bloqueo
exclusivo, autorización efímera y cierre seguro ante error. Solo después de
verificar el estado previo se registró la solicitud final indicada arriba. Esta
incidencia demuestra el comportamiento fail-closed; no se oculta ni se presenta
como una regeneración correcta.

## Evidencia humana pendiente

La validación con profesionales reales no se ha ejecutado todavía. El protocolo, consentimiento mínimo y hoja de observación están en [`validation-professionals/README.md`](validation-professionals/README.md). Los campos de resultados permanecen vacíos hasta disponer de participantes reales.
