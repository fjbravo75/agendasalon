"""Create and restore PostgreSQL plus media backups for AgendaSalon."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from django.core.exceptions import ImproperlyConfigured

from config.settings.database import postgres_database_config


MANIFEST_NAME = "manifest.json"
DATABASE_DUMP_NAME = "database.dump"
MEDIA_ARCHIVE_NAME = "media.tar.gz"
BACKUP_DIRECTORY_PREFIX = "agendasalon-"


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupCandidate:
    path: Path
    created_at: datetime


@dataclass(frozen=True)
class BackupRetentionPlan:
    keep: tuple[BackupCandidate, ...]
    remove: tuple[BackupCandidate, ...]


def create_backup(
    *,
    database_url: str,
    media_root: Path,
    backup_root: Path,
    pg_dump_executable: str = "pg_dump",
    now: datetime | None = None,
    integrity_key: str | None = None,
) -> Path:
    database = postgres_database_config(database_url)
    now = now or datetime.now(timezone.utc)
    backup_dir = _unique_backup_dir(backup_root, now)
    database_dump = backup_dir / DATABASE_DUMP_NAME
    media_archive = backup_dir / MEDIA_ARCHIVE_NAME

    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
        subprocess.run(
            [
                pg_dump_executable,
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                f"--file={database_dump}",
                f"--host={database['HOST']}",
                f"--port={database['PORT']}",
                f"--username={database['USER']}",
                f"--dbname={database['NAME']}",
            ],
            env=_postgres_environment(database),
            check=True,
        )
        if not database_dump.is_file() or database_dump.stat().st_size == 0:
            raise BackupError("pg_dump no creó una copia válida de la base de datos.")

        _archive_media(media_root, media_archive)
        manifest = {
            "schema_version": 1,
            "created_at": now.isoformat(),
            "database": {
                "engine": "postgresql",
                "name": database["NAME"],
                "host": database["HOST"],
                "port": database["PORT"],
                "file": DATABASE_DUMP_NAME,
                "sha256": _sha256(database_dump),
            },
            "media": {
                "file": MEDIA_ARCHIVE_NAME,
                "sha256": _sha256(media_archive),
            },
        }
        if integrity_key:
            manifest["authenticity"] = {
                "algorithm": "hmac-sha256",
                "digest": _manifest_hmac(manifest, integrity_key),
            }
        (backup_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return backup_dir
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise


def verify_backup(
    backup_dir: Path,
    *,
    integrity_key: str | None = None,
    require_authenticity: bool = False,
) -> dict:
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupError("La copia no contiene manifest.json.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise BackupError("La versión del manifiesto no es compatible.")

    authenticity = manifest.get("authenticity")
    if authenticity:
        if authenticity.get("algorithm") != "hmac-sha256" or not integrity_key:
            raise BackupError("La copia requiere una clave de autenticidad válida.")
        expected_digest = authenticity.get("digest", "")
        unsigned_manifest = dict(manifest)
        unsigned_manifest.pop("authenticity", None)
        if not hmac.compare_digest(
            expected_digest,
            _manifest_hmac(unsigned_manifest, integrity_key),
        ):
            raise BackupError("La autenticidad del manifiesto no coincide.")
    elif require_authenticity:
        raise BackupError("La copia no contiene una prueba de autenticidad.")

    expected_files = {
        "database": DATABASE_DUMP_NAME,
        "media": MEDIA_ARCHIVE_NAME,
    }
    for section, expected_file in expected_files.items():
        entry = manifest.get(section) or {}
        if entry.get("file") != expected_file:
            raise BackupError(f"El artefacto de {section} tiene un nombre no permitido.")
        artifact = backup_dir / expected_file
        expected_hash = entry.get("sha256")
        if not artifact.is_file() or not expected_hash:
            raise BackupError(f"Falta el artefacto de {section}.")
        if _sha256(artifact) != expected_hash:
            raise BackupError(f"La suma de comprobación de {section} no coincide.")
    return manifest


def restore_backup(
    *,
    database_url: str,
    backup_dir: Path,
    media_target: Path,
    confirm_restore: bool,
    replace_media: bool = False,
    pg_restore_executable: str = "pg_restore",
    integrity_key: str | None = None,
    require_authenticity: bool = False,
) -> Path | None:
    if not confirm_restore:
        raise BackupError("La restauración requiere --confirm-restore.")

    database = postgres_database_config(database_url)
    manifest = verify_backup(
        backup_dir,
        integrity_key=integrity_key,
        require_authenticity=require_authenticity,
    )
    database_dump = backup_dir / manifest["database"]["file"]
    media_archive = backup_dir / manifest["media"]["file"]

    if media_target.exists() and any(media_target.iterdir()) and not replace_media:
        raise BackupError(
            "El destino de media no está vacío. Usa --replace-media para conservar una copia de reversión."
        )

    subprocess.run(
        [
            pg_restore_executable,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            f"--host={database['HOST']}",
            f"--port={database['PORT']}",
            f"--username={database['USER']}",
            f"--dbname={database['NAME']}",
            os.fspath(database_dump),
        ],
        env=_postgres_environment(database),
        check=True,
    )

    rollback_dir = None
    media_target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=media_target.parent) as temporary_dir:
        extracted_media = Path(temporary_dir) / "media"
        extracted_media.mkdir()
        with tarfile.open(media_archive, "r:gz") as archive:
            archive.extractall(extracted_media, filter="data")

        if media_target.exists() and any(media_target.iterdir()):
            rollback_dir = media_target.with_name(
                f"{media_target.name}.before-restore-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
            )
            media_target.replace(rollback_dir)
        elif media_target.exists():
            media_target.rmdir()
        shutil.copytree(extracted_media, media_target)

    return rollback_dir


def build_retention_plan(
    *,
    backup_root: Path,
    integrity_key: str,
    daily: int = 7,
    weekly: int = 4,
    monthly: int = 6,
) -> BackupRetentionPlan:
    """Select verified backups to keep without deleting anything."""
    if min(daily, weekly, monthly) < 1:
        raise BackupError("La retención diaria, semanal y mensual debe ser al menos 1.")

    candidates = _verified_backup_candidates(
        backup_root=backup_root,
        integrity_key=integrity_key,
    )
    keep_paths: set[Path] = set()
    keep_paths.update(
        candidate.path
        for candidate in _latest_per_period(
            candidates,
            limit=daily,
            period=lambda value: value.date(),
        )
    )
    keep_paths.update(
        candidate.path
        for candidate in _latest_per_period(
            candidates,
            limit=weekly,
            period=lambda value: value.isocalendar()[:2],
        )
    )
    keep_paths.update(
        candidate.path
        for candidate in _latest_per_period(
            candidates,
            limit=monthly,
            period=lambda value: (value.year, value.month),
        )
    )
    return BackupRetentionPlan(
        keep=tuple(candidate for candidate in candidates if candidate.path in keep_paths),
        remove=tuple(candidate for candidate in candidates if candidate.path not in keep_paths),
    )


def apply_retention_plan(
    plan: BackupRetentionPlan,
    *,
    backup_root: Path,
    integrity_key: str,
) -> None:
    """Delete only candidates that still pass the authenticated verification."""
    root = backup_root.resolve()
    for candidate in plan.remove:
        _validate_managed_backup_path(candidate.path, root=root)
        verify_backup(
            candidate.path,
            integrity_key=integrity_key,
            require_authenticity=True,
        )
    for candidate in plan.remove:
        shutil.rmtree(candidate.path)


def check_backup_freshness(
    *,
    backup_root: Path,
    integrity_key: str,
    max_age_hours: int = 36,
    now: datetime | None = None,
) -> BackupCandidate:
    """Return the newest verified backup or fail if it is missing or stale."""
    if max_age_hours < 1:
        raise BackupError("La antigüedad máxima debe ser al menos una hora.")
    candidates = _verified_backup_candidates(
        backup_root=backup_root,
        integrity_key=integrity_key,
    )
    if not candidates:
        raise BackupError("No existe ninguna copia autenticada y verificable.")

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise BackupError("La fecha de comprobación debe incluir zona horaria.")
    latest = candidates[0]
    age = current_time.astimezone(timezone.utc) - latest.created_at
    if age.total_seconds() < -300:
        raise BackupError("La copia más reciente tiene una fecha futura no válida.")
    if age.total_seconds() > max_age_hours * 60 * 60:
        raise BackupError("La copia más reciente supera la antigüedad permitida.")
    return latest


def _archive_media(media_root: Path, destination: Path) -> None:
    with tarfile.open(destination, "w:gz") as archive:
        if not media_root.exists():
            return
        for path in sorted(media_root.rglob("*")):
            if path.is_symlink():
                raise BackupError("El directorio de media contiene un enlace simbólico no permitido.")
            if path.is_file():
                archive.add(path, arcname=path.relative_to(media_root), recursive=False)


def _verified_backup_candidates(
    *,
    backup_root: Path,
    integrity_key: str,
) -> tuple[BackupCandidate, ...]:
    if not integrity_key:
        raise BackupError("La retención requiere una clave de autenticidad válida.")
    if not backup_root.is_dir():
        raise BackupError("El directorio raíz de copias no existe.")

    root = backup_root.resolve()
    candidates = []
    for backup_dir in sorted(backup_root.iterdir()):
        if not backup_dir.name.startswith(BACKUP_DIRECTORY_PREFIX):
            continue
        _validate_managed_backup_path(backup_dir, root=root)
        manifest = verify_backup(
            backup_dir,
            integrity_key=integrity_key,
            require_authenticity=True,
        )
        candidates.append(
            BackupCandidate(
                path=backup_dir,
                created_at=_manifest_created_at(manifest),
            )
        )
    return tuple(sorted(candidates, key=lambda item: item.created_at, reverse=True))


def _validate_managed_backup_path(path: Path, *, root: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise BackupError("La retención encontró una ruta de copia no permitida.")
    if path.resolve().parent != root or not path.name.startswith(BACKUP_DIRECTORY_PREFIX):
        raise BackupError("La retención se ha limitado a una ruta no gestionada.")


def _manifest_created_at(manifest: dict) -> datetime:
    try:
        created_at = datetime.fromisoformat(manifest["created_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError("La copia no contiene una fecha de creación válida.") from exc
    if created_at.tzinfo is None:
        raise BackupError("La fecha de la copia debe incluir zona horaria.")
    return created_at.astimezone(timezone.utc)


def _latest_per_period(candidates, *, limit, period):
    selected = []
    periods = set()
    for candidate in candidates:
        key = period(candidate.created_at)
        if key in periods:
            continue
        selected.append(candidate)
        periods.add(key)
        if len(selected) == limit:
            break
    return selected


def _postgres_environment(database: dict) -> dict:
    environment = os.environ.copy()
    environment["PGPASSWORD"] = database["PASSWORD"]
    environment["PGSSLMODE"] = database["OPTIONS"]["sslmode"]
    return environment


def _unique_backup_dir(backup_root: Path, now: datetime) -> Path:
    base_name = now.strftime("agendasalon-%Y%m%dT%H%M%SZ")
    candidate = backup_root / base_name
    counter = 1
    while candidate.exists():
        candidate = backup_root / f"{base_name}-{counter}"
        counter += 1
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_hmac(manifest: dict, integrity_key: str) -> str:
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(integrity_key.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Crear una copia de BD y media.")
    backup.add_argument("--backup-root", type=Path, required=True)
    backup.add_argument("--media-root", type=Path, default=Path("media"))
    backup.add_argument("--pg-dump", default="pg_dump")

    verify = subparsers.add_parser("verify", help="Verificar integridad de una copia.")
    verify.add_argument("--backup-dir", type=Path, required=True)

    restore = subparsers.add_parser("restore", help="Restaurar BD y media.")
    restore.add_argument("--backup-dir", type=Path, required=True)
    restore.add_argument("--media-target", type=Path, required=True)
    restore.add_argument("--pg-restore", default="pg_restore")
    restore.add_argument("--confirm-restore", action="store_true")
    restore.add_argument("--replace-media", action="store_true")

    retention = subparsers.add_parser(
        "retention",
        help="Calcular o aplicar la retención de copias autenticadas.",
    )
    retention.add_argument("--backup-root", type=Path, required=True)
    retention.add_argument("--daily", type=int, default=7)
    retention.add_argument("--weekly", type=int, default=4)
    retention.add_argument("--monthly", type=int, default=6)
    retention.add_argument("--apply", action="store_true")

    health = subparsers.add_parser(
        "health",
        help="Comprobar que existe una copia autenticada y reciente.",
    )
    health.add_argument("--backup-root", type=Path, required=True)
    health.add_argument("--max-age-hours", type=int, default=36)
    return parser


def main() -> int:
    args = _parser().parse_args()
    database_url = os.environ.get("DJANGO_DATABASE_URL", "")
    integrity_key = os.environ.get("AGENDA_BACKUP_HMAC_KEY", "")
    try:
        if not integrity_key:
            raise BackupError("Define AGENDA_BACKUP_HMAC_KEY para operar con copias auténticas.")
        if args.command == "backup":
            backup_dir = create_backup(
                database_url=database_url,
                media_root=args.media_root,
                backup_root=args.backup_root,
                pg_dump_executable=args.pg_dump,
                integrity_key=integrity_key,
            )
            print(backup_dir)
        elif args.command == "verify":
            verify_backup(
                args.backup_dir,
                integrity_key=integrity_key,
                require_authenticity=True,
            )
            print("Copia verificada.")
        elif args.command == "restore":
            rollback_dir = restore_backup(
                database_url=database_url,
                backup_dir=args.backup_dir,
                media_target=args.media_target,
                confirm_restore=args.confirm_restore,
                replace_media=args.replace_media,
                pg_restore_executable=args.pg_restore,
                integrity_key=integrity_key,
                require_authenticity=True,
            )
            print("Restauración completada.")
            if rollback_dir:
                print(f"Media anterior conservada en: {rollback_dir}")
        elif args.command == "retention":
            plan = build_retention_plan(
                backup_root=args.backup_root,
                integrity_key=integrity_key,
                daily=args.daily,
                weekly=args.weekly,
                monthly=args.monthly,
            )
            if args.apply:
                apply_retention_plan(
                    plan,
                    backup_root=args.backup_root,
                    integrity_key=integrity_key,
                )
            action = "eliminadas" if args.apply else "eliminables"
            print(f"Copias conservadas: {len(plan.keep)}. Copias {action}: {len(plan.remove)}.")
        else:
            check_backup_freshness(
                backup_root=args.backup_root,
                integrity_key=integrity_key,
                max_age_hours=args.max_age_hours,
            )
            print("Copia reciente, autenticada y verificada.")
    except (
        BackupError,
        ImproperlyConfigured,
        subprocess.CalledProcessError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
