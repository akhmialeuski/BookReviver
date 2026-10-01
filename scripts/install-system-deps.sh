#!/usr/bin/env bash
# Install the system packages that BookReviver needs and that no Python or Node package can bring.
#
# Today that is DjVuLibre, whose command-line tools (djvused, djvudump, ddjvu) read DjVu books. Without it the
# application still starts and refuses every DjVu file, and the DjVu tests are skipped.
#
# Usage: sudo scripts/install-system-deps.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root: sudo $0" >&2
  exit 1
fi

if command -v zypper >/dev/null 2>&1; then
  zypper --non-interactive install djvulibre
elif command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install --yes --no-install-recommends djvulibre-bin
elif command -v dnf >/dev/null 2>&1; then
  dnf install --assumeyes djvulibre
else
  echo "No supported package manager found (zypper, apt-get, dnf). Install DjVuLibre by hand." >&2
  exit 1
fi

for tool in djvused djvudump ddjvu; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "DjVuLibre was installed but ${tool} is not on the PATH." >&2
    exit 1
  fi
done
echo "DjVuLibre is installed: djvused, djvudump and ddjvu are on the PATH."
