#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "Mediul Python lipsește. Urmează pașii de instalare din README.md."
  exit 1
fi

.venv/bin/python manage.py migrate --noinput
PORT="${PORT:-8010}"
echo "PriceMatch Local pornește la http://127.0.0.1:${PORT}"
exec .venv/bin/python manage.py runserver "127.0.0.1:${PORT}"
