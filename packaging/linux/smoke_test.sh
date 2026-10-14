#!/usr/bin/env bash
# Offline install smoke test, run inside a bare distro container as root:
#   docker run --rm --network none -v "$PWD/packaging/release:/pkg:ro" \
#     -v "$PWD/packaging/linux/smoke_test.sh:/smoke.sh:ro" centos:7 bash /smoke.sh /pkg/netx-X-linux-x86_64.tar.gz
# Exercises: install (custom ports) -> login -> set-port -> set-pg-port -> update
# (same package, with backup) -> data survives -> stop -> uninstall --purge.
set -euo pipefail
TARBALL="$1"
PKGDIR="${2:-}" # pre-extracted package (minimal images may lack tar)
PORT1=18890 PORT2=18891 PG1=25432 PG2=25433

step() { printf '\n\033[36m### %s\033[0m\n' "$*"; }
fail() { printf '\033[31m[SMOKE FAIL] %s\033[0m\n' "$*" >&2; tail -n 60 /var/lib/netx/logs/*.log 2>/dev/null >&2 || true; exit 1; }

PY() { /opt/netx/current/python/bin/python3 -I -c "$@"; }

login() { # login <port> -> prints access token
  PY "
import json, urllib.request
req = urllib.request.Request('http://127.0.0.1:$1/v1/auth/login',
    data=json.dumps({'username': 'admin', 'password': open('/var/lib/netx/.env').read().split('NETX_BOOTSTRAP_ADMIN_PASSWORD=')[1].split()[0]}).encode(),
    headers={'Content-Type': 'application/json'})
import time
for attempt in range(3):
    try:
        body = json.load(urllib.request.urlopen(req, timeout=60))
        break
    except Exception:
        if attempt == 2:
            raise
        time.sleep(5)
print(body.get('access_token') or body['token'])
"
}

health() { PY "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:$1/health', timeout=5).status == 200 else 1)" 2>/dev/null; }

listening() { # listening <port> -> 0 when something accepts on 127.0.0.1:<port>
  PY "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(s.connect_ex(('127.0.0.1', $1)))"
}

step "environment: $(. /etc/os-release && echo "${PRETTY_NAME}") / $(getconf GNU_LIBC_VERSION)"
if [[ -n "${PKGDIR}" ]]; then
  work="$(mktemp -d)"
  cp -a "${PKGDIR}" "${work}/"
else
  work="$(mktemp -d)"
  tar xzf "${TARBALL}" -C "${work}"
fi
pkg="$(echo "${work}"/netx-*)"

step "install (bundled PG ${PG1}, API ${PORT1})"
"${pkg}/install.sh" --yes --no-service --db bundled --port "${PORT1}" --pg-port "${PG1}" || fail "install"
health "${PORT1}" || fail "health after install"
listening "${PG1}" || fail "PG not on ${PG1}"
token="$(login "${PORT1}")" || fail "login"
[[ -n "${token}" ]] || fail "empty token"
netxctl status || fail "status"
netxctl admin-password | grep "$(grep '^NETX_BOOTSTRAP_ADMIN_PASSWORD=' /var/lib/netx/.env | cut -d= -f2-)" >/dev/null || fail "admin-password (initial)"
grep -q '^NETX_ALLOW_INSECURE_DEFAULTS=1' /var/lib/netx/.env || fail "UME/insecure defaults not set"

step "marker row survives update"
PGPASSWORD="$(grep '^NETX_DATABASE_URL=' /var/lib/netx/.env | sed 's#.*://netx:\([^@]*\)@.*#\1#')" \
  /opt/netx/current/pgsql/bin/psql -h 127.0.0.1 -p "${PG1}" -U netx -d netx -v ON_ERROR_STOP=1 \
  -c "CREATE TABLE IF NOT EXISTS smoke_marker(v text); INSERT INTO smoke_marker VALUES ('kept');" || fail "marker"

step "set-port ${PORT2}"
netxctl set-port "${PORT2}" || fail "set-port"
health "${PORT2}" || fail "health after set-port"

step "set-pg-port ${PG2}"
netxctl set-pg-port "${PG2}" || fail "set-pg-port"
health "${PORT2}" || fail "health after set-pg-port"
listening "${PG2}" || fail "PG not on ${PG2}"
listening "${PG1}" && fail "PG still on ${PG1}"

step "set-pg-port to a busy port is refused"
if netxctl set-pg-port "${PORT2}"; then fail "busy port accepted"; fi
health "${PORT2}" || fail "health after refused port change"

step "update with the same package (--force path via install.sh --mode update)"
"${pkg}/install.sh" --mode update --yes || fail "update"
health "${PORT2}" || fail "health after update"
ls /var/lib/netx/backups/pre-*.dump >/dev/null || fail "no pre-update dump"
out="$(PGPASSWORD="$(grep '^NETX_DATABASE_URL=' /var/lib/netx/.env | sed 's#.*://netx:\([^@]*\)@.*#\1#')" \
  /opt/netx/current/pgsql/bin/psql -h 127.0.0.1 -p "${PG2}" -U netx -d netx -tAc "SELECT v FROM smoke_marker")"
[[ "${out}" == "kept" ]] || fail "marker lost: ${out}"
login "${PORT2}" >/dev/null || fail "login after update"

step "offline update via netxctl update --file (same version needs --force)"
if netxctl update --file "${TARBALL}" --yes; then fail "same-version update without --force accepted"; fi
netxctl update --file "${TARBALL}" --yes --force || fail "netxctl update --file --force"
health "${PORT2}" || fail "health after netxctl update"

step "backup"
netxctl backup /tmp/netx-backup.tar.gz || fail "backup"
netxctl_admin_list="$(/opt/netx/current/python/bin/python3 -I /opt/netx/current/packaging/linux/netx_admin.py tar-list /tmp/netx-backup.tar.gz)"
[[ "${netxctl_admin_list}" == *netx.dump* ]] || fail "backup content"

step "stop"
netxctl stop || fail "stop"
health "${PORT2}" && fail "still healthy after stop"
listening "${PG2}" && fail "PG still running after stop"
pgrep -f netx_api >/dev/null 2>&1 && fail "netx_api processes left" || true

step "start again + uninstall --purge"
netxctl start || fail "restart"
netxctl uninstall --purge --yes || fail "uninstall"
[[ ! -e /opt/netx && ! -e /var/lib/netx && ! -e /usr/bin/netxctl ]] || fail "leftovers after purge"

printf '\n\033[32m[SMOKE OK] %s\033[0m\n' "$(. /etc/os-release && echo "${PRETTY_NAME}")"
