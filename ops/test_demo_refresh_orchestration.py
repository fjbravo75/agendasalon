from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "run_demo_refresh.sh"
SERVICE = ROOT / "ops" / "systemd" / "agendasalon-demo-refresh.service"
TIMER = ROOT / "ops" / "systemd" / "agendasalon-demo-refresh.timer"
START_GUARD = ROOT / "ops" / "systemd" / "agendasalon-demo-start-guard"
GUNICORN_DROP_IN = (
    ROOT
    / "ops"
    / "systemd"
    / "gunicorn-agendasalon.service.d"
    / "10-demo-refresh-safety.conf"
)
BACKUP_SERVICE = ROOT / "ops" / "systemd" / "backup-agendasalon.service"
BACKUP_CHECK_SERVICE = ROOT / "ops" / "systemd" / "check-agendasalon-backup.service"
GUARDED_SERVICES = (
    "gunicorn-agendasalon.service",
    "agendasalon-email.service",
    "agendasalon-registration-purge.service",
    "agendasalon-session-cleanup.service",
    "backup-agendasalon.service",
    "check-agendasalon-backup.service",
)


class DemoRefreshScriptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def test_bash_syntax_is_valid_when_bash_is_available(self):
        if os.name == "nt":
            self.skipTest("bash -n se valida por separado en Windows")
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash no está disponible en este sistema")
        completed = subprocess.run(
            [bash, "-n", str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_fails_closed_and_takes_a_non_blocking_process_lock(self):
        self.assertIn("set -Eeuo pipefail", self.script)
        self.assertIn("umask 0077", self.script)
        self.assertIn('flock --exclusive --nonblock 9', self.script)
        self.assertIn('[[ "$(id -u)" == "0" ]]', self.script)
        self.assertNotIn("set -x", self.script)
        self.assertNotIn("printenv", self.script)

    def test_preflight_runs_before_state_capture_and_before_any_stop(self):
        main = self.script[self.script.index("main() {") :]
        self.assertLess(main.index("preflight"), main.index("capture_operational_state"))
        self.assertLess(main.index("capture_operational_state"), main.index("trap on_exit EXIT"))
        self.assertLess(main.index("trap on_exit EXIT"), main.index("quiesce_application"))
        quiesce = self.script[self.script.index("quiesce_application() {") :]
        self.assertIn('systemctl stop "${TIMER_UNITS[@]}"', quiesce)

    def test_preflight_reads_but_does_not_write_the_read_only_backup_mount(self):
        preflight = self.script[
            self.script.index("preflight() {") :
            self.script.index("capture_operational_state() {")
        ]
        self.assertIn('test -r "${BACKUP_ROOT}"', preflight)
        self.assertIn('test -x "${BACKUP_ROOT}"', preflight)
        self.assertNotIn('test -w "${BACKUP_ROOT}"', preflight)

    def test_preflight_checks_the_live_gunicorn_email_barrier(self):
        validation = self.script[
            self.script.index("live_gunicorn_environment_is_safe() {") :
            self.script.index("preflight() {")
        ]
        self.assertIn('Path(f"/proc/{pid}/environ").read_bytes()', validation)
        self.assertIn('b"AGENDA_TRANSACTIONAL_EMAIL_ENABLED": b"0"', validation)
        self.assertIn('b"AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL": b"0"', validation)
        self.assertIn(
            'runuser -u "${APP_USER}" -g "${APP_GROUP}" -- "${PYTHON}" -I -c',
            validation,
        )
        self.assertIn('>/dev/null 2>&1', validation)
        self.assertIn('unit_property "${GUNICORN_UNIT}" MainPID', validation)
        self.assertIn('[[ "${main_pid_after}" == "${main_pid}" ]]', validation)
        self.assertNotIn('[[ -r "/proc/${main_pid}/environ" ]]', validation)
        self.assertNotIn("printenv", validation)

    def test_all_five_timer_service_pairs_and_gunicorn_are_managed(self):
        stems = (
            "agendasalon-email",
            "agendasalon-registration-purge",
            "agendasalon-session-cleanup",
            "backup-agendasalon",
            "check-agendasalon-backup",
        )
        for stem in stems:
            self.assertIn(f'"{stem}.timer"', self.script)
            self.assertIn(f'"{stem}.service"', self.script)
        self.assertIn('GUNICORN_UNIT="gunicorn-agendasalon.service"', self.script)
        self.assertIn('wait_unit_inactive "$unit"', self.script)

    def test_only_a_verified_canonical_fallback_exists_before_the_reset(self):
        main = self.script[self.script.index("main() {") :]
        self.assertLess(
            main.index("select_and_verify_canonical_fallback"),
            main.index("write_durable_state"),
        )
        self.assertNotIn("create_and_verify_clean_backup", main)
        self.assertIn(
            'BACKUP_ROOT="/var/backups/agendasalon-demo-canonical"',
            self.script,
        )
        self.assertIn("ops.backup_restore verify", self.script)
        self.assertIn('pg_restore --list "${backup_dir}/database.dump"', self.script)
        self.assertIn('sync -f "${backup_dir}"', self.script)

    def test_new_backup_is_created_only_after_commit_and_clean_media(self):
        reconcile = self.script[
            self.script.index("reconcile_database_and_media() {") :
            self.script.index("ensure_writers_stopped() {")
        ]
        self.assertLess(
            reconcile.index("commit_media_after_refresh"),
            reconcile.index("create_and_verify_clean_backup"),
        )
        self.assertLess(
            reconcile.index("create_and_verify_clean_backup"),
            reconcile.index("remove_durable_state"),
        )
        self.assertIn("systemctl start backup-agendasalon.service", self.script)
        self.assertIn("started_epoch", self.script)
        self.assertNotIn("copia rutinaria previa", self.script)

    def test_durable_state_precedes_media_move_and_django(self):
        main = self.script[self.script.index("main() {") :]
        self.assertLess(main.index("write_durable_state"), main.index("quarantine_media"))
        self.assertLess(main.index("quarantine_media"), main.index("run_refresh"))
        self.assertIn(
            'STATE_FILE_CANONICAL="${STATE_DIR_CANONICAL}/demo-refresh.state"',
            self.script,
        )
        self.assertNotIn("/run/agendasalon/demo-refresh.quiescent", self.script)

    def test_durable_state_and_media_quarantine_have_a_narrow_contract(self):
        for marker_key in (
            "run_id",
            "created_at",
            "backup_dir",
            "media_quarantine",
            "media_root",
        ):
            self.assertIn(f"printf '{marker_key}=%s", self.script)
        self.assertIn('chmod 0640 "${STATE_TMP}"', self.script)
        self.assertIn('chown root:"${APP_GROUP}"', self.script)
        self.assertIn('sync -f "${STATE_TMP}"', self.script)
        self.assertIn('sync -f "${STATE_DIR_CANONICAL}"', self.script)
        self.assertIn('mv -T -- "${MEDIA_ROOT_CANONICAL}" "${MEDIA_QUARANTINE}"', self.script)
        self.assertIn('sync -f "${MEDIA_PARENT}"', self.script)
        self.assertIn("restore_media_after_failure", self.script)

    def test_django_command_runs_as_the_unprivileged_app_user(self):
        self.assertIn('runuser -u "${APP_USER}" -g "${APP_GROUP}" -- env', self.script)
        self.assertIn("refresh_demo \\", self.script)
        self.assertIn("--confirm-full-reset", self.script)
        self.assertIn('--base-date "${BASE_DATE}"', self.script)
        self.assertIn("--settings=config.settings.prod", self.script)
        self.assertIn("AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL=1", self.script)
        self.assertIn("AGENDA_TRANSACTIONAL_EMAIL_ENABLED=0", self.script)
        self.assertIn("AGENDA_DEMO_EXPECTED_DATABASE_USER", self.script)
        self.assertIn("AGENDA_DEMO_EXPECTED_DATABASE_PORT", self.script)

    def test_receipt_reconciliation_is_strictly_trivalent(self):
        query = self.script[
            self.script.index("parse_receipt_payload() {") :
            self.script.index("delete_tree_contents() {")
        ]
        self.assertIn(
            'runuser -u "${APP_USER}" -g "${APP_GROUP}" -- \\\n'
            '    "${PYTHON}" -I -c',
            query,
        )
        self.assertNotIn('| "${PYTHON}" -c', query)
        self.assertIn("check_demo_refresh_receipt", query)
        self.assertIn('RECONCILIATION_RESULT="commit"', query)
        self.assertIn('RECONCILIATION_RESULT="rollback"', query)
        self.assertIn('RECONCILIATION_RESULT="indeterminate"', query)
        self.assertIn("if (( known_failure == 1 ))", query)
        self.assertIn(
            "el comando declaró éxito, pero PostgreSQL no conserva un recibo",
            query,
        )
        self.assertIn('re.fullmatch(r"[0-9a-f]{64}", fingerprint)', query)

    def test_commit_and_rollback_finish_media_before_removing_state(self):
        reconcile = self.script[
            self.script.index("reconcile_database_and_media() {") :
            self.script.index("ensure_writers_stopped() {")
        ]
        self.assertIn("commit_media_after_refresh", reconcile)
        self.assertIn("restore_media_after_failure", reconcile)
        self.assertLess(
            reconcile.index("commit_media_after_refresh"),
            reconcile.index("remove_durable_state"),
        )
        self.assertLess(
            reconcile.index("restore_media_after_failure"),
            reconcile.index("remove_durable_state"),
        )
        self.assertIn("RECONCILIATION_FINISHED=1", reconcile)

    def test_exit_trap_reconciles_before_rearming_and_fails_closed(self):
        self.assertIn("trap on_exit EXIT", self.script)
        on_exit = self.script[self.script.index("on_exit() {") : self.script.index("main() {")]
        self.assertIn("ensure_writers_stopped", on_exit)
        self.assertIn("reconcile_database_and_media", on_exit)
        self.assertIn("rearm_operational_state", on_exit)
        self.assertIn('wait_unit_inactive "${unit}"', on_exit)
        self.assertIn("EXIT_CLEANUP_DEADLINE=$((SECONDS + 480))", on_exit)
        self.assertIn("REFRESH_COMMAND_SUCCEEDED == 1", on_exit)
        self.assertLess(
            on_exit.index("reconcile_database_and_media"),
            on_exit.index("rearm_operational_state"),
        )
        self.assertIn("servicios escritores detenidos", on_exit)
        self.assertIn("rearme de temporizadores incompleto", on_exit)
        self.assertNotIn('rm -f -- "${STATE_FILE_CANONICAL}"', on_exit)
        self.assertIn('WAS_ACTIVE["$unit"]', self.script)
        self.assertIn('WAS_ENABLED["$unit"]', self.script)

    def test_email_and_historical_backup_timers_must_stay_disabled(self):
        self.assertIn('EMAIL_TIMER_UNIT="agendasalon-email.timer"', self.script)
        self.assertIn('BACKUP_TIMER_UNIT="backup-agendasalon.timer"', self.script)
        self.assertIn('for disabled_timer in "${DISABLED_TIMER_UNITS[@]}"', self.script)
        self.assertIn("un temporizador incompatible debe permanecer deshabilitado", self.script)

    def test_media_rollback_never_accepts_a_missing_quarantine_after_move(self):
        restore = self.script[
            self.script.index("restore_media_after_failure() {") :
            self.script.index("commit_media_after_refresh() {")
        ]
        self.assertIn('mv -T -- "${MEDIA_QUARANTINE}" "${MEDIA_ROOT_CANONICAL}"', restore)
        self.assertIn("(( MEDIA_MOVED == 0 )) || return 1", restore)

    def test_signal_window_is_decided_by_receipt_before_bash_success_flag(self):
        run_refresh = self.script[
            self.script.index("run_refresh() {") :
            self.script.index("parse_receipt_payload() {")
        ]
        self.assertLess(run_refresh.index("REFRESH_ATTEMPTED=1"), run_refresh.index("refresh_demo \\"))
        self.assertGreater(
            run_refresh.index("REFRESH_COMMAND_SUCCEEDED=1"),
            run_refresh.index("refresh_demo \\"),
        )
        query = self.script[
            self.script.index("query_refresh_receipt() {") :
            self.script.index("delete_tree_contents() {")
        ]
        self.assertLess(query.index("commit:*)"), query.index("absent)"))

    def test_runtime_postflight_checks_services_socket_and_local_http(self):
        self.assertIn("postflight_runtime", self.script)
        self.assertIn('[[ -S "${GUNICORN_SOCKET}" ]]', self.script)
        self.assertIn('--unix-socket "${GUNICORN_SOCKET}"', self.script)
        self.assertIn("systemctl is-active --quiet postgresql.service", self.script)
        self.assertIn("systemctl is-active --quiet nginx.service", self.script)

    def test_rearm_token_wraps_runtime_start_while_process_lock_is_held(self):
        rearm = self.script[
            self.script.index("rearm_operational_state() {") :
            self.script.index("postflight_runtime() {")
        ]
        self.assertLess(
            rearm.index("authorize_runtime_rearm"),
            rearm.index('systemctl start "${GUNICORN_UNIT}"'),
        )
        self.assertNotIn('systemctl start "$unit"', rearm)
        self.assertNotIn("revoke_runtime_rearm_authorization", rearm)
        self.assertIn('flock --exclusive --nonblock 9', self.script)

    def test_persistent_timers_start_only_after_token_revocation_and_unlock(self):
        release = self.script[
            self.script.index("release_process_lock_after_runtime_rearm() {") :
            self.script.index("restore_operational_timers_after_unlock() {")
        ]
        self.assertLess(
            release.index("revoke_runtime_rearm_authorization"),
            release.index("flock --unlock 9"),
        )
        self.assertLess(
            release.index("flock --unlock 9"),
            release.index("PROCESS_LOCK_HELD=0"),
        )

        restore = self.script[
            self.script.index("restore_operational_timers_after_unlock() {") :
            self.script.index("prepare_process_lock() {")
        ]
        self.assertIn(
            "(( PROCESS_LOCK_HELD == 0 && STATE_CAPTURED == 0 ))",
            restore,
        )
        self.assertIn('systemctl start "$unit"', restore)
        self.assertIn('for unit in "${TIMER_TRIGGERED_ONESHOT_UNITS[@]}"', restore)

        main = self.script[self.script.index("main() {") :]
        self.assertLess(main.index("rearm_operational_state"), main.index("postflight_runtime"))
        self.assertLess(
            main.index("postflight_runtime"),
            main.index("release_process_lock_after_runtime_rearm"),
        )
        self.assertLess(
            main.index("release_process_lock_after_runtime_rearm"),
            main.index("restore_operational_timers_after_unlock"),
        )


class DemoRefreshSystemdContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = SERVICE.read_text(encoding="utf-8")
        cls.timer = TIMER.read_text(encoding="utf-8")

    def test_service_uses_root_orchestrator_and_explicit_production_guards(self):
        self.assertIn("User=root", self.service)
        self.assertIn("Group=root", self.service)
        self.assertIn("EnvironmentFile=/etc/agendasalon/demo-refresh.env", self.service)
        exec_start_lines = [
            line.removeprefix("ExecStart=")
            for line in self.service.splitlines()
            if line.startswith("ExecStart=")
        ]
        self.assertEqual(len(exec_start_lines), 1)
        self.assertEqual(
            shlex.split(exec_start_lines[0], posix=True),
            [
                "/usr/bin/env",
                "DJANGO_SETTINGS_MODULE=config.settings.prod",
                "AGENDA_DEMO_REFRESH_ENABLED=1",
                "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL=1",
                "AGENDA_TRANSACTIONAL_EMAIL_ENABLED=0",
                "/usr/local/sbin/agendasalon-demo-refresh",
            ],
        )
        for unsafe_environment_directive in (
            "Environment=DJANGO_SETTINGS_MODULE=config.settings.prod",
            "Environment=AGENDA_DEMO_REFRESH_ENABLED=1",
            "Environment=AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL=1",
            "Environment=AGENDA_TRANSACTIONAL_EMAIL_ENABLED=0",
        ):
            self.assertNotIn(unsafe_environment_directive, self.service.splitlines())
        self.assertIn("Environment=AGENDA_DEMO_EXPECTED_DATABASE_PORT=5432", self.service)
        self.assertIn(
            "Environment=AGENDA_DEMO_QUIESCENCE_MARKER=/var/lib/agendasalon/demo-refresh.state",
            self.service,
        )
        self.assertNotIn("ConditionPathIs", self.service)

    def test_service_has_defence_in_depth_hardening_and_narrow_write_paths(self):
        for directive in (
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "PrivateDevices=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "ProtectKernelTunables=true",
            "ProtectKernelModules=true",
            "ProtectControlGroups=true",
            "LockPersonality=true",
            "MemoryDenyWriteExecute=true",
        ):
            self.assertIn(directive, self.service)
        self.assertIn("RestrictSUIDSGID=true", self.service)
        self.assertIn(
            "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER "
            "CAP_SETGID CAP_SETUID",
            self.service,
        )
        ambient_capabilities = [
            line
            for line in self.service.splitlines()
            if line.startswith("AmbientCapabilities=")
        ]
        self.assertEqual(ambient_capabilities, ["AmbientCapabilities=CAP_SETUID"])
        self.assertIn(
            "ReadOnlyPaths=/etc/agendasalon /var/www/agendasalon/app "
            "/var/backups/agendasalon-demo-canonical",
            self.service,
        )
        self.assertIn(
            "ReadWritePaths=/run/lock /var/lib/agendasalon /var/www/agendasalon/shared",
            self.service,
        )
        self.assertIn("StateDirectory=agendasalon", self.service)
        self.assertNotIn("CAP_SYS_PTRACE", self.service)

    def test_timer_is_non_persistent_and_runs_at_0405_madrid(self):
        self.assertIn("OnCalendar=*-*-* 04:05:00 Europe/Madrid", self.timer)
        self.assertIn("Persistent=false", self.timer)
        self.assertIn("AccuracySec=1min", self.timer)
        self.assertIn("RandomizedDelaySec=0", self.timer)
        self.assertIn("Unit=agendasalon-demo-refresh.service", self.timer)

    def test_failed_refresh_is_journaled_and_never_retried_blindly(self):
        self.assertIn("Type=oneshot", self.service)
        self.assertIn("Restart=no", self.service)
        self.assertIn("StandardOutput=journal", self.service)
        self.assertIn("StandardError=journal", self.service)
        self.assertIn("SyslogIdentifier=agendasalon-demo-refresh", self.service)
        self.assertNotIn("OnFailure=", self.service)

    def test_start_guard_is_root_owned_at_install_and_observes_lock_and_residue(self):
        guard = START_GUARD.read_text(encoding="utf-8")
        self.assertIn(
            '"${installed_path}" == "/usr/local/libexec/agendasalon-demo-start-guard"',
            guard,
        )
        self.assertIn("la guardia no pertenece a root", guard)
        self.assertIn('flock --shared --nonblock 9', guard)
        self.assertIn('STATE_FILE="${STATE_DIR}/demo-refresh.state"', guard)
        self.assertIn(".media-refresh-quarantine-*", guard)
        self.assertIn("--canonical-backup", guard)
        self.assertIn("--runtime-rearm", guard)
        self.assertIn("la copia canónica solo se permite con Gunicorn detenido", guard)

    def test_start_guard_bash_syntax_is_valid_when_bash_is_available(self):
        if os.name == "nt":
            self.skipTest("bash -n se valida por separado en Windows")
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash no está disponible en este sistema")
        completed = subprocess.run(
            [bash, "-n", str(START_GUARD)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_gunicorn_and_every_related_oneshot_have_the_start_guard(self):
        for service_name in GUARDED_SERVICES:
            drop_in = (
                ROOT
                / "ops"
                / "systemd"
                / f"{service_name}.d"
                / "10-demo-refresh-safety.conf"
            )
            self.assertTrue(drop_in.is_file(), service_name)
            content = drop_in.read_text(encoding="utf-8")
            self.assertIn("ExecStartPre=/usr/local/libexec/agendasalon-demo-start-guard", content)
            if service_name == "backup-agendasalon.service":
                self.assertIn("--canonical-backup", content)
            if service_name == "gunicorn-agendasalon.service":
                self.assertIn("--runtime-rearm", content)

    def test_backup_and_health_units_use_only_the_canonical_demo_root(self):
        backup = BACKUP_SERVICE.read_text(encoding="utf-8")
        health = BACKUP_CHECK_SERVICE.read_text(encoding="utf-8")
        for content in (backup, health):
            self.assertIn("/var/backups/agendasalon-demo-canonical", content)
        self.assertIn("--daily 1 --weekly 1 --monthly 1 --apply", backup)
        self.assertIn("TimeoutStartSec=15min", backup)
        self.assertNotIn("--backup-root /var/backups/agendasalon ", backup)
        self.assertNotIn("--backup-root /var/backups/agendasalon ", health)


if __name__ == "__main__":
    unittest.main()
