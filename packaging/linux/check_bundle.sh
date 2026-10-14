#!/usr/bin/env bash
# Fail when any ELF in the stage needs glibc newer than MAX or a shared library
# that is neither in the bundle nor on the base-system allowlist.
#   check_bundle.sh <stage-dir> [max-glibc=2.17]
set -euo pipefail
STAGE="$1"
MAX="${2:-2.17}"
# Present on every glibc distro (libcrypt.so.1 only backs the unused _crypt module).
ALLOW=" libc.so.6 libm.so.6 libdl.so.2 libpthread.so.0 librt.so.1 libutil.so.1 libcrypt.so.1 libnsl.so.1 libresolv.so.2 ld-linux-x86-64.so.2 libgcc_s.so.1 libstdc++.so.6 "

is_elf() { [[ "$(head -c4 "$1" 2>/dev/null | od -An -c | tr -d ' ')" == '177ELF' ]]; }

declare -A HAVE=()
ELFS=()
while IFS= read -r -d '' f; do
  is_elf "${f}" && ELFS+=("${f}")
done < <(find "${STAGE}" -type f \( -name '*.so*' -o -perm -u+x \) -print0)
for f in "${ELFS[@]}"; do HAVE["$(basename "${f}")"]=1; done
while IFS= read -r -d '' l; do HAVE["$(basename "${l}")"]=1; done < <(find "${STAGE}" -type l -name '*.so*' -print0)

bad=0
for f in "${ELFS[@]}"; do
  top="$(objdump -T "${f}" 2>/dev/null | grep -o 'GLIBC_[0-9][0-9.]*' | sed 's/GLIBC_//' | sort -V | tail -n1 || true)"
  if [[ -n "${top}" && "$(printf '%s\n%s\n' "${top}" "${MAX}" | sort -V | tail -n1)" != "${MAX}" ]]; then
    echo "[FAIL] ${f#"${STAGE}"/} needs GLIBC_${top} (> ${MAX})"
    bad=1
  fi
  while IFS= read -r need; do
    need="${need##*/}" # $ORIGIN/../lib/libfoo.so -> libfoo.so
    [[ -z "${need}" || "${ALLOW}" == *" ${need} "* ]] && continue
    [[ -n "${HAVE[${need}]:-}" ]] && continue
    echo "[FAIL] ${f#"${STAGE}"/} needs ${need} (not bundled)"
    bad=1
  done < <(readelf -d "${f}" 2>/dev/null | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p')
done
echo "==> checked ${#ELFS[@]} ELF files (max GLIBC ${MAX})"
exit "${bad}"
