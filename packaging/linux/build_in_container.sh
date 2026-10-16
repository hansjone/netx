#!/usr/bin/env bash
# Runs inside manylinux2014 (CentOS 7, glibc 2.17). Called by build_linux_release.sh.
# Mounts: /src (repo, ro)  /webdist (ro)  /cache (downloads + built PG)  /out
set -euo pipefail
. /src/packaging/linux/versions.env
: "${VERSION:?}"
ARCH=x86_64
NAME="netx-${VERSION}-linux-${ARCH}"
BUILD=/tmp/build
STAGE="${BUILD}/${NAME}"
JOBS="$(nproc)"

rm -rf /tmp/build
mkdir -p "${STAGE}"

# ------------------------------------------------------------ PostgreSQL (cached)
PG_TGZ="/cache/pgsql-${PG_VERSION}-linux-${ARCH}.tar.gz"
if [[ ! -f "${PG_TGZ}" ]]; then
  echo "==> building PostgreSQL ${PG_VERSION}"
  rm -rf /tmp/pgsrc /opt/pgsql
  mkdir -p /tmp/pgsrc
  tar xjf "/cache/$(basename "${PG_URL}")" -C /tmp/pgsrc
  cd "/tmp/pgsrc/postgresql-${PG_VERSION}"
  # No ICU/readline/OpenSSL: nothing for the target box to provide. SCRAM works
  # without OpenSSL; the server only listens on 127.0.0.1.
  ./configure --prefix=/opt/pgsql --without-icu --without-readline --without-openssl \
    --with-zlib --disable-rpath CFLAGS="-O2" >/tmp/pg-configure.log
  make -j"${JOBS}" >/tmp/pg-make.log
  make install >/tmp/pg-install.log
  cd /opt/pgsql
  keep=" postgres pg_ctl initdb psql pg_dump pg_dumpall pg_restore pg_isready pg_controldata pg_upgrade "
  for f in bin/*; do
    [[ "${keep}" == *" $(basename "${f}") "* ]] || rm -f "${f}"
  done
  rm -rf /opt/pgsql/include /opt/pgsql/lib/pgxs /opt/pgsql/lib/pkgconfig
  rm -f /opt/pgsql/lib/*.a
  cp -L "$(ldconfig -p | awk '/libz\.so\.1 .*x86-64/ {print $NF; exit}')" lib/libz.so.1
  for f in bin/*; do patchelf --set-rpath '$ORIGIN/../lib' "${f}"; done
  for f in lib/*.so*; do
    [[ -L "${f}" ]] || patchelf --set-rpath '$ORIGIN' "${f}"
  done
  strip --strip-unneeded bin/* lib/*.so* 2>/dev/null || true
  tar czf "${PG_TGZ}" -C /opt pgsql
fi
tar xzf "${PG_TGZ}" -C "${STAGE}"

# ------------------------------------------------------------ Python + deps
echo "==> Python ${PY_VERSION} + requirements"
tar xzf "/cache/$(basename "${PY_URL}")" -C "${STAGE}" # -> python/
PY="${STAGE}/python/bin/python3"
export PIP_DISABLE_PIP_VERSION_CHECK=1
[[ -n "${PIP_INDEX_URL:-}" ]] || unset PIP_INDEX_URL
mkdir -p /cache/pip
# Running on glibc 2.17 makes pip pick manylinux2014-compatible wheels only.
"${PY}" -m pip install --cache-dir /cache/pip --no-warn-script-location --only-binary=:all: \
  -r /src/requirements.txt

echo "==> slimming runtime"
PYLIB="${STAGE:?}/python/lib/python3.12"
for d in test idlelib tkinter turtledemo ensurepip turtle.py; do
  rm -rf "${PYLIB:?}/${d}"
done
rm -f "${PYLIB:?}"/lib-dynload/_tkinter* "${PYLIB:?}"/lib-dynload/_test* "${PYLIB:?}"/lib-dynload/xxlimited*
for d in "${STAGE:?}"/python/lib/tcl* "${STAGE:?}"/python/lib/tk* "${STAGE:?}"/python/lib/itcl* \
  "${STAGE:?}"/python/lib/thread* "${STAGE:?}"/python/lib/libtcl* "${STAGE:?}"/python/lib/libtk* \
  "${STAGE:?}/python/share"; do
  [[ -e "${d}" ]] && rm -rf "${d}"
done
find "${PYLIB}/site-packages" -depth -type d \( -name tests -o -name test -o -name testing \) \
  -exec rm -rf {} + 2>/dev/null || true
find "${STAGE}/python" -name __pycache__ -type d -prune -exec rm -rf {} +
find "${STAGE}/python" -name '*.pyc' -delete

# ------------------------------------------------------------ app files
echo "==> app files"
cp -r /src/netx_api /src/alembic /src/skills "${STAGE}/"
for f in pyproject.toml requirements.txt alembic.ini README.md LICENSE SECURITY.md; do
  if [[ -f "/src/${f}" ]]; then cp "/src/${f}" "${STAGE}/"; fi
done
find "${STAGE}/netx_api" "${STAGE}/alembic" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "${STAGE:?}/netx_api/tests"
mkdir -p "${STAGE}/web"
cp -r /webdist "${STAGE}/web/dist"
mkdir -p "${STAGE}/packaging/linux"
for f in lib.sh netxctl netx_admin.py systemd logrotate.netx README.md; do
  cp -r "/src/packaging/linux/${f}" "${STAGE}/packaging/linux/"
done
cp /src/packaging/linux/install.sh "${STAGE}/install.sh"
# Sources may come from a Windows checkout: force LF + exec bits.
sed -i 's/\r$//' "${STAGE}/install.sh" "${STAGE}"/packaging/linux/{lib.sh,netxctl,netx_admin.py,logrotate.netx} \
  "${STAGE}"/packaging/linux/systemd/*
chmod 0755 "${STAGE}/install.sh" "${STAGE}/packaging/linux/netxctl"
cat >"${STAGE}/version.json" <<JSON
{"version": "${VERSION}", "arch": "${ARCH}", "pg_major": ${PG_MAJOR}, "pg_version": "${PG_VERSION}", "python": "${PY_VERSION}", "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
JSON

# ------------------------------------------------------------ checks
(cd "${STAGE}" && PYTHONPATH="${STAGE}" "${PY}" -c "import netx_api.main, psycopg, cryptography, netmiko, bcrypt, psutil; print('import smoke OK')")
find "${STAGE}" -name __pycache__ -type d -prune -exec rm -rf {} +
"${STAGE}/pgsql/bin/postgres" --version
bash /src/packaging/linux/check_bundle.sh "${STAGE}" "${MAX_GLIBC}"

# ------------------------------------------------------------ archive
echo "==> archive"
tar --owner=0 --group=0 --numeric-owner -czf "/out/${NAME}.tar.gz" -C "${BUILD}" "${NAME}"
(cd /out && sha256sum "${NAME}.tar.gz" >"${NAME}.tar.gz.sha256")
chown "${HOST_UID:-0}:${HOST_GID:-0}" "/out/${NAME}.tar.gz" "/out/${NAME}.tar.gz.sha256" "${PG_TGZ}" 2>/dev/null || true
chown -R "${HOST_UID:-0}:${HOST_GID:-0}" /cache/pip 2>/dev/null || true
du -sh "${STAGE}" "/out/${NAME}.tar.gz"
