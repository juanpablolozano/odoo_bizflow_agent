#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/venv/bin/python" ]]; then
  echo "Missing venv. Create it with: python3.12 -m venv venv && ./venv/bin/pip install -r odoo/requirements.txt"
  exit 1
fi

exec "$ROOT/venv/bin/python" "$ROOT/odoo/odoo-bin" -c "$ROOT/odoo.conf" "$@"
