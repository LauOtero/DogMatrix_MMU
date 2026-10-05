#!/usr/bin/env bash
# Dog Matrix MMU - instalador de despliegue.
#
# Uso:
#   ./install.sh preflight [--dest DIR]
#   ./install.sh wizard    [--profile ID] [--dest DIR] [--headless] [--dry-run]
#   ./install.sh generate  [--profile ID] [--dest DIR] [--dry-run]
#   ./install.sh validate  [--dest DIR]
#   ./install.sh apply     [--dest DIR]
#   ./install.sh rollback  [--snapshot ID] [--dest DIR]
#   ./install.sh migrate   [--source PATH] [--dest DIR] [--dry-run]
#   ./install.sh doctor
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 no encontrado en PATH" >&2
    exit 127
fi

# Exportar la raíz del proyecto para que el instalador localice perfiles/plantillas.
export DM_PROJECT_ROOT="${DM_PROJECT_ROOT:-$SCRIPT_DIR}"

exec python3 -m installer.cli "$@"
