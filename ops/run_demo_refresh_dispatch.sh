#!/usr/bin/env bash
# Despachador root estrecho de peticiones manuales de regeneración.
# Se instala como /usr/local/sbin/agendasalon-demo-refresh-dispatch (root:root 0750).

set -Eeuo pipefail
IFS=$'\n\t'
umask 0077

readonly APP_USER="agendasalon"
readonly APP_GROUP="www-data"
readonly APP_ROOT="/var/www/agendasalon/app"
readonly PYTHON="/var/www/agendasalon/.venv/bin/python"
readonly MANAGE_PY="${APP_ROOT}/manage.py"
readonly ORCHESTRATOR="/usr/local/sbin/agendasalon-demo-refresh"
readonly DISPATCHER="/usr/local/sbin/agendasalon-demo-refresh-dispatch"
readonly LOCK_FILE="/run/lock/agendasalon-demo-refresh.lock"
readonly STATE_FILE="/var/lib/agendasalon/demo-refresh.state"
readonly RUNTIME_FAILURE_MARKER="/var/lib/agendasalon/demo-refresh-runtime-failed"
readonly MEDIA_PARENT="/var/www/agendasalon/shared"
readonly GUNICORN_UNIT="gunicorn-agendasalon.service"
readonly GUNICORN_SOCKET="/run/agendasalon/gunicorn.sock"
readonly EMAIL_TIMER_UNIT="agendasalon-email.timer"
readonly DAILY_REFRESH_TIMER_UNIT="agendasalon-demo-refresh.timer"
readonly MANUAL_DISPATCH_TIMER_UNIT="agendasalon-demo-refresh-dispatch.timer"
readonly BACKUP_TIMER_UNIT="backup-agendasalon.timer"
readonly -a REQUIRED_TIMER_UNITS=(
  "agendasalon-registration-purge.timer"
  "agendasalon-session-cleanup.timer"
  "check-agendasalon-backup.timer"
)
readonly -a GUARDED_ONESHOT_UNITS=(
  "agendasalon-email.service"
  "agendasalon-registration-purge.service"
  "agendasalon-session-cleanup.service"
  "backup-agendasalon.service"
  "check-agendasalon-backup.service"
)

log() {
  printf 'AgendaSalon demo refresh dispatch: %s\n' "$1"
}

fail() {
  log "ERROR: $1"
  return 1
}

