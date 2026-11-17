from odoo import api, fields, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    labor_pay_rate = fields.Monetary(
        "Tarifa de pago por hora",
        groups="hr.group_hr_user",
        tracking=True,
        help="Lo que se le paga al empleado por cada hora normal.",
    )
    labor_cost_factor = fields.Float(
        "Factor de costo cargado",
        groups="hr.group_hr_user",
        help="Déjelo en 0 para usar el factor de la empresa.",
    )
    labor_effective_cost_factor = fields.Float(
        "Factor aplicado", compute="_compute_labor_effective_cost_factor", groups="hr.group_hr_user"
    )

    @api.depends("labor_cost_factor", "company_id.labor_cost_factor")
    def _compute_labor_effective_cost_factor(self):
        for employee in self:
            employee.labor_effective_cost_factor = employee._labor_cost_factor()

    def _labor_cost_factor(self):
        self.ensure_one()
        return self.labor_cost_factor or self.company_id.labor_cost_factor or 1.0

    def _labor_sync_hourly_cost(self):
        """Costo por hora (el que usan los partes de horas) = tarifa de pago × factor cargado."""
        for employee in self.sudo():
            if employee.labor_pay_rate:
                cost = employee.currency_id.round(employee.labor_pay_rate * employee._labor_cost_factor())
                if employee.hourly_cost != cost:
                    employee.hourly_cost = cost

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        employees.filtered(lambda e: e.sudo().labor_pay_rate)._labor_sync_hourly_cost()
        return employees

    def write(self, vals):
        res = super().write(vals)
        if {"labor_pay_rate", "labor_cost_factor", "company_id"} & set(vals):
            self._labor_sync_hourly_cost()
        return res
