# Benchmark reproducible del motor de huecos

Comparación determinista entre el primer hueco cronológico y la puntuación real de compactación de AgendaSalon.

| Estrategia | Aceptadas | Rechazadas | Minutos ocupados | Minutos en restos <30 min | Restos <30 min | Intervalos restantes |
|---|---:|---:|---:|---:|---:|---:|
| Primera disponibilidad | 31 | 0 | 1590 | 120 | 8 | 11 |
| Optimizada | 31 | 0 | 1590 | 0 | 0 | 5 |

## Método

Simulación online determinista con intervalos de 15 minutos y la función real de puntuación del motor.
Cada estrategia recibe exactamente los mismos cuatro escenarios y la misma secuencia de duraciones.

## Lectura honesta

El resultado demuestra el efecto de la política sobre fragmentación y capacidad aceptada en estos escenarios; no se presenta como prueba universal de demanda real.
El JSON adjunto conserva entradas y resultados para repetir o ampliar el ensayo.

## Reproducción

`python tools/benchmark_slot_engine.py --output-dir docs/evidence/slot-engine`
