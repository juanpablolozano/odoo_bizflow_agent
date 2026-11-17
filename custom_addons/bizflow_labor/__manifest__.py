{
    "name": "BizFlow - Mano de obra de proyectos",
    "summary": "Liquidación semanal de horas, pago a empleados y costo de mano de obra por proyecto",
    "description": """
Liquidación de mano de obra para proyectos de construcción
==========================================================

* Tarifa de pago y factor de costo cargado por empleado (el costo por hora se calcula solo).
* Periodos de liquidación (semanal o quincenal) generados desde los partes de horas.
* Clasificación de horas normales, suplementarias (+50%) y extraordinarias (+100%).
* Aprobación con bloqueo de los partes de horas liquidados.
* Contabilización (gasto / sueldos por pagar) sin duplicar el costo analítico del proyecto.
* Recargo por horas extra cargado al proyecto donde se trabajaron.
* Pago a empleados y rol de pago en PDF.
* Equipo de obra por proyecto, carga semanal de cuadrilla y alertas.
""",
    "version": "20.0.1.0.0",
    "category": "Services/Timesheets",
    "author": "BizFlow",
    "license": "LGPL-3",
    "depends": ["hr_timesheet", "account", "project"],
    "data": [
        "security/ir.access.csv",
        "data/ir_cron.xml",
        "report/labor_reports.xml",
        "report/labor_payslip_templates.xml",
        "wizard/labor_pay_wizard_views.xml",
        "wizard/labor_weekly_entry_views.xml",
        "views/account_analytic_line_views.xml",
        "views/labor_period_views.xml",
        "views/labor_line_views.xml",
        "views/hr_employee_views.xml",
        "views/project_project_views.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
