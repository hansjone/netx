#!/usr/bin/env bash
# Build netx-<ver>-linux-x86_64.tar.gz (+ .sha256) into packaging/release/.
#
#   packaging/linux/build_linux_release.sh [--version X.Y.Z] [--web-dist DIR] [--skip-web-build]
#
# Needs docker (the build runs inside manylinux2014 so the result works on glibc >= 2.17).
# Node 20+ is needed only when web/dist must be (re)built.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
# shellcheck source=versions.env
. "${HERE}/versions.env"

VERSION=""
WEB_DIST=""
SKIP_WEB_BUILD=0
OUT="${REPO}/packaging/release"
CACHE="${REPO}/packaging/cache/linux"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --web-dist) WEB_DIST="$2"; shift 2 ;;
    --skip-web-build) SKIP_WEB_BUILD=1; shift ;;
    --out) OUT="$2"; shift 2 ;;
    --cache) CACHE="$2"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "${VERSION}" ]]; then
  VERSION="$(sed -n 's/^[[:space:]]*version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "${REPO}/pyproject.toml" | head -n1)"
fi
[[ -n "${VERSION}" ]] || { echo "cannot read version from pyproject.toml" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }

if [[ -z "${WEB_DIST}" ]]; then
  WEB_DIST="${REPO}/web/dist"
  if [[ "${SKIP_WEB_BUILD}" != "1" ]]; then
    echo "==> npm build (web/dist)"
    (cd "${REPO}/web" && npm ci && npm run build:release)
  fi
fi
[[ -f "${WEB_DIST}/index.html" ]] || { echo "web dist missing: ${WEB_DIST}/index.html" >&2; exit 1; }
WEB_DIST="$(cd "${WEB_DIST}" && pwd)"

mkdir -p "${OUT}" "${CACHE}"
fetch() { # fetch url sha256
  local f
  f="${CACHE}/$(basename "$1")"
  if [[ ! -f "${f}" ]] || ! echo "$2  ${f}" | sha256sum -c --status; then
    echo "==> download $1"
    curl -fSL --retry 3 -o "${f}.part" "$1"
    mv "${f}.part" "${f}"
  fi
  echo "$2  ${f}" | sha256sum -c --status || { echo "sha256 mismatch: ${f}" >&2; exit 1; }
}
fetch "${PG_URL}" "${PG_SHA256}"
fetch "${PY_URL}" "${PY_SHA256}"

echo "==> build in ${BUILD_IMAGE} (version ${VERSION})"
docker run --rm \
  -v "${REPO}:/src:ro" \
  -v "${WEB_DIST}:/webdist:ro" \
  -v "${CACHE}:/cache" \
  -v "${OUT}:/out" \
  -e VERSION="${VERSION}" \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -e PIP_INDEX_URL="${PIP_INDEX_URL:-}" \
  -e HTTP_PROXY="${HTTP_PROXY:-}" -e HTTPS_PROXY="${HTTPS_PROXY:-}" -e NO_PROXY="${NO_PROXY:-}" \
  -e http_proxy="${http_proxy:-}" -e https_proxy="${https_proxy:-}" -e no_proxy="${no_proxy:-}" \
  "${BUILD_IMAGE}" bash /src/packaging/linux/build_in_container.sh

echo "==> done:"
ls -la "${OUT}/netx-${VERSION}-linux-x86_64.tar.gz" "${OUT}/netx-${VERSION}-linux-x86_64.tar.gz.sha256"
