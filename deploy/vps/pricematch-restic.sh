#!/usr/bin/env bash
set -euo pipefail
umask 077

ENV_FILE="${RESTIC_ENV_FILE:-/etc/pricematch/restic.env}"
if [[ ! -r "$ENV_FILE" ]]; then
  echo "Configurația restic nu poate fi citită: $ENV_FILE" >&2
  exit 1
fi

set -a
# Fișierul este administrat de root și conține exclusiv variabile pentru restic.
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${RESTIC_REPOSITORY:?Lipsește RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD_FILE:?Lipsește RESTIC_PASSWORD_FILE}"
if [[ ! -r "$RESTIC_PASSWORD_FILE" ]]; then
  echo "Fișierul parolei restic nu poate fi citit." >&2
  exit 1
fi

exec /usr/bin/restic "$@"
