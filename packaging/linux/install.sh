#!/usr/bin/env bash
# NetX Linux installer / updater. Ships at the root of netx-<ver>-linux-x86_64.tar.gz.
#
#   sudo ./install.sh                       # interactive install (or update when already installed)
#   sudo ./install.sh --yes --db bundled --port 8890 --pg-port 15432
#   sudo ./install.sh --mode update --yes   # what `netxctl update` runs
#
# Needs only bash, coreutils and useradd (tar to unpack it): Python, PostgreSQL
# and the web UI are inside the package.
set -euo pipefail

PKG="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib.sh
. "${PKG}/packaging/linux/lib.sh"

NETX_PY="${PKG}/python/bin/python3"
NETX_ADMIN_PY="${PKG}/packaging/linux/netx_admin.py"

PREFIX="/opt/netx"
DATA="/var/lib/netx"
MODE=""
DB_MODE=""
API_PORT=""
BIND=""
PG_PORT=""
PG_PASSWORD=""
DB_URL=""
DB_HOST="" DB_PORT_EXT="" DB_USER="" DB_PASS="" DB_NAME=""
CRED_KEY_FILE=""
ADMIN_PASSWORD=""
ADMIN_PW_GENERATED=0
NO_SERVICE=0
AUTO_UPDATE=0
OPEN_FIREWALL=0
NO_BACKUP=0
GRANT_ACCESS=0
YES=0

usage() {
  cat <<EOF
$(t "用法" "Usage"): sudo ./install.sh [options]

  --mode install|update|reinstall  $(t "已安装时默认 update（保留数据与配置）" "default: update when already installed (keeps data/config)")
  --prefix DIR          $(t "程序目录" "program dir") (/opt/netx)
  --data DIR            $(t "数据目录" "data dir") (/var/lib/netx)
  --port N              $(t "Web/API 端口" "Web/API port") (8890)
  --bind HOST           $(t "监听地址" "listen address") (0.0.0.0)
  --db bundled|external
  --pg-port N           $(t "内置 PostgreSQL 端口" "bundled PostgreSQL port") (15432)
  --pg-password PW      $(t "内置库密码（默认自动生成）" "bundled DB password (default: generated)")
  --db-url URL          postgresql+psycopg://USER:PASS@HOST:5432/DB
  --db-host/--db-port/--db-user/--db-password/--db-name   $(t "外置库参数（替代 --db-url）" "external DB parts")
  --admin-password PW   $(t "初始管理员 admin 的密码（默认随机生成，安装结束时显示）" "initial password for admin (default: generated, shown at the end)")
  --credential-key-file F   $(t "沿用其他安装的 NE 凭据加密密钥" "reuse NETX_CREDENTIAL_SECRET_KEY from another install")
  --no-service          $(t "不注册 systemd 服务" "do not install the systemd service")
  --auto-update         $(t "开启每日自动更新" "enable daily automatic updates")
  --open-firewall       $(t "自动放行防火墙端口（firewalld/ufw）" "open the port in firewalld/ufw")
  --no-backup           $(t "更新前不备份数据库" "skip the pre-update database dump")
  --grant-access        $(t "目录在 /root 等受限目录下时，自动给服务用户 netx 开放上级目录的“进入”权限" "if a parent dir (e.g. /root) blocks the netx service user, grant it traverse-only access")
  -y, --yes             $(t "非交互，使用默认值" "non-interactive, accept defaults")
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --prefix) PREFIX="${2:-}"; shift 2 ;;
    --data) DATA="${2:-}"; shift 2 ;;
    --port) API_PORT="${2:-}"; shift 2 ;;
    --bind) BIND="${2:-}"; shift 2 ;;
    --db) DB_MODE="${2:-}"; shift 2 ;;
    --pg-port) PG_PORT="${2:-}"; shift 2 ;;
    --pg-password) PG_PASSWORD="${2:-}"; shift 2 ;;
    --db-url) DB_URL="${2:-}"; shift 2 ;;
    --db-host) DB_HOST="${2:-}"; shift 2 ;;
    --db-port) DB_PORT_EXT="${2:-}"; shift 2 ;;
    --db-user) DB_USER="${2:-}"; shift 2 ;;
    --db-password) DB_PASS="${2:-}"; shift 2 ;;
    --db-name) DB_NAME="${2:-}"; shift 2 ;;
    --credential-key-file) CRED_KEY_FILE="${2:-}"; shift 2 ;;
    --admin-password) ADMIN_PASSWORD="${2:-}"; shift 2 ;;
    --no-service) NO_SERVICE=1; shift ;;
    --auto-update) AUTO_UPDATE=1; shift ;;
    --open-firewall) OPEN_FIREWALL=1; shift ;;
    --no-backup) NO_BACKUP=1; shift ;;
    --grant-access) GRANT_ACCESS=1; shift ;;
    -y|--yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) err "unknown option: $1"; usage; exit 2 ;;
  esac
