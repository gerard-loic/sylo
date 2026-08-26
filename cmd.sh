#!/usr/bin/env bash
# Point d'entrée générique pour les scripts de scripts/.
# Usage : ./cmd.sh <commande> [arguments...]
#   ex : ./cmd.sh create_role "Administrateur" ADMIN
#        ./cmd.sh sync_permissions --dry-run
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

list_commands() {
    echo "Commandes disponibles :" >&2
    for f in scripts/*.py; do
        echo "  - $(basename "$f" .py)" >&2
    done
}

if [ -z "${1:-}" ]; then
    echo "Usage: ./cmd.sh <commande> [arguments...]" >&2
    echo >&2
    list_commands
    exit 1
fi

COMMAND="$1"
shift
SCRIPT="scripts/${COMMAND}.py"

if [ ! -f "$SCRIPT" ]; then
    echo "Commande inconnue : ${COMMAND}" >&2
    echo >&2
    list_commands
    exit 1
fi

if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

python3 "$SCRIPT" "$@"
