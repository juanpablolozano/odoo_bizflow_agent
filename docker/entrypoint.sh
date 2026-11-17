#!/bin/bash
set -euo pipefail

if [ -v PASSWORD_FILE ]; then
  PASSWORD="$(< "$PASSWORD_FILE")"
fi

: "${HOST:=${DB_PORT_5432_TCP_ADDR:=db}}"
: "${PORT:=${DB_PORT_5432_TCP_PORT:=5432}}"
: "${USER:=${DB_ENV_POSTGRES_USER:=${POSTGRES_USER:=odoo}}}"
: "${PASSWORD:=${DB_ENV_POSTGRES_PASSWORD:=${POSTGRES_PASSWORD:=odoo}}}"
: "${ODOO_DB:=odoo}"
: "${ODOO_MODULES:=bizflow_proposals}"
: "${ADMIN_PASSWD:=changeme}"

TEMPLATE_RC="${ODOO_RC:-/etc/odoo/odoo.conf}"
RUNTIME_RC="/var/lib/odoo/odoo.conf"
export ODOO_RC="$RUNTIME_RC"

mkdir -p /var/lib/odoo
cp "$TEMPLATE_RC" "$RUNTIME_RC"

if [ -n "${ADMIN_PASSWD}" ]; then
  sed -i "s|^admin_passwd *=.*|admin_passwd = ${ADMIN_PASSWD}|" "$RUNTIME_RC"
fi
if ! grep -qE '^\s*db_name\s*=' "$RUNTIME_RC"; then
  echo "db_name = ${ODOO_DB}" >> "$RUNTIME_RC"
else
  sed -i "s|^db_name *=.*|db_name = ${ODOO_DB}|" "$RUNTIME_RC"
fi

DB_ARGS=()
check_config() {
  local param="$1"
  local value="$2"
  local pg_var="${3:-}"
  if [ -n "$pg_var" ] && [[ -v "$pg_var" ]]; then
    return
  fi
  if ! grep -q -E "^\s*\b${param}\b\s*=" "$ODOO_RC"; then
    DB_ARGS+=("--${param}" "${value}")
  fi
}
check_config "db_host" "$HOST" "PGHOST"
check_config "db_port" "$PORT" "PGPORT"
check_config "db_user" "$USER" "PGUSER"
check_config "db_password" "$PASSWORD" "PGPASSWORD"

wait_for_postgres() {
  if command -v wait-for-psql.py >/dev/null 2>&1; then
    wait-for-psql.py "${DB_ARGS[@]}" --timeout=60
  else
    local i=0
    until PGPASSWORD="$PASSWORD" psql -h "$HOST" -p "$PORT" -U "$USER" -d postgres -c '\q' >/dev/null 2>&1; do
      i=$((i + 1))
      if [ "$i" -ge 60 ]; then
        echo "Postgres is unavailable after 60s" >&2
        exit 1
      fi
      sleep 1
    done
  fi
}

module_state() {
  local module="$1"
  PGPASSWORD="$PASSWORD" psql -h "$HOST" -p "$PORT" -U "$USER" -d "$ODOO_DB" -tAc \
    "SELECT state FROM ir_module_module WHERE name='${module}' LIMIT 1;" 2>/dev/null || true
}

db_exists() {
  PGPASSWORD="$PASSWORD" psql -h "$HOST" -p "$PORT" -U "$USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${ODOO_DB}'" 2>/dev/null | grep -q 1
}

bootstrap_modules() {
  local install_list=()
  local update_list=()
  local module
  local state

  IFS=',' read -r -a modules <<< "$ODOO_MODULES"
  for module in "${modules[@]}"; do
    module="$(echo "$module" | xargs)"
    [ -n "$module" ] || continue

    if ! db_exists; then
      install_list+=("$module")
      continue
    fi

    state="$(module_state "$module" | tr -d '[:space:]')"
    if [ "$state" = "installed" ]; then
      update_list+=("$module")
    else
      install_list+=("$module")
    fi
  done

  if [ "${#install_list[@]}" -gt 0 ]; then
    local install_csv
    install_csv=$(IFS=,; echo "${install_list[*]}")
    echo "Installing Odoo modules: ${install_csv}"
    odoo --stop-after-init -d "$ODOO_DB" -i "$install_csv" "${DB_ARGS[@]}"
  fi

  if [ "${#update_list[@]}" -gt 0 ]; then
    local update_csv
    update_csv=$(IFS=,; echo "${update_list[*]}")
    echo "Updating Odoo modules: ${update_csv}"
    odoo --stop-after-init -d "$ODOO_DB" -u "$update_csv" "${DB_ARGS[@]}"
  fi
}

case "${1:-}" in
  -- | odoo)
    shift
    if [[ "${1:-}" == "scaffold" ]]; then
      exec odoo "$@"
    fi
    wait_for_postgres
    bootstrap_modules
    exec odoo "$@" "${DB_ARGS[@]}"
    ;;
  -*)
    wait_for_postgres
    bootstrap_modules
    exec odoo "$@" "${DB_ARGS[@]}"
    ;;
  *)
    exec "$@"
    ;;
esac
