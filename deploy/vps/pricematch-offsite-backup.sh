#!/usr/bin/env bash
set -euo pipefail
umask 077

APP_ROOT="/srv/pricematch"
PYTHON="$APP_ROOT/.venv/bin/python"
RESTIC="$APP_ROOT/deploy/vps/pricematch-restic.sh"
STAGING_ROOT="$APP_ROOT/data/offsite-staging"

install -d -m 0700 "$STAGING_ROOT"
WORK_DIR="$(mktemp -d "$STAGING_ROOT/run.XXXXXX")"
cleanup() {
  rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT INT TERM

BACKUP_PATH="$($PYTHON "$APP_ROOT/manage.py" backup_pricematch --output "$WORK_DIR")"
if [[ "$BACKUP_PATH" != "$WORK_DIR"/pricematch-* || ! -f "$BACKUP_PATH/manifest.json" ]]; then
  echo "Backupul portabil nu a produs un director verificabil." >&2
  exit 1
fi

"$RESTIC" backup "$BACKUP_PATH" --tag pricematch --host "$(hostname)"
"$RESTIC" forget --tag pricematch --keep-daily 7 --keep-weekly 5 --keep-monthly 12 --keep-yearly 3 --prune
"$RESTIC" check