done

INTERACTIVE=1
if [[ "${YES}" == "1" || ! -t 0 ]]; then INTERACTIVE=0; fi
PREFIX="${PREFIX%/}"
DATA="${DATA%/}"
ENV_FILE="${DATA}/.env"
NETX_LOG_DIR="${DATA}/logs"
PGDATA_DIR="${DATA}/pgdata"

ask() { # ask "prompt" default -> echo answer
  local a
  read -r -p "$1 [$2]: " a || true
  echo "${a:-$2}"
}

# ------------------------------------------------------------------ preflight

preflight() {
  is_root || die "$(t "请用 root 运行：sudo ./install.sh" "run as root: sudo ./install.sh")"
  local arch
  arch="$(uname -m)"
  [[ "${arch}" == "x86_64" || "${arch}" == "amd64" ]] || die "$(t "本安装包仅支持 x86_64，当前" "this package is x86_64 only; this machine is") ${arch}"
  local glibc
  glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')" || true
  [[ -n "${glibc}" ]] || die "$(t "未检测到 glibc（不支持 Alpine/musl）" "glibc not found (Alpine/musl is not supported)")"
  if [[ "$(printf '%s\n2.17\n' "${glibc}" | sort -V | head -n1)" != "2.17" ]]; then
    die "$(t "glibc 版本过低" "glibc too old"): ${glibc} (>= 2.17)"
  fi
  "${NETX_PY}" -c "import sys" 2>/dev/null || die "$(t "内置 Python 无法运行，安装包可能损坏" "bundled Python does not run; package may be corrupt")"
  [[ -f "${PKG}/version.json" ]] || die "version.json missing in ${PKG}"
  local parent avail
  parent="$(dirname "${PREFIX}")"
  mkdir -p "${parent}"
  avail="$(df -Pk "${parent}" | awk 'NR==2 {print $4}')"
  if [[ -n "${avail}" && "${avail}" -lt 1048576 ]]; then
    die "$(t "磁盘空间不足（至少 1GB）" "not enough disk space (need 1GB)"): ${parent}"
  fi
}

ensure_user() {
  if id "${NETX_USER}" >/dev/null 2>&1; then return 0; fi
  info "$(t "创建系统用户" "creating system user") ${NETX_USER}"
  local nologin=/sbin/nologin
  [[ -x "${nologin}" ]] || nologin=/usr/sbin/nologin
  [[ -x "${nologin}" ]] || nologin=/bin/false
  if command -v useradd >/dev/null 2>&1; then
    useradd -r -U -d "${DATA}" -M -s "${nologin}" "${NETX_USER}" 2>/dev/null ||
      useradd -r -d "${DATA}" -M -s "${nologin}" "${NETX_USER}"
  elif command -v adduser >/dev/null 2>&1; then
    adduser -S -D -H -h "${DATA}" -s "${nologin}" "${NETX_USER}" 2>/dev/null ||
      adduser --system --group --no-create-home --home "${DATA}" "${NETX_USER}"
  else
    die "$(t "缺少 useradd/adduser，无法创建用户" "useradd/adduser not found")"
  fi
  # Some useradd builds do not create a same-named group with -r.
  getent group "${NETX_USER}" >/dev/null 2>&1 || groupadd -r "${NETX_USER}" 2>/dev/null || true
}

# ------------------------------------------------------------------ prompts

pick_port() { # pick_port <label> <value> <default> <host> <allow_current>
  local label="$1" val="$2" def="$3" host="$4" allow="$5" p
  while :; do
    if [[ -z "${val}" ]]; then
      if [[ "${INTERACTIVE}" == "1" ]]; then val="$(ask "${label}" "${def}")"; else val="${def}"; fi
    fi
    if ! p="$(netx_admin valid-port "${val}" 2>&1)"; then
      [[ "${INTERACTIVE}" == "1" ]] || die "${p}"
      err "${p}"; val=""; continue
    fi
    local rc=0
    [[ "${p}" == "${allow}" ]] || netx_admin port-free "${p}" --host "${host}" || rc=$?
    if [[ "${rc}" == "0" ]]; then
      echo "${p}"; return 0
    fi
    [[ "${rc}" == "3" ]] && die "$(t "监听地址无效" "invalid listen address"): ${host}"
    local msg
    msg="$(t "端口 ${p} 已被占用" "port ${p} is in use")"
    [[ "${INTERACTIVE}" == "1" ]] || die "port_in_use: ${p} — ${msg}"
    err "${msg}"
    val=""
  done
}

