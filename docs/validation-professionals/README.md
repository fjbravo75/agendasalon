# Protocolo de validación con profesionales

Estado: **preparado y pendiente de ejecución con participantes reales**.

## Objetivo

Comprobar si un profesional de peluquería o barbería puede completar sin ayuda las operaciones principales y si entiende las recomendaciones del motor de huecos.

## Muestra mínima

- 3 profesionales de al menos 2 negocios distintos.
- Al menos una persona que gestione habitualmente citas por teléfono o WhatsApp.
- No utilizar datos reales de clientes: la prueba se realiza con la semilla demo.

## Tareas

1. Entrar como profesional y localizar la siguiente cita.
2. Crear una cita de dos servicios para una ficha existente.
3. Explicar por qué AgendaSalon recomienda el primer hueco mostrado.
4. Bloquear una franja por ausencia puntual.
5. Pausar un servicio y comprobar cómo cambia la reserva pública.
6. Cerrar una cita pasada como atendida o no presentada.

## Métricas

| Métrica | Cómo se registra | Criterio deseable |
|---|---|---|
| Finalización | tarea terminada sin intervención | al menos 85 % |
| Tiempo por tarea | cronómetro desde lectura hasta éxito | registrar mediana, sin inventar umbral posterior |
| Errores recuperables | clic o dato equivocado que el usuario corrige | máximo 1 por tarea en mediana |
| Ayuda requerida | pista verbal del moderador | ninguna en tareas 1, 2 y 6 |
| Comprensión de recomendación | explicación en palabras propias | entiende compactación sin vocabulario técnico |

## Guion del moderador

No explicar dónde pulsar. Leer cada tarea, pedir que piense en voz alta y registrar la primera duda literal. Si la persona queda bloqueada durante 90 segundos, ofrecer una única pista y marcar la tarea como realizada con ayuda.

## Hoja de resultados

| Participante | Perfil | T1 | T2 | T3 | T4 | T5 | T6 | Observación principal |
|---|---|---|---|---|---|---|---|---|
| P1 | pendiente | — | — | — | — | — | — | pendiente |
| P2 | pendiente | — | — | — | — | — | — | pendiente |
| P3 | pendiente | — | — | — | — | — | — | pendiente |

## Regla de honestidad

No se sustituirá una sesión real por una simulación de IA ni se presentarán estos criterios como resultados. Tras las sesiones se conservarán únicamente métricas y observaciones anonimizadas necesarias para mejorar el producto.

