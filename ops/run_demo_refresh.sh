#!/usr/bin/env bash
# Orquestador root del reinicio nocturno de la demo académica de AgendaSalon.
# Este fichero se instala como /usr/local/sbin/agendasalon-demo-refresh (root:root 0750).

set -Eeuo pipefail
IFS=$'\n\t'
umask 0077

readonly APP_USER="agendasalon"
readonly APP_GROUP="www-data"
readonly APP_ROOT="/var/www/agendasalon/app"
readonly PYTHON="/var/www/agendasalon/.venv/bin/python"
readonly MANAGE_PY="${APP_ROOT}/manage.py"
readonly BACKUP_ROOT="/var/backups/agendasalon-demo-canonical"
readonly MEDIA_ROOT_CANONICAL="/var/www/agendasalon/shared/media"
readonly STATE_DIR_CANONICAL="/var/lib/agendasalon"
readonly STATE_FILE_CANONICAL="${STATE_DIR_CANONICAL}/demo-refresh.state"
readonly LOCK_FILE="/run/lock/agendasalon-demo-refresh.lock"
readonly BACKUP_AUTH_FILE="/run/lock/agendasalon-demo-backup-authorized"
readonly REARM_AUTH_FILE="/run/lock/agendasalon-demo-rearm-authorized"
readonly GUNICORN_UNIT="gunicorn-agendasalon.service"
readonly GUNICORN_SOCKET="/run/agendasalon/gunicorn.sock"
readonly BACKUP_TIMER_UNIT="backup-agendasalon.timer"
readonly EMAIL_TIMER_UNIT="agendasalon-email.timer"
readonly -a DISABLED_TIMER_UNITS=(
  "${EMAIL_TIMER_UNIT}"
  "${BACKUP_TIMER_UNIT}"
)

readonly -a TIMER_UNITS=(
  "agendasalon-registration-purge.timer"
  "agendasalon-session-cleanup.timer"
  "check-agendasalon-backup.timer"
)
readonly -a ONESHOT_UNITS=(
  "agendasalon-email.service"
  "agendasalon-registration-purge.service"
  "agendasalon-session-cleanup.service"
  "backup-agendasalon.service"
  "check-agendasalon-backup.service"
)
readonly -a TIMER_TRIGGERED_ONESHOT_UNITS=(
  "agendasalon-registration-purge.service"
  "agendasalon-session-cleanup.service"
  "check-agendasalon-backup.service"
)
readonly -a MANAGED_UNITS=(
  "${GUNICORN_UNIT}"
  "${TIMER_UNITS[@]}"
  "${DISABLED_TIMER_UNITS[@]}"
  "${ONESHOT_UNITS[@]}"
)

declare -A WAS_ACTIVE=()
declare -A WAS_ENABLED=()
RUN_ID=""
BASE_DATE=""
LATEST_BACKUP=""
MEDIA_PARENT=""
MEDIA_QUARANTINE=""
STATE_TMP=""
BACKUP_AUTH_TMP=""
REARM_AUTH_TMP=""
MEDIA_UID=""
MEDIA_GID=""
MEDIA_MODE=""
STATE_CAPTURED=0
STATE_WRITTEN=0
MEDIA_MOVED=0
REFRESH_ATTEMPTED=0
REFRESH_COMMAND_SUCCEEDED=0
RECONCILIATION_RESULT="pending"
RECONCILED_FINGERPRINT=""
RECONCILIATION_FINISHED=0
EXIT_CLEANUP_DEADLINE=0
PROCESS_LOCK_HELD=0

log() {
  # Los mensajes son deliberadamente técnicos y no incluyen datos de clientes ni secretos.
  printf 'AgendaSalon demo refresh: %s\n' "$1"
}

fail() {
  log "ERROR: $1"
  return 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "falta una herramienta operativa obligatoria"
}

unit_property() {
  systemctl show --property="$2" --value "$1"
}

unit_is_loaded() {
  [[ "$(unit_property "$1" LoadState)" == "loaded" ]]
}

wait_unit_inactive() {
  local unit="$1"
  local deadline=$((SECONDS + 900))
  if (( EXIT_CLEANUP_DEADLINE > 0 && EXIT_CLEANUP_DEADLINE < deadline )); then
    deadline="${EXIT_CLEANUP_DEADLINE}"
  fi
  local state
  while true; do
    state="$(unit_property "$unit" ActiveState)"
    case "$state" in
      inactive|failed)
        return 0
        ;;
      active|activating|deactivating|reloading)
        if (( SECONDS >= deadline )); then
          fail "un proceso en segundo plano no terminó dentro del plazo seguro"
          return 1
        fi
        sleep 1
        ;;
      *)
        fail "una unidad presentó un estado operativo desconocido"
        return 1
        ;;
    esac
  done
}

wait_unit_active() {
  local unit="$1"
  local deadline=$((SECONDS + 60))
  until systemctl is-active --quiet "$unit"; do
    if (( SECONDS >= deadline )); then
      fail "un servicio no recuperó su estado operativo"
      return 1
    fi
    sleep 1
  done
}

wait_socket_present() {
  local deadline=$((SECONDS + 60))
  until [[ -S "${GUNICORN_SOCKET}" ]]; do
    if (( SECONDS >= deadline )); then
      fail "el socket de la aplicación no reapareció"
      return 1
    fi
    sleep 1
  done
}

wait_socket_absent() {
  local deadline=$((SECONDS + 60))
  while [[ -e "${GUNICORN_SOCKET}" ]]; do
    if (( SECONDS >= deadline )); then
      fail "el socket de la aplicación continuó disponible durante la ventana fría"
      return 1
    fi
    sleep 1
  done
}

validate_safe_path() {
  local path="$1"
  local expected="$2"
  [[ -d "$path" ]] || return 1
  [[ ! -L "$path" ]] || return 1
  [[ "$(realpath -e -- "$path")" == "$expected" ]]
}