configure() {
  local old_port old_bind old_mode old_pg
  old_port="$(env_get "${ENV_FILE}" NETX_PORT)"
  old_bind="$(env_get "${ENV_FILE}" NETX_HOST)"
  old_mode="$(env_get "${ENV_FILE}" NETX_DB_MODE)"
  old_pg="$(env_get "${ENV_FILE}" NETX_BUNDLED_PG_PORT)"

  echo
  info "$(t "配置 NetX" "Configure NetX")"
  local b
  while :; do
    if [[ -z "${BIND}" ]]; then
      if [[ "${INTERACTIVE}" == "1" ]]; then
        BIND="$(ask "$(t "监听地址（回车或 0.0.0.0 = 所有网卡；127.0.0.1 = 仅本机；或本机某个 IP）" "Listen address (Enter/0.0.0.0 = all interfaces, 127.0.0.1 = local only, or one of this server's IPs)")" "${old_bind:-0.0.0.0}")"
      else
        BIND="${old_bind:-0.0.0.0}"
      fi
    fi
    if b="$(netx_admin norm-bind -- "${BIND}" 2>&1)"; then
      BIND="${b}"
      break
    fi
    [[ "${INTERACTIVE}" == "1" ]] || die "${b}"
    err "${b}"
    BIND=""
  done
  API_PORT="$(pick_port "$(t "Web/API 端口" "Web/API port")" "${API_PORT}" "${old_port:-8890}" "${BIND}" "${old_port}")"

  if [[ -z "${DB_MODE}" ]]; then
    if [[ -n "${DB_URL}${DB_HOST}" ]]; then
      DB_MODE="external"
    elif [[ "${INTERACTIVE}" == "1" ]]; then
      echo "$(t "数据库：" "Database:")"
      echo "  1) $(t "内置 PostgreSQL（离线可用，推荐）" "bundled PostgreSQL (offline, recommended)")"
      echo "  2) $(t "外置 PostgreSQL（已有的库）" "external PostgreSQL (existing server)")"
      local def=1
      [[ "${old_mode}" == "external" ]] && def=2
      [[ "$(ask "$(t "请选择" "Choose")" "${def}")" == "2" ]] && DB_MODE="external" || DB_MODE="bundled"
    else
      DB_MODE="${old_mode:-bundled}"
    fi
  fi
  case "${DB_MODE}" in bundled|external) ;; *) die "invalid --db: ${DB_MODE}" ;; esac

  if [[ "${DB_MODE}" == "bundled" ]]; then
    PG_PORT="$(pick_port "$(t "内置 PostgreSQL 端口（仅本机 127.0.0.1）" "Bundled PostgreSQL port (127.0.0.1 only)")" "${PG_PORT}" "${old_pg:-15432}" 127.0.0.1 "${old_pg}")"
  else
    configure_external
  fi

  CRED_KEY=""
  if [[ -n "${CRED_KEY_FILE}" ]]; then
    CRED_KEY="$(tr -d ' \r\n' <"${CRED_KEY_FILE}")"
  elif [[ -z "$(env_get "${ENV_FILE}" NETX_CREDENTIAL_SECRET_KEY)" && "${INTERACTIVE}" == "1" ]]; then
    echo "$(t "可选：粘贴其他 NetX 安装的 NETX_CREDENTIAL_SECRET_KEY，以沿用已加密的网元密码；回车跳过（自动生成）" \
      "Optional: paste NETX_CREDENTIAL_SECRET_KEY from another install to reuse encrypted NE passwords; Enter = generate")"
    read -r -p "NETX_CREDENTIAL_SECRET_KEY: " CRED_KEY || true
  fi
}

