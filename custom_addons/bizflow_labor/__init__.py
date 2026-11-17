from . import models
from . import wizard


def post_init_hook(env):
    """Crea las cuentas contables de mano de obra en cada empresa con plan contable."""
    for company in env["res.company"].search([("chart_template", "!=", False)]):
        company._bizflow_labor_setup_accounting()