validate_installed_script() {
  local script_path
  script_path="$(realpath -e -- "$0")"
  [[ "$script_path" == "/usr/local/sbin/agendasalon-demo-refresh" ]] ||
    fail "el orquestador no se está ejecutando desde su ruta root canónica"
  [[ "$(stat -c '%u' -- "$script_path")" == "0" ]] ||
    fail "el orquestador instalado no pertenece a root"
  local mode
  mode="$(stat -c '%a' -- "$script_path")"
  (( (8#${mode} & 8#022) == 0 )) ||
    fail "el orquestador instalado es escribible por grupo u otros"
}

live_gunicorn_environment_is_safe() {
  local pid="$1"
  # /proc/<pid>/environ aplica el control ptrace. La lectura se hace con el
  # mismo UID que Gunicorn para no conceder CAP_SYS_PTRACE al orquestador root.
  # El helper no imprime ni devuelve valores: solo compara dos flags públicos.
  runuser -u "${APP_USER}" -g "${APP_GROUP}" -- "${PYTHON}" -I -c '
from pathlib import Path
import sys

pid = sys.argv[1]
expected = {
    b"AGENDA_TRANSACTIONAL_EMAIL_ENABLED": b"0",
    b"AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL": b"0",
}
try:
    records = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
except OSError:
    raise SystemExit(1)
for name, value in expected.items():
    prefix = name + b"="
    matches = [record for record in records if record.startswith(prefix)]
    if matches != [prefix + value]:
        raise SystemExit(1)
' "${pid}" >/dev/null 2>&1
}

validate_live_gunicorn_email_barrier() {
  local main_pid main_pid_after
  main_pid="$(unit_property "${GUNICORN_UNIT}" MainPID)"
  [[ "${main_pid}" =~ ^[1-9][0-9]*$ ]] ||
    fail "Gunicorn no expone un proceso principal valido"

  live_gunicorn_environment_is_safe "${main_pid}" ||
    fail "el entorno vivo de Gunicorn no conserva la barrera de correo esperada"

  main_pid_after="$(unit_property "${GUNICORN_UNIT}" MainPID)"
  [[ "${main_pid_after}" == "${main_pid}" ]] ||
    fail "Gunicorn cambio de proceso durante la comprobacion de correo"
}

preflight() {
  [[ "$(id -u)" == "0" ]] || fail "la operación requiere root"

  local command
  for command in basename cat chmod chown curl date dirname env find flock \
    grep id install mktemp mv pg_restore realpath rmdir rm runuser \
    sed sleep sort stat sync systemctl test; do
    require_command "$command"
  done

  validate_installed_script
  [[ -x "${PYTHON}" && -f "${MANAGE_PY}" ]] || fail "el runtime de la aplicación no está disponible"
  [[ "$(realpath -e -- "${APP_ROOT}")" == "${APP_ROOT}" ]] || fail "la ruta de la aplicación no coincide"
  [[ -d "${BACKUP_ROOT}" && ! -L "${BACKUP_ROOT}" ]] || fail "el almacén canónico de copias no es válido"
  [[ "$(realpath -e -- "${BACKUP_ROOT}")" == "${BACKUP_ROOT}" ]] ||
    fail "el almacén canónico de copias no coincide con su ruta dedicada"
  runuser -u "${APP_USER}" -g "${APP_GROUP}" -- test -r "${BACKUP_ROOT}" ||
    fail "la aplicación no puede leer el almacén canónico de copias"
  runuser -u "${APP_USER}" -g "${APP_GROUP}" -- test -x "${BACKUP_ROOT}" ||
    fail "la aplicación no puede recorrer el almacén canónico de copias"

  [[ "${AGENDA_DEMO_REFRESH_ENABLED:-}" == "1" ]] || fail "la regeneración no está habilitada"
  [[ "${AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL:-}" == "1" ]] || fail "la barrera de correo no está activa"
  [[ "${DJANGO_SETTINGS_MODULE:-}" == "config.settings.prod" ]] || fail "los ajustes no son los de producción"
  [[ -n "${AGENDA_DEMO_EXPECTED_DATABASE_NAME:-}" ]] || fail "falta la identidad esperada de la base de datos"
  [[ -n "${AGENDA_DEMO_EXPECTED_DATABASE_USER:-}" ]] || fail "falta el usuario esperado de la base de datos"
  [[ -n "${AGENDA_DEMO_EXPECTED_DATABASE_HOST:-}" ]] || fail "falta el host esperado de la base de datos"
  [[ "${AGENDA_DEMO_EXPECTED_DATABASE_PORT:-}" == "5432" ]] ||
    fail "el puerto esperado de PostgreSQL debe ser exactamente 5432"
  [[ "${AGENDA_DEMO_EXPECTED_PLATFORM_WEBSITE:-}" == "https://agendasalon.brvsoftwarestudio.com" ]] ||
    fail "el sitio esperado no es la demo académica canónica"
  [[ "${AGENDA_DEMO_EXPECTED_MEDIA_ROOT:-}" == "${MEDIA_ROOT_CANONICAL}" ]] ||
    fail "la ruta de medios esperada no es la canónica"
  [[ "${AGENDA_DEMO_QUIESCENCE_MARKER:-}" == "${STATE_FILE_CANONICAL}" ]] ||
    fail "la ruta del estado de quiescencia no es la canónica"
  [[ -n "${DJANGO_DATABASE_URL:-}" && -n "${AGENDA_BACKUP_HMAC_KEY:-}" ]] ||
    fail "faltan credenciales operativas de copia"

  validate_safe_path "${AGENDA_DEMO_EXPECTED_MEDIA_ROOT}" "${MEDIA_ROOT_CANONICAL}" ||
    fail "el directorio de medios no supera la validación de ruta"
  MEDIA_PARENT="$(realpath -e -- "$(dirname -- "${MEDIA_ROOT_CANONICAL}")")"
  [[ "${MEDIA_PARENT}" == "/var/www/agendasalon/shared" ]] || fail "el padre de medios no es el esperado"
  [[ ! -e "${STATE_FILE_CANONICAL}" && ! -L "${STATE_FILE_CANONICAL}" ]] ||
    fail "existe un estado duradero pendiente de reconciliación"
  if [[ -d "${STATE_DIR_CANONICAL}" ]] && find "${STATE_DIR_CANONICAL}" \
    -mindepth 1 -maxdepth 1 -name 'demo-refresh.state.tmp.*' -print -quit | grep -q .; then
    fail "existe un estado temporal pendiente de revisión"
  fi
  [[ ! -e "${BACKUP_AUTH_FILE}" && ! -L "${BACKUP_AUTH_FILE}" ]] ||
    fail "existe una autorización de copia canónica pendiente de revisión"
  [[ ! -e "${REARM_AUTH_FILE}" && ! -L "${REARM_AUTH_FILE}" ]] ||
    fail "existe una autorización de rearme pendiente de revisión"
  if find "${MEDIA_PARENT}" -mindepth 1 -maxdepth 1 \
    -name '.media-refresh-quarantine-*' -print -quit | grep -q .; then
    fail "existe una cuarentena de medios pendiente de revisión"
  fi

  local unit
  for unit in "${MANAGED_UNITS[@]}" postgresql.service nginx.service; do
    unit_is_loaded "$unit" || fail "falta una unidad systemd obligatoria"
  done
  systemctl is-active --quiet postgresql.service || fail "PostgreSQL no está activo"
  systemctl is-active --quiet nginx.service || fail "Nginx no está activo"
  systemctl is-active --quiet "${GUNICORN_UNIT}" || fail "la aplicación no estaba operativa antes del reset"
  # El servicio de refresh fuerza sus propios flags. Se comprueba además el
  # entorno real de Gunicorn para que ese override no pueda ocultar un runtime
  # web capaz de enviar correo externo.
  validate_live_gunicorn_email_barrier
  for unit in "${TIMER_UNITS[@]}"; do
    systemctl is-active --quiet "$unit" || fail "un temporizador escritor no estaba activo antes del reset"
    systemctl is-enabled --quiet "$unit" || fail "un temporizador escritor no estaba habilitado antes del reset"
  done
  local disabled_timer
  for disabled_timer in "${DISABLED_TIMER_UNITS[@]}"; do
    if systemctl is-active --quiet "${disabled_timer}" ||
      systemctl is-enabled --quiet "${disabled_timer}"; then
      fail "un temporizador incompatible debe permanecer deshabilitado en la demo regenerable"
    fi
  done
  for unit in "${ONESHOT_UNITS[@]}"; do
    if systemctl is-failed --quiet "${unit}"; then
      fail "una unidad auxiliar arrastra un fallo previo sin revisar"
    fi
  done
  [[ -S "${GUNICORN_SOCKET}" ]] || fail "falta el socket previo de la aplicación"
  curl --silent --show-error --fail --max-time 20 \
    --unix-socket "${GUNICORN_SOCKET}" \
    --header "Host: agendasalon.brvsoftwarestudio.com" \
    "http://localhost/" >/dev/null || fail "la comprobación previa de la aplicación ha fallado"

  [[ "${REFRESH_BASE_DATE:-}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] ||
    fail "la fecha base no tiene un formato seguro"
  date --date="${REFRESH_BASE_DATE}" '+%F' | grep -qx "${REFRESH_BASE_DATE}" ||
    fail "la fecha base no es válida"
}

capture_operational_state() {
  local unit state
  for unit in "${GUNICORN_UNIT}" "${TIMER_UNITS[@]}"; do
    state="$(systemctl is-active "$unit" 2>/dev/null || true)"
    WAS_ACTIVE["$unit"]="$state"
    state="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    WAS_ENABLED["$unit"]="$state"
  done
  STATE_CAPTURED=1
}

quiesce_application() {
  log "iniciando ventana fría controlada"
  systemctl stop "${TIMER_UNITS[@]}" "${DISABLED_TIMER_UNITS[@]}"

  local unit
  for unit in "${ONESHOT_UNITS[@]}"; do
    wait_unit_inactive "$unit"
  done
  # Cancela también cualquier trabajo de arranque que hubiera quedado en cola.
  systemctl stop "${ONESHOT_UNITS[@]}"
  for unit in "${ONESHOT_UNITS[@]}"; do
    wait_unit_inactive "$unit"
  done

  systemctl stop "${GUNICORN_UNIT}"
  wait_unit_inactive "${GUNICORN_UNIT}"
  wait_socket_absent

  for unit in "${TIMER_UNITS[@]}" "${DISABLED_TIMER_UNITS[@]}" "${ONESHOT_UNITS[@]}" "${GUNICORN_UNIT}"; do
    systemctl is-active --quiet "$unit" && fail "una unidad escritora continuó activa"
  done
}

latest_canonical_backup() {
  local candidate
  candidate="$(
    find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d \
      -name 'agendasalon-*' -printf '%T@ %p\n' |
      sort -rn | sed -n '1{s/^[^ ]* //;p;}'
  )"
  [[ -n "${candidate}" && ! -L "${candidate}" ]] || return 1
  realpath -e -- "${candidate}"
}

verify_canonical_backup() {
  local backup_dir="$1"
  [[ -d "${backup_dir}" && ! -L "${backup_dir}" ]] || return 1
  backup_dir="$(realpath -e -- "${backup_dir}")" || return 1
  [[ "$(dirname -- "${backup_dir}")" == "$(realpath -e -- "${BACKUP_ROOT}")" ]] ||
    return 1

  (
    cd "${APP_ROOT}"
    runuser -u "${APP_USER}" -g "${APP_GROUP}" -- \
      "${PYTHON}" -m ops.backup_restore verify --backup-dir "${backup_dir}"
  ) >/dev/null || return 1
  runuser -u "${APP_USER}" -g "${APP_GROUP}" -- \
    pg_restore --list "${backup_dir}/database.dump" >/dev/null || return 1
  sync -f "${backup_dir}" || return 1
  sync -f "${BACKUP_ROOT}" || return 1
}

select_and_verify_canonical_fallback() {
  LATEST_BACKUP="$(latest_canonical_backup)" ||
    fail "no existe un fallback canónico en el almacén exclusivo de la demo"
  verify_canonical_backup "${LATEST_BACKUP}" ||
    fail "el fallback canónico no supera autenticidad, lectura y sincronización"
  log "fallback canónico previo autenticado y sincronizado"
}

authorize_canonical_backup() {
  [[ ! -e "${BACKUP_AUTH_FILE}" && ! -L "${BACKUP_AUTH_FILE}" ]] || return 1
  BACKUP_AUTH_TMP="$(mktemp "${BACKUP_AUTH_FILE}.tmp.XXXXXX")" || return 1
  printf '%s\n' "${RUN_ID}" >"${BACKUP_AUTH_TMP}" || return 1
  chown root:"${APP_GROUP}" "${BACKUP_AUTH_TMP}" || return 1
  chmod 0640 "${BACKUP_AUTH_TMP}" || return 1
  sync -f "${BACKUP_AUTH_TMP}" || return 1
  mv -T -- "${BACKUP_AUTH_TMP}" "${BACKUP_AUTH_FILE}" || return 1
  BACKUP_AUTH_TMP=""
  sync -f "$(dirname -- "${BACKUP_AUTH_FILE}")" || return 1
}

revoke_canonical_backup_authorization() {
  if [[ -e "${BACKUP_AUTH_FILE}" || -L "${BACKUP_AUTH_FILE}" ]]; then
    [[ -f "${BACKUP_AUTH_FILE}" && ! -L "${BACKUP_AUTH_FILE}" ]] || return 1
    [[ "$(stat -c '%u' -- "${BACKUP_AUTH_FILE}")" == "0" ]] || return 1
    [[ "$(cat -- "${BACKUP_AUTH_FILE}")" == "${RUN_ID}" ]] || return 1
    rm -f -- "${BACKUP_AUTH_FILE}" || return 1
    sync -f "$(dirname -- "${BACKUP_AUTH_FILE}")" || return 1
  fi
}

authorize_runtime_rearm() {
  (( PROCESS_LOCK_HELD == 1 )) || return 1
  [[ ! -e "${REARM_AUTH_FILE}" && ! -L "${REARM_AUTH_FILE}" ]] || return 1
  REARM_AUTH_TMP="$(mktemp "${REARM_AUTH_FILE}.tmp.XXXXXX")" || return 1
  printf '%s\n' "${RUN_ID}" >"${REARM_AUTH_TMP}" || return 1
  chown root:"${APP_GROUP}" "${REARM_AUTH_TMP}" || return 1
  chmod 0640 "${REARM_AUTH_TMP}" || return 1
  sync -f "${REARM_AUTH_TMP}" || return 1
  mv -T -- "${REARM_AUTH_TMP}" "${REARM_AUTH_FILE}" || return 1
  REARM_AUTH_TMP=""
  sync -f "$(dirname -- "${REARM_AUTH_FILE}")" || return 1
}

revoke_runtime_rearm_authorization() {
  if [[ -e "${REARM_AUTH_FILE}" || -L "${REARM_AUTH_FILE}" ]]; then
    [[ -f "${REARM_AUTH_FILE}" && ! -L "${REARM_AUTH_FILE}" ]] || return 1
    [[ "$(stat -c '%u' -- "${REARM_AUTH_FILE}")" == "0" ]] || return 1
    [[ "$(cat -- "${REARM_AUTH_FILE}")" == "${RUN_ID}" ]] || return 1
    rm -f -- "${REARM_AUTH_FILE}" || return 1
    sync -f "$(dirname -- "${REARM_AUTH_FILE}")" || return 1
  fi
}

create_and_verify_clean_backup() {
  local started_epoch candidate backup_status=0 revoke_status=0
  started_epoch="$(date '+%s')"
  log "creando la nueva copia canónica con la demo ya limpia"
  authorize_canonical_backup || return 1
  systemctl reset-failed backup-agendasalon.service >/dev/null 2>&1 || true
  systemctl start backup-agendasalon.service || backup_status=$?
  revoke_canonical_backup_authorization || revoke_status=$?
  (( backup_status == 0 && revoke_status == 0 )) || return 1
  [[ "$(unit_property backup-agendasalon.service Result)" == "success" ]] || return 1
  [[ "$(unit_property backup-agendasalon.service ExecMainStatus)" == "0" ]] || return 1

  candidate="$(latest_canonical_backup)" || return 1
  (( $(stat -c '%Y' -- "${candidate}") >= started_epoch - 5 )) || return 1
  verify_canonical_backup "${candidate}" || return 1
  LATEST_BACKUP="${candidate}"
  log "nueva copia canónica limpia autenticada y sincronizada"
}

prepare_media_quarantine() {
  RUN_ID="$(cat /proc/sys/kernel/random/uuid)"
  [[ "${RUN_ID}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] ||
    fail "no se pudo generar un identificador de ejecución seguro"
  MEDIA_QUARANTINE="${MEDIA_PARENT}/.media-refresh-quarantine-${RUN_ID}"
  [[ ! -e "${MEDIA_QUARANTINE}" && ! -L "${MEDIA_QUARANTINE}" ]] ||
    fail "la cuarentena de esta ejecución ya existe"

  MEDIA_UID="$(stat -c '%u' -- "${MEDIA_ROOT_CANONICAL}")"
  MEDIA_GID="$(stat -c '%g' -- "${MEDIA_ROOT_CANONICAL}")"
  MEDIA_MODE="$(stat -c '%a' -- "${MEDIA_ROOT_CANONICAL}")"
  (( (8#${MEDIA_MODE} & 8#002) == 0 )) || fail "el directorio de medios es escribible por otros"
  runuser -u "${APP_USER}" -g "${APP_GROUP}" -- test -r "${MEDIA_ROOT_CANONICAL}" ||
    fail "la aplicación no puede leer el directorio de medios"
  runuser -u "${APP_USER}" -g "${APP_GROUP}" -- test -w "${MEDIA_ROOT_CANONICAL}" ||
    fail "la aplicación no puede escribir en el directorio de medios"
  runuser -u "${APP_USER}" -g "${APP_GROUP}" -- test -x "${MEDIA_ROOT_CANONICAL}" ||
    fail "la aplicación no puede recorrer el directorio de medios"
}

quarantine_media() {
  mv -T -- "${MEDIA_ROOT_CANONICAL}" "${MEDIA_QUARANTINE}"
  MEDIA_MOVED=1
  sync -f "${MEDIA_PARENT}"
  install -d -o "${MEDIA_UID}" -g "${MEDIA_GID}" -m "${MEDIA_MODE}" "${MEDIA_ROOT_CANONICAL}"
  [[ -z "$(find "${MEDIA_ROOT_CANONICAL}" -mindepth 1 -print -quit)" ]] ||
    fail "el directorio de medios nuevo no está vacío"
  sync -f "${MEDIA_ROOT_CANONICAL}"
  sync -f "${MEDIA_PARENT}"
}

write_durable_state() {
  if [[ -e "${STATE_DIR_CANONICAL}" || -L "${STATE_DIR_CANONICAL}" ]]; then
    [[ -d "${STATE_DIR_CANONICAL}" && ! -L "${STATE_DIR_CANONICAL}" ]] ||
      fail "la ruta de estado duradero no es un directorio seguro"
  fi
  install -d -o root -g "${APP_GROUP}" -m 0750 "${STATE_DIR_CANONICAL}"
  [[ "$(realpath -e -- "${STATE_DIR_CANONICAL}")" == "${STATE_DIR_CANONICAL}" ]] ||
    fail "la ruta de estado duradero no coincide con la canónica"
  STATE_TMP="$(mktemp "${STATE_FILE_CANONICAL}.tmp.XXXXXX")"
  {
    printf 'run_id=%s\n' "${RUN_ID}"
    printf 'created_at=%s\n' "$(date '+%s')"
    printf 'backup_dir=%s\n' "${LATEST_BACKUP}"
    printf 'media_quarantine=%s\n' "${MEDIA_QUARANTINE}"
    printf 'media_root=%s\n' "${MEDIA_ROOT_CANONICAL}"
  } >"${STATE_TMP}"
  chown root:"${APP_GROUP}" "${STATE_TMP}"
  chmod 0640 "${STATE_TMP}"
  sync -f "${STATE_TMP}"
  mv -T -- "${STATE_TMP}" "${STATE_FILE_CANONICAL}"
  STATE_TMP=""
  sync -f "${STATE_DIR_CANONICAL}"
  STATE_WRITTEN=1
}

run_refresh() {
  log "regenerando el escenario académico"
  REFRESH_ATTEMPTED=1
  (
    cd "${APP_ROOT}"
    runuser -u "${APP_USER}" -g "${APP_GROUP}" -- env \
      DJANGO_SETTINGS_MODULE=config.settings.prod \
      AGENDA_DEMO_REFRESH_ENABLED=1 \
      AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL=1 \
      AGENDA_DEMO_REFRESH_RUN_ID="${RUN_ID}" \
      AGENDA_DEMO_QUIESCENCE_MARKER="${STATE_FILE_CANONICAL}" \
      AGENDA_DEMO_EXPECTED_DATABASE_NAME="${AGENDA_DEMO_EXPECTED_DATABASE_NAME}" \
      AGENDA_DEMO_EXPECTED_DATABASE_USER="${AGENDA_DEMO_EXPECTED_DATABASE_USER}" \
      AGENDA_DEMO_EXPECTED_DATABASE_HOST="${AGENDA_DEMO_EXPECTED_DATABASE_HOST}" \
      AGENDA_DEMO_EXPECTED_DATABASE_PORT="${AGENDA_DEMO_EXPECTED_DATABASE_PORT}" \
      AGENDA_DEMO_EXPECTED_PLATFORM_WEBSITE="${AGENDA_DEMO_EXPECTED_PLATFORM_WEBSITE}" \
      AGENDA_DEMO_EXPECTED_MEDIA_ROOT="${MEDIA_ROOT_CANONICAL}" \
      AGENDA_TRANSACTIONAL_EMAIL_ENABLED=0 \
      "${PYTHON}" "${MANAGE_PY}" refresh_demo \
        --confirm-full-reset \
        --base-date "${BASE_DATE}" \
        --settings=config.settings.prod
  )
  REFRESH_COMMAND_SUCCEEDED=1
}

parse_receipt_payload() {
  local payload="$1"
  # El orquestador es root, pero el runtime de la aplicación no debe ejecutarse
  # nunca con esos privilegios. El modo aislado evita además configuración de
  # usuario y variables Python heredadas durante el parseo del recibo.
  printf '%s' "${payload}" | runuser -u "${APP_USER}" -g "${APP_GROUP}" -- \
    "${PYTHON}" -I -c '
import json
import re
import sys

expected_run_id = sys.argv[1]
try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeError):
    raise SystemExit(3)
if not isinstance(payload, dict) or payload.get("run_id") != expected_run_id:
    raise SystemExit(3)
committed = payload.get("committed")
if committed is False and set(payload) == {"committed", "run_id"}:
    print("absent")
    raise SystemExit(0)
if committed is not True or set(payload) != {
    "base_date", "committed", "completed_at", "fingerprint", "run_id"
}:
    raise SystemExit(3)
fingerprint = payload.get("fingerprint")
if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
    raise SystemExit(3)
if not isinstance(payload.get("base_date"), str) or not payload["base_date"]:
    raise SystemExit(3)
if not isinstance(payload.get("completed_at"), str) or not payload["completed_at"]:
    raise SystemExit(3)
print(f"commit:{fingerprint}")
' "${RUN_ID}"
}

query_refresh_receipt() {
  local known_failure="$1"
  local output="" parsed="" query_status=0 parse_status=0
  RECONCILIATION_RESULT="indeterminate"
  RECONCILED_FINGERPRINT=""

  if output="$(
    (
      cd "${APP_ROOT}" || exit 125
      runuser -u "${APP_USER}" -g "${APP_GROUP}" -- env \
        DJANGO_SETTINGS_MODULE=config.settings.prod \
        AGENDA_DEMO_EXPECTED_DATABASE_PORT="${AGENDA_DEMO_EXPECTED_DATABASE_PORT}" \
        "${PYTHON}" "${MANAGE_PY}" check_demo_refresh_receipt \
          --run-id "${RUN_ID}" \
          --settings=config.settings.prod
    ) 2>/dev/null
  )"; then
    query_status=0
  else
    query_status=$?
  fi
  if (( query_status != 0 )); then
    log "ERROR: PostgreSQL no pudo confirmar ni descartar el recibo del refresco"
    return 1
  fi

  if parsed="$(parse_receipt_payload "${output}" 2>/dev/null)"; then
    parse_status=0
  else
    parse_status=$?
  fi
  if (( parse_status != 0 )); then
    log "ERROR: la respuesta del recibo no cumple el contrato operativo"
    return 1
  fi

  case "${parsed}" in
    commit:*)
      RECONCILED_FINGERPRINT="${parsed#commit:}"
      RECONCILIATION_RESULT="commit"
      return 0
      ;;
    absent)
      if (( known_failure == 1 )); then
        RECONCILIATION_RESULT="rollback"
        return 0
      fi
      log "ERROR: el comando declaró éxito, pero PostgreSQL no conserva un recibo"
      return 1
      ;;
    *)
      log "ERROR: el resultado del recibo es ambiguo"
      return 1
      ;;
  esac
}

delete_tree_contents() {
  local target="$1"
  [[ -d "$target" && ! -L "$target" ]] || return 1
  [[ "$(dirname -- "$target")" == "${MEDIA_PARENT}" ]] || return 1
  [[ "$(basename -- "$target")" == .media-refresh-quarantine-* ]] || return 1
  find "$target" -xdev -mindepth 1 -depth -delete
  rmdir -- "$target"
}

restore_media_after_failure() {
  if [[ -d "${MEDIA_QUARANTINE}" && ! -L "${MEDIA_QUARANTINE}" ]]; then
    if [[ -e "${MEDIA_ROOT_CANONICAL}" || -L "${MEDIA_ROOT_CANONICAL}" ]]; then
      [[ -d "${MEDIA_ROOT_CANONICAL}" && ! -L "${MEDIA_ROOT_CANONICAL}" ]] || return 1
      find "${MEDIA_ROOT_CANONICAL}" -xdev -mindepth 1 -depth -delete || return 1
      rmdir -- "${MEDIA_ROOT_CANONICAL}" || return 1
    fi
    mv -T -- "${MEDIA_QUARANTINE}" "${MEDIA_ROOT_CANONICAL}" || return 1
    sync -f "${MEDIA_ROOT_CANONICAL}" || return 1
    sync -f "${MEDIA_PARENT}" || return 1
    MEDIA_MOVED=0
    validate_safe_path "${MEDIA_ROOT_CANONICAL}" "${MEDIA_ROOT_CANONICAL}"
    return
  fi
  (( MEDIA_MOVED == 0 )) || return 1
  validate_safe_path "${MEDIA_ROOT_CANONICAL}" "${MEDIA_ROOT_CANONICAL}" || return 1
  MEDIA_MOVED=0
}

commit_media_after_refresh() {
  if [[ -e "${MEDIA_QUARANTINE}" || -L "${MEDIA_QUARANTINE}" ]]; then
    [[ -d "${MEDIA_QUARANTINE}" && ! -L "${MEDIA_QUARANTINE}" ]] || return 1
    delete_tree_contents "${MEDIA_QUARANTINE}" || return 1
  fi
  validate_safe_path "${MEDIA_ROOT_CANONICAL}" "${MEDIA_ROOT_CANONICAL}" || return 1
  sync -f "${MEDIA_ROOT_CANONICAL}" || return 1
  sync -f "${MEDIA_PARENT}" || return 1
  MEDIA_MOVED=0
}

remove_durable_state() {
  [[ -f "${STATE_FILE_CANONICAL}" && ! -L "${STATE_FILE_CANONICAL}" ]] || return 1
  [[ "$(stat -c '%u' -- "${STATE_FILE_CANONICAL}")" == "0" ]] || return 1
  local mode
  mode="$(stat -c '%a' -- "${STATE_FILE_CANONICAL}")"
  (( (8#${mode} & 8#022) == 0 )) || return 1
  rm -f -- "${STATE_FILE_CANONICAL}" || return 1
  sync -f "${STATE_DIR_CANONICAL}" || return 1
  STATE_WRITTEN=0
}

reconcile_database_and_media() {
  local known_failure="$1"
  query_refresh_receipt "${known_failure}" || return 1
  case "${RECONCILIATION_RESULT}" in
    commit)
      log "PostgreSQL confirmó el commit; eliminando la cuarentena anterior"
      commit_media_after_refresh || return 1
      create_and_verify_clean_backup || return 1
      ;;
    rollback)
      log "PostgreSQL descartó el commit tras el fallo; restaurando los medios anteriores"
      restore_media_after_failure || return 1
      ;;
    *)
      return 1
      ;;
  esac
  remove_durable_state || return 1
  RECONCILIATION_FINISHED=1
}

ensure_writers_stopped() {
  local failed=0 unit
  systemctl stop "${TIMER_UNITS[@]}" "${DISABLED_TIMER_UNITS[@]}" || failed=1
  systemctl stop "${ONESHOT_UNITS[@]}" || failed=1
  systemctl stop "${GUNICORN_UNIT}" || failed=1
  for unit in "${ONESHOT_UNITS[@]}" "${GUNICORN_UNIT}"; do
    wait_unit_inactive "${unit}" || failed=1
  done
  wait_socket_absent || failed=1
  for unit in "${TIMER_UNITS[@]}" "${DISABLED_TIMER_UNITS[@]}" "${ONESHOT_UNITS[@]}" "${GUNICORN_UNIT}"; do
    if systemctl is-active --quiet "${unit}"; then
      failed=1
    fi
  done
  return "${failed}"
}

rearm_operational_state() {
  (( STATE_CAPTURED == 1 && PROCESS_LOCK_HELD == 1 )) || return 0
  local failed=0
  authorize_runtime_rearm || return 1

  if [[ "${WAS_ACTIVE[${GUNICORN_UNIT}]:-}" == "active" ]]; then
    systemctl start "${GUNICORN_UNIT}" || failed=1
    wait_unit_active "${GUNICORN_UNIT}" || failed=1
    wait_socket_present || failed=1
  fi

  if [[ "$(systemctl is-enabled "${GUNICORN_UNIT}" 2>/dev/null || true)" != \
        "${WAS_ENABLED[${GUNICORN_UNIT}]:-}" ]]; then
    log "ERROR: cambio inesperadamente el estado de habilitacion de Gunicorn"
    failed=1
  fi

  systemctl is-active --quiet postgresql.service || failed=1
  systemctl is-active --quiet nginx.service || failed=1
  return "$failed"
}

postflight_runtime() {
  systemctl is-active --quiet "${GUNICORN_UNIT}" || fail "Gunicorn no quedó activo"
  [[ -S "${GUNICORN_SOCKET}" ]] || fail "el socket de Gunicorn no quedó disponible"
  curl --silent --show-error --fail --max-time 20 \
    --unix-socket "${GUNICORN_SOCKET}" \
    --header "Host: agendasalon.brvsoftwarestudio.com" \
    "http://localhost/" >/dev/null || fail "la comprobación final de la aplicación ha fallado"

}

release_process_lock_after_runtime_rearm() {
  (( PROCESS_LOCK_HELD == 1 && STATE_CAPTURED == 1 )) || return 1
  if (( RECONCILIATION_FINISHED == 0 )); then
    (( STATE_WRITTEN == 0 && MEDIA_MOVED == 0 && REFRESH_ATTEMPTED == 0 )) ||
      return 1
  fi
  [[ ! -e "${STATE_FILE_CANONICAL}" && ! -L "${STATE_FILE_CANONICAL}" ]] || return 1
  [[ ! -e "${BACKUP_AUTH_FILE}" && ! -L "${BACKUP_AUTH_FILE}" ]] || return 1
  if find "${MEDIA_PARENT}" -mindepth 1 -maxdepth 1 \
    -name '.media-refresh-quarantine-*' -print -quit | grep -q .; then
    return 1
  fi

  # El token cubre solo el arranque y la verificación de Gunicorn mientras el
  # flock sigue retenido. Se revoca justo antes de liberar el lock; no se hace
  # ningún trabajo externo entre ambas operaciones.
  revoke_runtime_rearm_authorization || return 1
  [[ ! -e "${REARM_AUTH_FILE}" && ! -L "${REARM_AUTH_FILE}" ]] || return 1
  flock --unlock 9 || return 1
  PROCESS_LOCK_HELD=0
  STATE_CAPTURED=0
  exec 9>&- || return 1
}

restore_operational_timers_after_unlock() {
  (( PROCESS_LOCK_HELD == 0 && STATE_CAPTURED == 0 )) || return 1
  [[ ! -e "${REARM_AUTH_FILE}" && ! -L "${REARM_AUTH_FILE}" ]] || return 1
  local failed=0 unit

  # Los timers Persistent pueden lanzar su oneshot nada más activarse. Por eso
  # se restauran solo cuando el flock ya no está retenido y el guard puede
  # evaluarlos como arranques ordinarios, sin token efímero.
  for unit in "${TIMER_UNITS[@]}"; do
    if [[ "${WAS_ACTIVE[$unit]:-}" == "active" ]]; then
      systemctl start "$unit" || failed=1
    fi
  done
  for unit in "${TIMER_TRIGGERED_ONESHOT_UNITS[@]}"; do
    wait_unit_inactive "${unit}" || failed=1
    if systemctl is-failed --quiet "${unit}"; then
      failed=1
    fi
  done

  for unit in "${TIMER_UNITS[@]}"; do
    if [[ "${WAS_ACTIVE[$unit]:-}" == "active" ]]; then
      systemctl is-active --quiet "$unit" || failed=1
    fi
    if [[ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" != "${WAS_ENABLED[$unit]:-}" ]]; then
      log "ERROR: cambio inesperadamente el estado de habilitacion de un temporizador"
      failed=1
    fi
  done
  for unit in "${DISABLED_TIMER_UNITS[@]}"; do
    if systemctl is-active --quiet "${unit}" || systemctl is-enabled --quiet "${unit}"; then
      failed=1
    fi
  done
  (( failed == 0 )) || fail "el rearme posterior de temporizadores no quedo integro"
}

prepare_process_lock() {
  [[ -d "$(dirname -- "${LOCK_FILE}")" && ! -L "$(dirname -- "${LOCK_FILE}")" ]] ||
    fail "el directorio del lock no es seguro"
  if [[ ! -e "${LOCK_FILE}" && ! -L "${LOCK_FILE}" ]]; then
    ( set -o noclobber; : >"${LOCK_FILE}" ) 2>/dev/null || true
  fi
  [[ -f "${LOCK_FILE}" && ! -L "${LOCK_FILE}" ]] ||
    fail "el lock del orquestador no es un archivo regular"
  [[ "$(stat -c '%u' -- "${LOCK_FILE}")" == "0" ]] ||
    fail "el lock del orquestador no pertenece a root"
  chown root:"${APP_GROUP}" "${LOCK_FILE}"
  chmod 0640 "${LOCK_FILE}"
  sync -f "${LOCK_FILE}"
  exec 9<>"${LOCK_FILE}"
  flock --exclusive --nonblock 9 || fail "ya hay otra regeneración en curso"
  PROCESS_LOCK_HELD=1
}

on_exit() {
  local status=$?
  local cleanup_failed=0
  local needs_reconciliation=0
  local known_failure=1
  trap - EXIT INT TERM
  set +e
  # Todas las esperas de salida comparten un único plazo. Así systemd conserva
  # margen suficiente antes de TimeoutStopSec para restaurar medios y tráfico.
  EXIT_CLEANUP_DEADLINE=$((SECONDS + 480))

  if [[ -n "${STATE_TMP}" ]]; then
    if [[ -f "${STATE_TMP}" && ! -L "${STATE_TMP}" &&
          "$(dirname -- "${STATE_TMP}")" == "${STATE_DIR_CANONICAL}" &&
          "$(basename -- "${STATE_TMP}")" == demo-refresh.state.tmp.* ]]; then
      rm -f -- "${STATE_TMP}" || cleanup_failed=1
      sync -f "${STATE_DIR_CANONICAL}" || cleanup_failed=1
    else
      cleanup_failed=1
    fi
  fi

  # Las copias y purgas iniciadas por systemd viven en otras unidades. No se
  # reabre la aplicación mientras cualquiera de ellas siga ejecutándose.
  if (( STATE_CAPTURED == 1 )); then
    local unit
    for unit in "${ONESHOT_UNITS[@]}"; do
      wait_unit_inactive "${unit}" || cleanup_failed=1
    done
  fi
  if [[ -n "${BACKUP_AUTH_TMP}" ]]; then
    if [[ -f "${BACKUP_AUTH_TMP}" && ! -L "${BACKUP_AUTH_TMP}" &&
          "$(dirname -- "${BACKUP_AUTH_TMP}")" == "$(dirname -- "${BACKUP_AUTH_FILE}")" &&
          "$(basename -- "${BACKUP_AUTH_TMP}")" == agendasalon-demo-backup-authorized.tmp.* ]]; then
      rm -f -- "${BACKUP_AUTH_TMP}" || cleanup_failed=1
      sync -f "$(dirname -- "${BACKUP_AUTH_FILE}")" || cleanup_failed=1
    else
      cleanup_failed=1
    fi
  fi
  if [[ -e "${BACKUP_AUTH_FILE}" || -L "${BACKUP_AUTH_FILE}" ]]; then
    revoke_canonical_backup_authorization || cleanup_failed=1
  fi
  if [[ -n "${REARM_AUTH_TMP}" ]]; then
    if [[ -f "${REARM_AUTH_TMP}" && ! -L "${REARM_AUTH_TMP}" &&
          "$(dirname -- "${REARM_AUTH_TMP}")" == "$(dirname -- "${REARM_AUTH_FILE}")" &&
          "$(basename -- "${REARM_AUTH_TMP}")" == agendasalon-demo-rearm-authorized.tmp.* ]]; then
      rm -f -- "${REARM_AUTH_TMP}" || cleanup_failed=1
      sync -f "$(dirname -- "${REARM_AUTH_FILE}")" || cleanup_failed=1
    else
      cleanup_failed=1
    fi
  fi
  if [[ -e "${REARM_AUTH_FILE}" || -L "${REARM_AUTH_FILE}" ]]; then
    revoke_runtime_rearm_authorization || cleanup_failed=1
  fi

  if [[ -e "${STATE_FILE_CANONICAL}" || -L "${STATE_FILE_CANONICAL}" ]] ||
    (( STATE_WRITTEN == 1 || MEDIA_MOVED == 1 || REFRESH_ATTEMPTED == 1 )); then
    needs_reconciliation=1
  fi

  if (( STATE_CAPTURED == 1 && RECONCILIATION_FINISHED == 0 && needs_reconciliation == 1 )); then
    # La prioridad es cerrar todos los escritores. Si después la consulta del
    # recibo o la reconciliación de medios falla, no se vuelve a abrir tráfico.
    ensure_writers_stopped || cleanup_failed=1
    if [[ -f "${STATE_FILE_CANONICAL}" && ! -L "${STATE_FILE_CANONICAL}" && -n "${RUN_ID}" ]]; then
      (( REFRESH_COMMAND_SUCCEEDED == 1 )) && known_failure=0
      if (( cleanup_failed == 0 )); then
        reconcile_database_and_media "${known_failure}" || cleanup_failed=1
      fi
    else
      cleanup_failed=1
      log "ERROR: falta un estado duradero íntegro para reconciliar la ejecución"
    fi
  fi

  if (( STATE_CAPTURED == 1 && RECONCILIATION_FINISHED == 1 && status != 0 )) &&
    [[ "${RECONCILIATION_RESULT}" == "commit" ]]; then
    cleanup_failed=1
    log "ERROR: la verificación final no concluyó; se conserva la ventana fría"
  fi

  if (( STATE_CAPTURED == 1 && cleanup_failed == 0 )); then
    if (( needs_reconciliation == 0 || RECONCILIATION_FINISHED == 1 )); then
      rearm_operational_state || cleanup_failed=1
      (( cleanup_failed == 0 )) && postflight_runtime || cleanup_failed=1
      (( cleanup_failed == 0 )) &&
        release_process_lock_after_runtime_rearm || cleanup_failed=1
      if (( PROCESS_LOCK_HELD == 0 && STATE_CAPTURED == 0 )); then
        restore_operational_timers_after_unlock || cleanup_failed=1
      fi
    else
      cleanup_failed=1
    fi
  fi
  if (( cleanup_failed != 0 )); then
    if (( PROCESS_LOCK_HELD == 1 )); then
      # Segundo cierre best-effort: el guard de Gunicorn aporta otra barrera si
      # systemd o un operador intentan arrancarlo antes de la revisión manual.
      ensure_writers_stopped >/dev/null 2>&1 || true
      log "ERROR: estado indeterminado; servicios escritores detenidos y revision manual obligatoria"
    else
      # La base, los medios y Gunicorn ya quedaron verificados antes de soltar
      # el lock. Un fallo posterior de timer permanece visible en systemd y en
      # el journal, pero no vuelve a abrir una ventana fría sin protección.
      log "ERROR: rearme de temporizadores incompleto; el fallo queda visible para revision manual"
    fi
    status=1
  fi
  if (( status != 0 )); then
    log "ejecución abortada sin declarar éxito"
  fi
  exit "$status"
}

main() {
  prepare_process_lock

  export REFRESH_BASE_DATE="${AGENDA_DEMO_BASE_DATE:-$(TZ=Europe/Madrid date '+%F')}"
  preflight
  BASE_DATE="${REFRESH_BASE_DATE}"
  select_and_verify_canonical_fallback
  prepare_media_quarantine
  capture_operational_state
  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  write_durable_state
  quiesce_application
  quarantine_media
  run_refresh

  reconcile_database_and_media 0
  rearm_operational_state
  postflight_runtime
  release_process_lock_after_runtime_rearm
  restore_operational_timers_after_unlock
  log "regeneración completada y servicios verificados"
}

main "$@"