configure_external() {
  local existing
  existing="$(env_get "${ENV_FILE}" NETX_DATABASE_URL)"
  while :; do
    if [[ -z "${DB_URL}" && -n "${DB_HOST}" ]]; then
      DB_URL="$(netx_admin build-url -- "${DB_USER:-netx}" "${DB_PASS}" "${DB_HOST}" "${DB_PORT_EXT:-5432}" "${DB_NAME:-netx}")" || exit 1
    fi
    if [[ -z "${DB_URL}" ]]; then
      if [[ "${INTERACTIVE}" != "1" ]]; then
        [[ -n "${existing}" && "$(env_get "${ENV_FILE}" NETX_DB_MODE)" == "external" ]] && DB_URL="${existing}"
        [[ -n "${DB_URL}" ]] || die "external_url_required: --db-url or --db-host/--db-user/--db-password/--db-name"
      else
        echo "$(t "外置 PostgreSQL（库和用户需事先建好）" "External PostgreSQL (database and role must exist)")"
        local h po u pw n
        h="$(ask "$(t "主机" "Host")" "127.0.0.1")"
        po="$(ask "$(t "端口" "Port")" "5432")"
        u="$(ask "$(t "用户" "User")" "netx")"
        read -r -s -p "$(t "密码" "Password"): " pw || true
        echo
        n="$(ask "$(t "数据库" "Database")" "netx")"
        DB_URL="$(netx_admin build-url -- "${u}" "${pw}" "${h}" "${po}" "${n}")" || { DB_URL=""; continue; }
      fi
    fi
    info "$(t "测试外置数据库连接" "probing external PostgreSQL")"
    if netx_admin probe-db "${DB_URL}"; then
      ok "$(t "外置数据库连接成功" "external PostgreSQL OK")"
      return 0
    fi
    [[ "${INTERACTIVE}" == "1" ]] || die "external_db_probe_failed"
    DB_URL="" DB_HOST=""
  done
}

# ------------------------------------------------------------------ program files

# Set by load_pkg_meta (after preflight proved the bundled Python runs).
VERSION=""
PKG_PG_MAJOR=""
REL=""
load_pkg_meta() {
  VERSION="$(netx_admin json-get "${PKG}/version.json" version)" || die "bad version.json"
  PKG_PG_MAJOR="$(netx_admin json-get "${PKG}/version.json" pg_major 2>/dev/null || echo 16)"
  REL="${PREFIX}/releases/${VERSION}"
}

deploy_release() {
  info "$(t "安装程序文件到" "installing program files to") ${REL}"
  mkdir -p "${PREFIX}/releases"
  if [[ "$(readlink -f "${PKG}")" != "$(readlink -f "${REL}" 2>/dev/null || echo "${REL}")" ]]; then
    rm -rf "${REL}.new"
    mkdir -p "${REL}.new"
    cp -a "${PKG}/." "${REL}.new/"
    rm -rf "${REL}"
    mv "${REL}.new" "${REL}"
  fi
  chown -R root:root "${REL}"
  chmod -R u+rwX,go+rX,go-w "${REL}"
  info "$(t "预编译 Python 字节码" "precompiling Python bytecode")"
  "${REL}/python/bin/python3" -m compileall -q -j 0 "${REL}/netx_api" "${REL}/alembic" \
    "${REL}/python/lib/python3.12" >/dev/null 2>&1 || true
  printf 'NETX_DATA=%s\n' "${DATA}" >"${PREFIX}/netx.conf"
}

switch_current() { # switch_current <version>
  ln -sfn "releases/$1" "${PREFIX}/current.tmp"
  mv -Tf "${PREFIX}/current.tmp" "${PREFIX}/current"
}

install_integration() {
  # /usr/bin: sudo's secure_path on RHEL-family systems omits /usr/local/bin.
  ln -sfn "${PREFIX}/current/packaging/linux/netxctl" /usr/bin/netxctl
  if [[ -d /etc/logrotate.d ]]; then
    sed -e "s#@DATA@#${DATA}#g" -e "s#@USER@#${NETX_USER}#g" \
      "${REL}/packaging/linux/logrotate.netx" >/etc/logrotate.d/netx
    chmod 0644 /etc/logrotate.d/netx
  fi
  if [[ "${NO_SERVICE}" != "1" ]] && have_systemd; then
    sed -e "s#@PREFIX@#${PREFIX}#g" -e "s#@USER@#${NETX_USER}#g" \
      "${REL}/packaging/linux/systemd/netx.service.in" >/etc/systemd/system/netx.service
    if [[ -f /etc/systemd/system/netx-update.service ]]; then
      sed -e "s#@PREFIX@#${PREFIX}#g" "${REL}/packaging/linux/systemd/netx-update.service.in" \
        >/etc/systemd/system/netx-update.service
    fi
    systemctl daemon-reload
    systemctl enable netx.service >/dev/null 2>&1
  fi
  if command -v restorecon >/dev/null 2>&1 && command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
    restorecon -R "${PREFIX}" "${DATA}" >/dev/null 2>&1 || true
  fi
}

