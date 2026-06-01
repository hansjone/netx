#!/usr/bin/env bash
set -euo pipefail

# netx Linux stop script (API + Web)
# Stops by PID file first, then falls back to killing listeners on ports.

usage() {
  cat <<'EOF'
Usage: ./scripts/stop_netx.sh [options]

Options:
  --port <port>         API port to stop by port scan. Default: 8890
  --web-port <port>     Web port to stop by port scan. Default: 8505
  --force               Use SIGKILL (immediate)
  -h, --help            Show help
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/scripts/.run"

PID_FILE="${RUN_DIR}/netx.pid"
WEB_PID_FILE="${RUN_DIR}/web.pid"

API_PORT="8890"
WEB_PORT="8505"
FORCE="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) API_PORT="${2:-}"; shift 2 ;;
    --web-port) WEB_PORT="${2:-}"; shift 2 ;;
    --force) FORCE="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[ERR] Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

kill_pid() {
  local pid="$1"
  local name="$2"
  if [[ -z "${pid}" ]] || ! [[ "${pid}" =~ ^[0-9]+$ ]]; then
    return 0
  fi
  if ! kill -0 "${pid}" 2>/dev/null; then
    echo "[INFO] ${name} PID not running: ${pid}"
    return 0
  fi

  if [[ "${FORCE}" == "1" ]]; then
    kill -KILL "${pid}" 2>/dev/null || true
    echo "Stopped ${name} PID=${pid} (SIGKILL)"
    return 0
  fi

  kill -TERM "${pid}" 2>/dev/null || true
  for _ in {1..30}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      echo "Stopped ${name} PID=${pid}"
      return 0
    fi
    sleep 0.2
  done
  kill -KILL "${pid}" 2>/dev/null || true
  echo "Stopped ${name} PID=${pid} (SIGKILL after timeout)"
}

pids_by_port() {
  local port="$1"
  # Try lsof first (best), then ss fallback.
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true
    return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    # Example: LISTEN ... users:(("python",pid=1234,fd=3))
    ss -ltnp 2>/dev/null \
      | awk -v p=":${port}" '$4 ~ p {print $0}' \
      | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' \
      | sort -u
    return 0
  fi
  return 0
}

echo "==> Stopping netx"

if [[ -f "${PID_FILE}" ]]; then
  API_PID="$(head -n 1 "${PID_FILE}" | tr -d '[:space:]' || true)"
  kill_pid "${API_PID}" "api"
  rm -f "${PID_FILE}" || true
else
  echo "[INFO] No API PID file: ${PID_FILE}"
fi

if [[ -f "${WEB_PID_FILE}" ]]; then
  WEB_PID="$(head -n 1 "${WEB_PID_FILE}" | tr -d '[:space:]' || true)"
  kill_pid "${WEB_PID}" "web"
  rm -f "${WEB_PID_FILE}" || true
else
  echo "[INFO] No Web PID file: ${WEB_PID_FILE}"
fi

echo "==> Fallback: stopping by listening ports (${API_PORT}, ${WEB_PORT})"
for pid in $(pids_by_port "${API_PORT}"); do
  kill_pid "${pid}" "api(port:${API_PORT})"
done
for pid in $(pids_by_port "${WEB_PORT}"); do
  kill_pid "${pid}" "web(port:${WEB_PORT})"
done

echo "==> netx stop finished"
