#!/usr/bin/env bash
# Crea (o recrea) la base de datos de Odoo con todos los módulos y la configuración base.
#
#   scripts/setup_db.sh            # crea la BD si no existe, instala y configura
#   scripts/setup_db.sh --demo     # además carga los datos de demostración
#   scripts/setup_db.sh --reset    # BORRA la BD existente y la recrea desde cero
#
# Detén el servidor de Odoo antes de ejecutarlo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEMO=0
RESET=0
for arg in "$@"; do
  case "$arg" in
    --demo) DEMO=1 ;;
    --reset) RESET=1 ;;
    *) echo "Opción desconocida: $arg"; exit 1 ;;
  esac
done

conf() { awk -F' *= *' -v k="$1" '$1==k {print $2}' odoo.conf; }
DB="${ODOO_DB:-$(conf db_name)}"  # ODOO_DB=otra_bd para usar otra base
export PGHOST="$(conf db_host)" PGPORT="$(conf db_port)" PGUSER="$(conf db_user)" PGPASSWORD="$(conf db_password)"

MODULES="crm,website_crm,sale_management,sale_crm,project,sale_project,project_todo,hr_timesheet,sale_timesheet,\
sale_project_margin,purchase,project_purchase,purchase_requisition,account,project_account,hr_expense,\
project_hr_expense,spreadsheet_dashboard_sale,spreadsheet_dashboard_account,web_timeline,project_timeline,bizflow_labor"

ODOO_BIN=("$ROOT/venv/bin/python" "$ROOT/odoo/odoo-bin")
ODOO=("${ODOO_BIN[@]}" -c "$ROOT/odoo.conf")
ODOO_SHELL=("${ODOO_BIN[@]}" shell -c "$ROOT/odoo.conf")
mkdir -p .local

db_exists() { psql -d postgres -tAc "select 1 from pg_database where datname='$DB'" | grep -q 1; }

if [[ $RESET -eq 1 ]] && db_exists; then
  echo ">> Borrando base de datos $DB"
  dropdb "$DB"
  rm -rf ".local/odoo-data/filestore/$DB"
fi

echo ">> Instalando módulos en $DB"
: > .local/setup.log
"${ODOO[@]}" -d "$DB" -i "$MODULES" --without-demo=all --load-language=es_419 \
  --stop-after-init --http-port=8070 --logfile=.local/setup.log
if grep -qE " (ERROR|CRITICAL) " .local/setup.log; then
  echo "!! Errores durante la instalación, revisa .local/setup.log"; exit 1
fi

echo ">> Configuración base"
"${ODOO_SHELL[@]}" -d "$DB" --no-http --log-level=error < scripts/configure_base.py

if [[ $DEMO -eq 1 ]]; then
  echo ">> Datos de demostración"
  "${ODOO_SHELL[@]}" -d "$DB" --no-http --log-level=error < scripts/demo_data.py
  "${ODOO_SHELL[@]}" -d "$DB" --no-http --log-level=error < scripts/demo_subcontract.py
fi

echo ">> Listo. Arranca con ./start.sh"
