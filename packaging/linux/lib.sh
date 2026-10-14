# Shared helpers for install.sh and netxctl. Sourced, never executed.
# Portable bash 4.2+ (CentOS 7): no `wait -n`, no `${var@Q}`, no mapfile -d.

NETX_ZH=0
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in zh*|ZH*) NETX_ZH=1 ;; esac

# t "中文" "English"
t() { if [[ "${NETX_ZH}" == "1" ]]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
info() { printf '==> %s\n' "$*"; }
ok() { printf '\033[32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[WARN] %s\033[0m\n' "$*" >&2; }
err() { printf '\033[31m[ERR] %s\033[0m\n' "$*" >&2; }
die() { err "$*"; exit 1; }

NETX_USER="${NETX_USER:-netx}"

have_systemd() { [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; }
is_root() { [[ "$(id -u)" -eq 0 ]]; }

# Run a command as the netx service user (no-op when already that user).
as_netx() {
  if [[ "$(id -un)" == "${NETX_USER}" ]]; then
    "$@"
  else
    # Bundled Python drops privileges itself: minimal images may lack su/runuser.
    # It also cd's to / (the service user usually cannot read root's cwd).
    netx_admin run-as "${NETX_USER}" "$@"
  fi
}

# netx_admin <subcommand> ... — stdlib helper on the bundled Python.
# Needs NETX_PY and NETX_ADMIN_PY set by the caller.
netx_admin() { "${NETX_PY}" -I "${NETX_ADMIN_PY}" "$@"; }

env_get() { netx_admin env-get "$1" "$2" 2>/dev/null || true; }

# Export every KEY=VALUE from a .env file without eval (values may hold any char).
load_env_file() {
  local f="$1" kv
  [[ -f "${f}" ]] || return 0
  while IFS= read -r -d '' kv; do
    export "${kv%%=*}=${kv#*=}"
  done < <(netx_admin env-export "${f}")
}

# Runtime paths always live under the data root (same keys as Windows Get-NetxDataEnvMap).
data_env_pairs() {
  local d="$1"
  printf '%s\n' \
    "NETX_AUTH_MCP_TOKEN_FILE=${d}/data/auth/mcp_token" \
    "NETX_AUTH_SECRET_FILE=${d}/data/auth/jwt_secret" \
    "NETX_CREDENTIAL_SECRET_FILE=${d}/data/auth/credential_secret" \
    "NETX_SCHEDULER_HEARTBEAT_PATH=${d}/data/runtime/scheduler_heartbeat.json" \
    "NETX_RUN_DIR=${d}/data/runtime" \
    "NETX_BIZ_STATE_SPOOL_DIR=${d}/data/biz_state_spool" \
    "NETX_NE_COLLECTION_DATA_DIR=${d}/data/ne_collections" \
    "NETX_NE_EXEC_JOB_DIR=${d}/data/ne_exec_jobs" \
    "NETX_WEBCRT_DATA_DIR=${d}/data/webcrt"
}

ensure_data_dirs() {
  local d="$1" sub
  for sub in data data/auth data/runtime data/biz_state_spool data/ne_collections \
    data/ne_exec_jobs data/webcrt backups logs updates pgdata; do
    mkdir -p "${d}/${sub}"
  done
  if is_root; then
    chown -R "${NETX_USER}:${NETX_USER}" "${d}"
    chmod 0750 "${d}"
    chmod 0700 "${d}/pgdata"
    [[ -f "${d}/.env" ]] && chmod 0600 "${d}/.env"
  fi
  return 0
}

# Client-side host for health checks when bound to a wildcard.
client_host() {
  case "$1" in
    ""|0.0.0.0|"::"|"[::]") echo "127.0.0.1" ;;
    *) echo "$1" ;;
  esac
}

is_loopback() { case "$1" in 127.0.0.1|localhost|::1) return 0 ;; *) return 1 ;; esac; }

primary_ip() {
  local ip=""
  if command -v hostname >/dev/null 2>&1; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
  [[ -n "${ip}" ]] || ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  echo "${ip:-<server-ip>}"
}

# ---------------------------------------------------------------- PostgreSQL
# Callers set: PGBIN (…/pgsql/bin), PGDATA_DIR, NETX_LOG_DIR.

pg_ctl_netx() { as_netx env LC_ALL=C LANG=C "${PGBIN}/pg_ctl" "$@"; }
pg_is_running() { pg_ctl_netx -D "${PGDATA_DIR}" status >/dev/null 2>&1; }
pg_start() {
  pg_ctl_netx -D "${PGDATA_DIR}" -l "${NETX_LOG_DIR}/pg.log" -w -t 90 start >/dev/null
}
pg_stop() { pg_ctl_netx -D "${PGDATA_DIR}" -m fast -w -t 60 stop >/dev/null 2>&1 || true; }

# Major version of an initialised cluster (empty when none).
pg_data_major() { [[ -f "${PGDATA_DIR}/PG_VERSION" ]] && tr -d ' \n' <"${PGDATA_DIR}/PG_VERSION"; }

# ---------------------------------------------------------------- service control
# svc_* PREFIX DATA

svc_unit_installed() { have_systemd && [[ -f /etc/systemd/system/netx.service ]]; }
svc_pidfile() { echo "$1/data/runtime/netx.pid"; }

svc_pid_alive() {
  local pf pid
  pf="$(svc_pidfile "$1")"
  [[ -f "${pf}" ]] || return 1
  pid="$(cat "${pf}" 2>/dev/null)"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

svc_running() {
  local data="$2"
  if svc_unit_installed; then
    systemctl is-active --quiet netx.service
  else
    svc_pid_alive "${data}"
  fi
}

svc_start() {
  local prefix="$1" data="$2"
  if svc_unit_installed; then
    systemctl start netx.service
    return
  fi
  if svc_pid_alive "${data}"; then
    return 0
  fi
  local ctl="${prefix}/current/packaging/linux/netxctl" pf
  pf="$(svc_pidfile "${data}")"
  # Own session => the pid in the file also names the process group to stop.
  # (Bundled Python instead of setsid/nohup: minimal images lack util-linux.)
  # Installed copy: install.sh's own NETX_PY may live where netx cannot enter (/root).
  as_netx "${prefix}/current/python/bin/python3" -I "${prefix}/current/packaging/linux/netx_admin.py"     spawn "${data}/logs/netxctl.log" "${pf}" "${ctl}" run
}

svc_stop() {
  local prefix="$1" data="$2"
  if svc_unit_installed; then
    systemctl stop netx.service || true
  elif svc_pid_alive "${data}"; then
    local pid i
    pid="$(cat "$(svc_pidfile "${data}")")"
    kill -TERM "${pid}" 2>/dev/null || true
    for ((i = 0; i < 90; i++)); do
      kill -0 "${pid}" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "${pid}" 2>/dev/null; then
      warn "$(t "进程未在 90 秒内退出，强制结束" "netx did not exit in 90s; killing")"
      kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL "${pid}" 2>/dev/null || true
    fi
  fi
  rm -f "$(svc_pidfile "${data}")"
  # A crashed supervisor can leave the bundled cluster running.
  if [[ -x "${prefix}/current/pgsql/bin/pg_ctl" && -f "${data}/pgdata/PG_VERSION" ]]; then
    PGBIN="${prefix}/current/pgsql/bin" PGDATA_DIR="${data}/pgdata" pg_stop
  fi
  return 0
}
