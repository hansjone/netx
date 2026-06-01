#!/usr/bin/env bash
set -euo pipefail

# netx Linux start script (API + Web)
# - API: python -m netx_api.main (default 8890)
# - Web: Vite dev server (default 8055, external)
#
# PID/log files are stored in: scripts/.run/

usage() {
  cat <<'EOF'
Usage: ./scripts/start_netx.sh [options]

Options:
  --python-cmd <path>   Python executable. Default: /usr/local/python_env_new/bin/python3
                        (or env PYTHON_CMD if set)
  --bind-host <host>    API bind host (sets NETX_HOST). Default: 127.0.0.1
  --port <port>         API port (sets NETX_PORT). Default: 8890
  --web-host <host>     Web bind host. Default: 0.0.0.0
  --web-port <port>     Web port. Default: 8055
  --skip-install        Skip pip/npm install steps
  -h, --help            Show help

Notes:
  - Runs with system/host Python (no .venv).
  - If repo root has a .env file, it will be sourced (exported).
  - Web uses Vite dev proxy (see web/vite.config.ts) to forward /v1 -> API.
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/scripts/.run"
mkdir -p "${RUN_DIR}"

PID_FILE="${RUN_DIR}/netx.pid"
LOG_FILE="${RUN_DIR}/netx.out.log"
ERR_FILE="${RUN_DIR}/netx.err.log"
WEB_PID_FILE="${RUN_DIR}/web.pid"
WEB_LOG_FILE="${RUN_DIR}/web.out.log"
WEB_ERR_FILE="${RUN_DIR}/web.err.log"

PYTHON_CMD="${PYTHON_CMD:-/usr/local/python_env_new/bin/python3}"
API_BIND_HOST="127.0.0.1"
API_PORT="8890"
WEB_BIND_HOST="0.0.0.0"
WEB_PORT="8055"
SKIP_INSTALL="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python-cmd) PYTHON_CMD="${2:-}"; shift 2 ;;
    --bind-host) API_BIND_HOST="${2:-}"; shift 2 ;;
    --port) API_PORT="${2:-}"; shift 2 ;;
    --web-host) WEB_BIND_HOST="${2:-}"; shift 2 ;;
    --web-port) WEB_PORT="${2:-}"; shift 2 ;;
    --skip-install) SKIP_INSTALL="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[ERR] Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

cd "${ROOT_DIR}"

if [[ -f "${PID_FILE}" ]] || [[ -f "${WEB_PID_FILE}" ]]; then
  echo "[INFO] Existing PID file(s) found under ${RUN_DIR}."
  echo "       If services are not actually running, remove PID files or run ./scripts/stop_netx.sh"
fi

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

export NETX_HOST="${API_BIND_HOST}"
export NETX_PORT="${API_PORT}"

if [[ ! -x "${PYTHON_CMD}" ]]; then
  echo "[ERR] python_not_found: ${PYTHON_CMD}" >&2
  echo "      Set PYTHON_CMD or pass --python-cmd <path>" >&2
  exit 1
fi

echo "==> Project root: ${ROOT_DIR}"
echo "==> Using python: ${PYTHON_CMD}"

if [[ "${SKIP_INSTALL}" != "1" ]]; then
  echo "==> Installing backend dependencies"
  "${PYTHON_CMD}" -m pip install -r "${ROOT_DIR}/requirements.txt"
else
  echo "==> Skip dependency install"
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "[ERR] npm_not_found: install Node.js (includes npm)" >&2
  exit 1
fi

WEB_ROOT="${ROOT_DIR}/web"
if [[ ! -d "${WEB_ROOT}" ]]; then
  echo "[ERR] web/ not found: ${WEB_ROOT}" >&2
  exit 1
fi

if [[ "${SKIP_INSTALL}" != "1" ]] || [[ ! -d "${WEB_ROOT}/node_modules" ]]; then
  echo "==> Installing web dependencies (npm install)"
  (cd "${WEB_ROOT}" && npm install)
else
  echo "==> Skip web dependency install (node_modules exists)"
fi

BASE_URL="http://${API_BIND_HOST}:${API_PORT}"
WEB_URL="http://${WEB_BIND_HOST}:${WEB_PORT}"

echo ""
echo "==> netx API URL"
echo "Base:          ${BASE_URL}/"
echo "Health:        ${BASE_URL}/health"
echo "Integrations:  ${BASE_URL}/v1/integrations/status"
echo ""
echo "==> netx UI URL"
echo "Vite Dev UI:   ${WEB_URL}/"
echo ""

echo "==> Starting netx API in background"
(
  cd "${ROOT_DIR}"
  nohup "${PYTHON_CMD}" -m netx_api.main >"${LOG_FILE}" 2>"${ERR_FILE}" &
  echo $! > "${PID_FILE}"
)
API_PID="$(cat "${PID_FILE}")"
echo "netx.pid = ${PID_FILE}"
echo "PID      = ${API_PID}"
echo "Log      = ${LOG_FILE}"
echo "Err      = ${ERR_FILE}"

echo "==> Starting Vite dev server in background"
(
  cd "${WEB_ROOT}"
  nohup npm run dev -- --host "${WEB_BIND_HOST}" --port "${WEB_PORT}" \
    >"${WEB_LOG_FILE}" 2>"${WEB_ERR_FILE}" &
  echo $! > "${WEB_PID_FILE}"
)
WEB_PID="$(cat "${WEB_PID_FILE}")"
echo "web.pid  = ${WEB_PID_FILE}"
echo "PID      = ${WEB_PID}"
echo "Log      = ${WEB_LOG_FILE}"
echo "Err      = ${WEB_ERR_FILE}"

echo ""
echo "==> Background services started; API/web keep running."