prune_releases() { # keep current + $1
  local keep1 keep2 d
  keep1="$(basename "$(readlink -f "${PREFIX}/current")")"
  keep2="${1:-}"
  for d in "${PREFIX}"/releases/*; do
    [[ -d "${d}" ]] || continue
    case "$(basename "${d}")" in
      "${keep1}"|"${keep2}") ;;
      *) rm -rf "${d}" ;;
    esac
  done
}

# ------------------------------------------------------------------ data / db

write_env() {
  local pairs=()
  pairs+=("NETX_DB_MODE=${DB_MODE}" "NETX_HOST=${BIND}" "NETX_PORT=${API_PORT}")
  pairs+=("NETX_UI_DIST_DIR=${PREFIX}/current/web/dist" "NETX_RUN_INLINE_SCHEDULERS=false")
  local kv
  while IFS= read -r kv; do pairs+=("${kv}"); done < <(data_env_pairs "${DATA}")
  if [[ "${DB_MODE}" == "bundled" ]]; then
    local url
    url="$(netx_admin build-url -- netx "${PG_PASSWORD}" 127.0.0.1 "${PG_PORT}" netx)" || die "build_db_url_failed"
    pairs+=("NETX_BUNDLED_PG_PORT=${PG_PORT}" "NETX_DATABASE_URL=${url}")
  else
    pairs+=("NETX_DATABASE_URL=${DB_URL}")
  fi
  # NetX refuses a non-loopback bind while the bootstrap admin password is the lab
  # default (admin123) or UME TLS verification is off (security_bootstrap.py).
  if [[ -n "${ADMIN_PASSWORD}" ]]; then
    pairs+=("NETX_BOOTSTRAP_ADMIN_PASSWORD=${ADMIN_PASSWORD}")
  elif [[ -z "$(env_get "${ENV_FILE}" NETX_BOOTSTRAP_ADMIN_PASSWORD)" ]]; then
    ADMIN_PASSWORD="$(netx_admin gen-secret password)"
    ADMIN_PW_GENERATED=1
    pairs+=("NETX_BOOTSTRAP_ADMIN_PASSWORD=${ADMIN_PASSWORD}")
  fi
  # Deployments are on internal networks where UME uses self-signed certificates:
  # TLS verification is off by default and that check is waived explicitly
  # (admin password and JWT secret are already unique per install). Written for
  # every bind address so a later `netxctl set-bind` cannot trip the check.
  [[ -n "$(env_get "${ENV_FILE}" NETX_UME_VERIFY_TLS)" ]] || pairs+=("NETX_UME_VERIFY_TLS=false")
  [[ -n "$(env_get "${ENV_FILE}" NETX_ALLOW_INSECURE_DEFAULTS)" ]] || pairs+=("NETX_ALLOW_INSECURE_DEFAULTS=1")
  if [[ -n "${CRED_KEY}" ]]; then
    pairs+=("NETX_CREDENTIAL_SECRET_KEY=${CRED_KEY}")
  elif [[ -z "$(env_get "${ENV_FILE}" NETX_CREDENTIAL_SECRET_KEY)" ]]; then
    pairs+=("NETX_CREDENTIAL_SECRET_KEY=$(netx_admin gen-secret fernet)")
  fi
  touch "${ENV_FILE}"
  chmod 0600 "${ENV_FILE}"
  netx_admin env-set "${ENV_FILE}" "${pairs[@]}"
  chown "${NETX_USER}:${NETX_USER}" "${ENV_FILE}"
  chmod 0600 "${ENV_FILE}"
}

setup_bundled_pg() {
  PGBIN="${REL}/pgsql/bin"
  local major
  major="$(pg_data_major || true)"
  if [[ -n "${major}" && "${major}" != "${PKG_PG_MAJOR}" ]]; then
    die "pg_major_mismatch: ${PGDATA_DIR} is PostgreSQL ${major}, package ships ${PKG_PG_MAJOR}"
  fi
  if [[ -z "${PG_PASSWORD}" ]]; then
    local old_url
    old_url="$(env_get "${ENV_FILE}" NETX_DATABASE_URL)"
    if [[ -n "${major}" ]]; then
      # Existing cluster: keep its password (it was set at first install).
      [[ -n "${old_url}" ]] && PG_PASSWORD="$(netx_admin url-part "${old_url}" password 2>/dev/null || true)"
      [[ -n "${PG_PASSWORD}" ]] || die "$(t "已有内置数据库但找不到原密码，请用 --pg-password 指定" "existing bundled cluster but its password is unknown; pass --pg-password")"
    else
      PG_PASSWORD="$(netx_admin gen-secret password)"
    fi
  fi
  # Record the password before initdb so re-running a failed install can reuse the cluster.
  local early_url
  early_url="$(netx_admin build-url -- netx "${PG_PASSWORD}" 127.0.0.1 "${PG_PORT}" netx)" || die "build_db_url_failed"
  touch "${ENV_FILE}"
  netx_admin env-set "${ENV_FILE}" "NETX_DATABASE_URL=${early_url}"
  chown "${NETX_USER}:${NETX_USER}" "${ENV_FILE}"
  chmod 0600 "${ENV_FILE}"
  if [[ -z "${major}" ]]; then
    info "$(t "初始化内置 PostgreSQL" "initialising bundled PostgreSQL") (${PGDATA_DIR})"
    rm -rf "${PGDATA_DIR:?}"/* "${PGDATA_DIR:?}"/.[!.]* 2>/dev/null || true
    local pw="${DATA}/.pginit.pw"
    (umask 077 && printf '%s' "${PG_PASSWORD}" >"${pw}")
    chown "${NETX_USER}:${NETX_USER}" "${pw}"
    as_netx env LC_ALL=C LANG=C "${PGBIN}/initdb" -D "${PGDATA_DIR}" -U postgres --auth=scram-sha-256 \
      --pwfile="${pw}" -E UTF8 --locale=C >"${NETX_LOG_DIR}/initdb.log" 2>&1 ||
      { rm -f "${pw}"; tail -n 20 "${NETX_LOG_DIR}/initdb.log" >&2; die "initdb_failed"; }
    rm -f "${pw}"
  fi
  netx_admin pg-conf "${PGDATA_DIR}/postgresql.conf" --set "port=${PG_PORT}" \
    --set "listen_addresses='127.0.0.1'" --set "unix_socket_directories=''" \
    --set "logging_collector=off"
  chown "${NETX_USER}:${NETX_USER}" "${PGDATA_DIR}/postgresql.conf"

  local started=0
  if ! pg_is_running; then
    pg_start || { tail -n 20 "${NETX_LOG_DIR}/pg.log" >&2; die "pg_start_failed"; }
    started=1
  fi
  local sqlpw="${PG_PASSWORD//\'/\'\'}" out
  psql_su() {
    as_netx env PGPASSWORD="${PG_PASSWORD}" LC_ALL=C "${PGBIN}/psql" -h 127.0.0.1 -p "${PG_PORT}" -U postgres \
      -d postgres -v ON_ERROR_STOP=1 -tAc "$1"
  }
  if ! out="$(psql_su "SELECT 1 FROM pg_roles WHERE rolname='netx'" 2>&1)"; then
    [[ "${started}" == "1" ]] && pg_stop
    die "bundled_pg_login_failed: ${out}"
  fi
  if [[ "${out}" == "1" ]]; then
    psql_su "ALTER ROLE netx WITH LOGIN PASSWORD '${sqlpw}'" >/dev/null
  else
    psql_su "CREATE ROLE netx LOGIN PASSWORD '${sqlpw}'" >/dev/null
  fi
  if [[ "$(psql_su "SELECT 1 FROM pg_database WHERE datname='netx'")" != "1" ]]; then
    psql_su "CREATE DATABASE netx OWNER netx" >/dev/null
  fi
  [[ "${started}" == "1" ]] && pg_stop
  ok "$(t "内置 PostgreSQL 就绪" "bundled PostgreSQL ready"): 127.0.0.1:${PG_PORT}"
}

backup_before_update() { # $1 = new version
  [[ "${NO_BACKUP}" == "1" ]] && return 0
  [[ "$(env_get "${ENV_FILE}" NETX_DB_MODE)" == "bundled" ]] || return 0
  [[ -n "$(pg_data_major || true)" ]] || return 0
  # Installed (root-owned, world-readable) copy: the unpacked package may sit in a
  # directory the netx user cannot enter (e.g. /root).
  PGBIN="${REL}/pgsql/bin"
  local url port pw out started=0
  url="$(env_get "${ENV_FILE}" NETX_DATABASE_URL)"
  port="$(netx_admin url-part "${url}" port)"
  pw="$(netx_admin url-part "${url}" password)"
  out="${DATA}/backups/pre-$1-$(date +%Y%m%d-%H%M%S).dump"
  if ! pg_is_running; then
    pg_start || die "pg_start_failed (backup)"
    started=1
  fi
  info "$(t "更新前备份数据库" "backing up database before update") → ${out}"
  if ! as_netx env PGPASSWORD="${pw}" LC_ALL=C "${PGBIN}/pg_dump" -Fc -h 127.0.0.1 -p "${port}" -U netx -d netx -f "${out}"; then
    [[ "${started}" == "1" ]] && pg_stop
    die "$(t "备份失败，已中止更新（可加 --no-backup 跳过）" "backup failed; update aborted (use --no-backup to skip)")"
  fi
  [[ "${started}" == "1" ]] && pg_stop
  # Keep the 3 newest pre-update dumps.
  local old
  ls -1t "${DATA}"/backups/pre-*.dump 2>/dev/null | tail -n +4 | while IFS= read -r old; do rm -f "${old}"; done
}

health_url() {
  echo "http://$(client_host "$(env_get "${ENV_FILE}" NETX_HOST)"):$(env_get "${ENV_FILE}" NETX_PORT)/health"
}

start_and_wait() {
  svc_start "${PREFIX}" "${DATA}"
  info "$(t "等待 NetX 就绪（首次启动会建表，可能需要 1-2 分钟）" "waiting for NetX (first start creates tables; may take 1-2 min)")"
  netx_admin wait-health "$(health_url)" --timeout 180
}

open_firewall() {
  local p="$1"
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    if [[ "${OPEN_FIREWALL}" == "1" ]]; then
      firewall-cmd --permanent --add-port="${p}/tcp" >/dev/null && firewall-cmd --reload >/dev/null &&
        ok "firewalld: ${p}/tcp $(t "已放行" "opened")"
    else
      echo "  $(t "防火墙放行" "open firewall"): firewall-cmd --permanent --add-port=${p}/tcp && firewall-cmd --reload"
    fi
  elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    if [[ "${OPEN_FIREWALL}" == "1" ]]; then
      ufw allow "${p}/tcp" >/dev/null && ok "ufw: ${p}/tcp $(t "已放行" "opened")"
    else
      echo "  $(t "防火墙放行" "open firewall"): ufw allow ${p}/tcp"
    fi
  fi
}

summary() {
  local port bind host
  port="$(env_get "${ENV_FILE}" NETX_PORT)"
  bind="$(env_get "${ENV_FILE}" NETX_HOST)"
  host="$(primary_ip)"
  [[ "${bind}" == "127.0.0.1" ]] && host="127.0.0.1"
  echo
  ok "NetX ${VERSION} $(t "已就绪" "is up")"
  echo "  $(t "访问地址" "URL")      : http://${host}:${port}/"
  [[ "${MODE}" == "update" ]] || print_admin_login "${ENV_FILE}"
  echo "  $(t "程序目录" "program")  : ${PREFIX}/current -> releases/${VERSION}"
  echo "  $(t "数据目录" "data")     : ${DATA}"
  echo "  $(t "管理命令" "manage")   : sudo netxctl status | stop | start | set-port | set-pg-port | update"
  if svc_unit_installed; then
    echo "  systemd    : netx.service ($(t "开机自启" "starts at boot"))"
  else
    echo "  $(t "未注册 systemd 服务，重启后需执行 netxctl start" "no systemd service: run netxctl start after reboot")"
  fi
  [[ "${bind}" != "127.0.0.1" ]] && open_firewall "${port}"
  return 0
}

# ------------------------------------------------------------------ flows

do_install() { # install or reinstall
  ensure_user
  check_netx_access "${PREFIX}" "${DATA}"
  mkdir -p "${DATA}"
  ensure_data_dirs "${DATA}"
  if [[ -x "${PREFIX}/current/packaging/linux/netxctl" ]]; then
    svc_stop "${PREFIX}" "${DATA}"
  fi
  configure
  deploy_release
  if [[ "${DB_MODE}" == "bundled" ]]; then setup_bundled_pg; fi
  write_env
  ensure_data_dirs "${DATA}"
  switch_current "${VERSION}"
  install_integration
  if [[ "${AUTO_UPDATE}" == "1" ]]; then "${PREFIX}/current/packaging/linux/netxctl" auto-update on || true; fi
  if ! start_and_wait; then
    err "$(t "NetX 启动失败，日志" "NetX failed to start; logs"): ${DATA}/logs/"
    echo "  $(t "排除问题后执行 sudo netxctl start；登录密码用 sudo netxctl admin-password 查看" "after fixing it: sudo netxctl start; login password: sudo netxctl admin-password")" >&2
    show_start_failure "${DATA}"
    exit 1
  fi
  prune_releases ""
  summary
}

do_update() {
  [[ -f "${ENV_FILE}" ]] || die "$(t "未找到已有安装（${ENV_FILE}），请用 --mode install" "no existing install (${ENV_FILE}); use --mode install")"
  ensure_user
  check_netx_access "${PREFIX}" "${DATA}"
  ensure_data_dirs "${DATA}"
  local old_ver=""
  if [[ -f "${PREFIX}/current/version.json" ]]; then
    old_ver="$(netx_admin json-get "${PREFIX}/current/version.json" version || true)"
  fi
  if [[ "$(env_get "${ENV_FILE}" NETX_DB_MODE)" == "bundled" ]]; then
    local major
    major="$(pg_data_major || true)"
    if [[ -n "${major}" && "${major}" != "${PKG_PG_MAJOR}" ]]; then
      die "pg_major_mismatch: data=${major} package=${PKG_PG_MAJOR}"
    fi
  fi
  info "$(t "更新" "Updating") NetX ${old_ver:-?} → ${VERSION}"
  if [[ -n "${old_ver}" ]]; then svc_stop "${PREFIX}" "${DATA}"; fi
  if [[ "${old_ver}" == "${VERSION}" ]]; then
    # Reinstalling the same version: keep a copy so a failed start can roll back.
    rm -rf "${PREFIX}/releases/${VERSION}.prev"
    cp -a "${REL}" "${PREFIX}/releases/${VERSION}.prev" 2>/dev/null || true
  fi
  deploy_release
  backup_before_update "${VERSION}"
  # Re-assert keys newer releases rely on; never touches ports or DB settings.
  local pairs=("NETX_UI_DIST_DIR=${PREFIX}/current/web/dist" "NETX_RUN_INLINE_SCHEDULERS=false") kv
  while IFS= read -r kv; do pairs+=("${kv}"); done < <(data_env_pairs "${DATA}")
  # Defaults added after the first Linux release; existing values are kept.
  [[ -n "$(env_get "${ENV_FILE}" NETX_UME_VERIFY_TLS)" ]] || pairs+=("NETX_UME_VERIFY_TLS=false")
  [[ -n "$(env_get "${ENV_FILE}" NETX_ALLOW_INSECURE_DEFAULTS)" ]] || pairs+=("NETX_ALLOW_INSECURE_DEFAULTS=1")
  netx_admin env-set "${ENV_FILE}" "${pairs[@]}"
  chown "${NETX_USER}:${NETX_USER}" "${ENV_FILE}"
  switch_current "${VERSION}"
  install_integration
  if [[ "${AUTO_UPDATE}" == "1" ]]; then "${PREFIX}/current/packaging/linux/netxctl" auto-update on || true; fi
  if start_and_wait; then
    rm -rf "${PREFIX}/releases/${VERSION}.prev"
    prune_releases "${old_ver}"
    summary
    return 0
  fi
  err "$(t "新版本启动失败" "new version failed to start")"
  show_start_failure "${DATA}"
  svc_stop "${PREFIX}" "${DATA}"
  local back=""
  if [[ -n "${old_ver}" && "${old_ver}" != "${VERSION}" && -d "${PREFIX}/releases/${old_ver}" ]]; then
    back="${old_ver}"
  elif [[ -d "${PREFIX}/releases/${VERSION}.prev" ]]; then
    rm -rf "${REL}"
    mv "${PREFIX}/releases/${VERSION}.prev" "${REL}"
    back="${VERSION}"
  fi
  if [[ -n "${back}" ]]; then
    warn "$(t "回滚到" "rolling back to") ${back}"
    switch_current "${back}"
    REL="${PREFIX}/releases/${back}"
    install_integration
    svc_start "${PREFIX}" "${DATA}"
    netx_admin wait-health "$(health_url)" --timeout 180 && ok "$(t "已回滚并恢复运行" "rolled back and running"): ${back}"
    local dump
    dump="$(ls -1t "${DATA}"/backups/pre-*.dump 2>/dev/null | head -n1 || true)"
    [[ -n "${dump}" ]] && warn "$(t "如数据库已被新版本迁移，可用此备份恢复" "if the new version migrated the database, restore from"): ${dump}"
  fi
  exit 3
}

preflight
load_pkg_meta
if [[ -z "${MODE}" ]]; then
  if [[ -n "$(env_get "${ENV_FILE}" NETX_DB_MODE)" ]]; then MODE="update"; else MODE="install"; fi
  if [[ "${MODE}" == "update" && "${INTERACTIVE}" == "1" ]]; then
    echo "$(t "检测到已有安装（${DATA}）：" "Existing installation found (${DATA}):")"
    echo "  1) $(t "更新（保留数据和配置，推荐）" "update (keep data and settings, recommended)")"
    echo "  2) $(t "重新配置（重新选择端口/数据库，不删数据）" "reconfigure (choose ports/database again; data kept)")"
    [[ "$(ask "$(t "请选择" "Choose")" 1)" == "2" ]] && MODE="reinstall"
  fi
fi
case "${MODE}" in
  install|reinstall) do_install ;;
  update) do_update ;;
  *) die "invalid --mode: ${MODE}" ;;
esac