validate_root_executable() {
  local path="$1" mode
  [[ -f "${path}" && ! -L "${path}" && -x "${path}" ]] ||
    fail "falta un ejecutable root canónico"
  [[ "$(realpath -e -- "${path}")" == "${path}" ]] ||
    fail "la ruta de un ejecutable root no coincide"
  [[ "$(stat -c '%u' -- "${path}")" == "0" ]] ||
    fail "un ejecutable operativo no pertenece a root"
  mode="$(stat -c '%a' -- "${path}")"
  (( (8#${mode} & 8#022) == 0 )) ||
    fail "un ejecutable operativo es escribible por grupo u otros"
}

run_django_unlocked() {
  (
    cd "${APP_ROOT}"
    runuser -u "${APP_USER}" -g "${APP_GROUP}" -- env \
      DJANGO_SETTINGS_MODULE=config.settings.prod \
      "${PYTHON}" "${MANAGE_PY}" "$@" --settings=config.settings.prod
  )
}

run_django() {
  local lock_mode="$1"
  shift
  (
    exec 8<>"${LOCK_FILE}"
    if [[ "${lock_mode}" == "nonblocking" ]]; then
      flock --shared --nonblock --conflict-exit-code 75 8
    else
      flock --shared --timeout 2400 --conflict-exit-code 75 8
    fi
    run_django_unlocked "$@"
  )
}

timer_has_exact_state() {
  local unit="$1" expected="$2" active=0 enabled=0
  systemctl is-active --quiet "${unit}" && active=1
  systemctl is-enabled --quiet "${unit}" && enabled=1
  [[ "${active}" == "${expected}" && "${enabled}" == "${expected}" ]]
}

runtime_timers_are_safe() {
  [[ "${AGENDA_DEMO_EXPECTED_RUNTIME_TRANSACTIONAL_EMAIL_ENABLED:-}" =~ ^[01]$ ]] ||
    return 1
  [[ "${AGENDA_TRANSACTIONAL_EMAIL_ENABLED:-}" == "${AGENDA_DEMO_EXPECTED_RUNTIME_TRANSACTIONAL_EMAIL_ENABLED}" ]] ||
    return 1
  [[ "${AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL:-0}" == "0" ]] || return 1
  [[ "${AGENDA_MANUAL_DEMO_REFRESH_ENABLED:-}" == "1" ]] || return 1

  local unit
  for unit in "${REQUIRED_TIMER_UNITS[@]}" "${MANUAL_DISPATCH_TIMER_UNIT}"; do
    timer_has_exact_state "${unit}" 1 || return 1
  done
  timer_has_exact_state \
    "${EMAIL_TIMER_UNIT}" \
    "${AGENDA_DEMO_EXPECTED_RUNTIME_TRANSACTIONAL_EMAIL_ENABLED}" || return 1

  # El temporizador nocturno puede seguir activo durante la transición o estar
  # ya retirado, pero nunca puede quedar a medias entre active y enabled.
  local daily_active=0 daily_enabled=0
  systemctl is-active --quiet "${DAILY_REFRESH_TIMER_UNIT}" && daily_active=1
  systemctl is-enabled --quiet "${DAILY_REFRESH_TIMER_UNIT}" && daily_enabled=1
  [[ "${daily_active}" == "${daily_enabled}" ]] || return 1
  timer_has_exact_state "${BACKUP_TIMER_UNIT}" 0 || return 1

  for unit in "${GUARDED_ONESHOT_UNITS[@]}"; do
    systemctl is-failed --quiet "${unit}" && return 1
  done
  return 0
}

runtime_is_recoverable() {
  systemctl is-active --quiet postgresql.service &&
    systemctl is-active --quiet nginx.service &&
    systemctl is-active --quiet "${GUNICORN_UNIT}" &&
    runtime_timers_are_safe &&
    [[ -S "${GUNICORN_SOCKET}" ]] &&
    [[ ! -e "${STATE_FILE}" && ! -L "${STATE_FILE}" ]] &&
    [[ ! -e "${RUNTIME_FAILURE_MARKER}" && ! -L "${RUNTIME_FAILURE_MARKER}" ]] &&
    ! find "${MEDIA_PARENT}" -mindepth 1 -maxdepth 1 \
      -name '.media-refresh-quarantine-*' -print -quit | grep -q . &&
    curl --silent --show-error --fail --max-time 20 \
      --unix-socket "${GUNICORN_SOCKET}" \
      --header "Host: agendasalon.brvsoftwarestudio.com" \
      "http://localhost/" >/dev/null
}

recover_request() {
  local request_id="$1"
  (
    exec 8<>"${LOCK_FILE}"
    flock --shared --timeout 2400 --conflict-exit-code 75 8
    if runtime_is_recoverable; then
      run_django_unlocked finalize_demo_refresh_request \
        --request-id "${request_id}" \
        --result completed >/dev/null
      log "petición reconciliada con su recibo y un runtime íntegro"
      return 0
    fi
    run_django_unlocked finalize_demo_refresh_request \
      --request-id "${request_id}" \
      --result failed \
      --failure-code runtime_recovery_required >/dev/null
    fail "el recibo existe, pero el runtime necesita revisión manual"
  )
}

main() {
  [[ "$(id -u)" == "0" ]] || fail "la operación requiere root"
  [[ "$(realpath -e -- "$0")" == "${DISPATCHER}" ]] ||
    fail "el despachador no se ejecuta desde su ruta root canónica"
  validate_root_executable "${DISPATCHER}"
  validate_root_executable "${ORCHESTRATOR}"
  [[ -x "${PYTHON}" && -f "${MANAGE_PY}" ]] ||
    fail "el runtime de la aplicación no está disponible"

  local claim claim_status marker request_id base_date extra orchestrator_status=0
  set +e
  claim="$(run_django nonblocking claim_demo_refresh_request)"
  claim_status=$?
  set -e
  if (( claim_status == 75 )); then
    log "otra regeneración conserva la ventana exclusiva; la cola no se ha tocado"
    return 0
  fi
  (( claim_status == 0 )) || fail "no se pudo consultar la cola manual"
  if [[ "${claim}" == "IDLE" ]]; then
    exit 0
  fi
  [[ "${claim}" != *$'\n'* ]] ||
    fail "la respuesta de la cola manual contiene más de una línea"
  IFS='|' read -r marker request_id base_date extra <<<"${claim}"
  [[ ( "${marker}" == "CLAIMED" || "${marker}" == "RECOVER" ) && -z "${extra:-}" ]] ||
    fail "la respuesta de la cola manual no cumple el contrato"
  [[ "${request_id}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] ||
    fail "la petición no contiene un UUID seguro"
  [[ "${base_date}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] ||
    fail "la petición no contiene una fecha segura"
  date --date="${base_date}" '+%F' | grep -qx "${base_date}" ||
    fail "la fecha de la petición no es válida"

  if [[ "${marker}" == "RECOVER" ]]; then
    recover_request "${request_id}"
    return $?
  fi

  log "petición reclamada; iniciando el orquestador protegido"
  /usr/bin/env \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    AGENDA_DEMO_REFRESH_ENABLED=1 \
    AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL=1 \
    AGENDA_TRANSACTIONAL_EMAIL_ENABLED=0 \
    AGENDA_DEMO_REFRESH_RUN_ID="${request_id}" \
    AGENDA_DEMO_BASE_DATE="${base_date}" \
    "${ORCHESTRATOR}" || orchestrator_status=$?

  if (( orchestrator_status == 0 )); then
    run_django blocking finalize_demo_refresh_request \
      --request-id "${request_id}" \
      --result completed >/dev/null ||
      fail "el orquestador terminó, pero la petición no pudo cerrarse"
    log "petición completada y enlazada con su recibo"
    return 0
  fi

  run_django blocking finalize_demo_refresh_request \
    --request-id "${request_id}" \
    --result failed \
    --failure-code orchestrator_failed >/dev/null ||
    log "ERROR: la petición requiere reconciliación manual"
  fail "el orquestador no declaró una regeneración correcta"
}

main "$@"
