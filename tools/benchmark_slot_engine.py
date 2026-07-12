"""Benchmark determinista de la política de compactación del motor de huecos."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys

import django

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()


DAY_START = datetime(2026, 7, 13, 9, 0)
SLOT_INTERVAL = 15


@dataclass(frozen=True)
class Scenario:
    name: str
    free_intervals: tuple[tuple[int, int], ...]
    requests: tuple[int, ...]


@dataclass(frozen=True)
class Result:
    scenario: str
    strategy: str
    accepted: int
    rejected: int
    occupied_minutes: int
    remaining_minutes: int
    small_fragment_minutes: int
    small_fragment_count: int
    remaining_intervals: int


SCENARIOS = (
    Scenario(
        "huecos_exactos_despues",
        ((0, 180), (210, 255), (300, 360), (390, 540)),
        (45, 60, 30, 45, 60, 30, 90, 45),
    ),
    Scenario(
        "jornada_fragmentada",
        ((0, 75), (105, 225), (255, 330), (360, 540)),
        (60, 45, 30, 75, 30, 60, 45, 30),
    ),
    Scenario(
        "mezcla_servicios_largos",
        ((0, 150), (180, 285), (315, 540)),
        (90, 45, 60, 30, 90, 45, 60),
    ),
    Scenario(
        "picos_y_rellenos",
        ((0, 60), (90, 270), (300, 345), (375, 540)),
        (45, 30, 60, 45, 75, 30, 60, 45),
    ),
)


def _slot_candidates(intervals, duration):
    from apps.booking.slot_engine import _score_slot

    candidates = []
    for free_start, free_end in intervals:
        start = free_start
        while start + duration <= free_end:
            starts_at = DAY_START + timedelta(minutes=start)
            ends_at = starts_at + timedelta(minutes=duration)
            score, reason = _score_slot(
                starts_at=starts_at,
                ends_at=ends_at,
                free_start=DAY_START + timedelta(minutes=free_start),
                free_end=DAY_START + timedelta(minutes=free_end),
                duration_minutes=duration,
            )
            candidates.append((start, score, reason, free_start, free_end))
            start += SLOT_INTERVAL
    return candidates


def _allocate(intervals, *, start, duration):
    updated = []
    end = start + duration
    for free_start, free_end in intervals:
        if free_start <= start and end <= free_end:
            if free_start < start:
                updated.append((free_start, start))
            if end < free_end:
                updated.append((end, free_end))
        else:
            updated.append((free_start, free_end))
    return tuple(sorted(updated))


def run_scenario(scenario, strategy):
    intervals = scenario.free_intervals
    accepted_durations = []
    for duration in scenario.requests:
        candidates = _slot_candidates(intervals, duration)
        if not candidates:
            continue
        if strategy == "first_available":
            chosen = min(candidates, key=lambda item: item[0])
        else:
            chosen = min(candidates, key=lambda item: (-item[1], item[0]))
        intervals = _allocate(intervals, start=chosen[0], duration=duration)
        accepted_durations.append(duration)

    small_fragments = [end - start for start, end in intervals if end - start < 30]
    return Result(
        scenario=scenario.name,
        strategy=strategy,
        accepted=len(accepted_durations),
        rejected=len(scenario.requests) - len(accepted_durations),
        occupied_minutes=sum(accepted_durations),
        remaining_minutes=sum(end - start for start, end in intervals),
        small_fragment_minutes=sum(small_fragments),
        small_fragment_count=len(small_fragments),
        remaining_intervals=len(intervals),
    )


def build_report():
    results = [
        run_scenario(scenario, strategy)
        for scenario in SCENARIOS
        for strategy in ("first_available", "optimized")
    ]
    totals = {}
    for strategy in ("first_available", "optimized"):
        rows = [result for result in results if result.strategy == strategy]
        totals[strategy] = {
            "accepted": sum(row.accepted for row in rows),
            "rejected": sum(row.rejected for row in rows),
            "occupied_minutes": sum(row.occupied_minutes for row in rows),
            "small_fragment_minutes": sum(row.small_fragment_minutes for row in rows),
            "small_fragment_count": sum(row.small_fragment_count for row in rows),
            "remaining_intervals": sum(row.remaining_intervals for row in rows),
        }
    return {
        "schema_version": 1,
        "method": {
            "description": "Simulación online determinista con intervalos de 15 minutos y la función real de puntuación del motor.",
            "baseline": "Primer hueco cronológico disponible.",
            "optimized": "Mayor puntuación; desempate por hora más temprana.",
            "limitations": [
                "No modela cancelaciones, preferencias humanas ni distribución comercial real.",
                "Mide la política de selección; las reglas de disponibilidad se prueban aparte en Django.",
            ],
        },
        "scenarios": [asdict(scenario) for scenario in SCENARIOS],
        "results": [asdict(result) for result in results],
        "totals": totals,
    }


def markdown_report(report):
    lines = [
        "# Benchmark reproducible del motor de huecos",
        "",
        "Comparación determinista entre el primer hueco cronológico y la puntuación real de compactación de AgendaSalon.",
        "",
        "| Estrategia | Aceptadas | Rechazadas | Minutos ocupados | Minutos en restos <30 min | Restos <30 min | Intervalos restantes |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, label in (("first_available", "Primera disponibilidad"), ("optimized", "Optimizada")):
        row = report["totals"][strategy]
        lines.append(
            f"| {label} | {row['accepted']} | {row['rejected']} | {row['occupied_minutes']} | "
            f"{row['small_fragment_minutes']} | {row['small_fragment_count']} | {row['remaining_intervals']} |"
        )
    lines.extend(
        [
            "",
            "## Método",
            "",
            report["method"]["description"],
            "Cada estrategia recibe exactamente los mismos cuatro escenarios y la misma secuencia de duraciones.",
            "",
            "## Lectura honesta",
            "",
            "El resultado demuestra el efecto de la política sobre fragmentación y capacidad aceptada en estos escenarios; no se presenta como prueba universal de demanda real.",
            "El JSON adjunto conserva entradas y resultados para repetir o ampliar el ensayo.",
            "",
            "## Reproducción",
            "",
            "`python tools/benchmark_slot_engine.py --output-dir docs/evidence/slot-engine`",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = build_report()
    (args.output_dir / "benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "README.md").write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
