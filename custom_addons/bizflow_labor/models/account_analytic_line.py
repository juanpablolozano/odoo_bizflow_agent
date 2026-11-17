from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

PROTECTED_FIELDS = {
    "unit_amount", "date", "employee_id", "project_id", "task_id", "amount", "account_id", "labor_period_id",
}


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    labor_period_id = fields.Many2one(
        "bizflow.labor.period", "Liquidación", readonly=True, copy=False, index="btree_not_null",
    )
    labor_locked = fields.Boolean("Liquidado", compute="_compute_labor_locked")

    @api.depends("labor_period_id.state")
    def _compute_labor_locked(self):
        for line in self:
            line.labor_locked = line.labor_period_id.state in ("approved", "posted", "paid")

    def _is_readonly(self):
        return super()._is_readonly() or self.labor_locked

    def _labor_check_locked(self, vals=None):
        if self.env.context.get("bizflow_labor_bypass"):
            return
        if vals is not None and not (PROTECTED_FIELDS & set(vals)):
            return
        locked = self.filtered("labor_locked")
        if locked:
            raise UserError(_(
                "No se pueden modificar horas ya liquidadas (periodo %s). "
                "Pida al administrador que regrese la liquidación a borrador.",
                ", ".join(locked.labor_period_id.mapped("name")),
            ))

    def write(self, vals):
        self._labor_check_locked(vals)
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_labor_locked(self):
        self._labor_check_locked()

    @api.constrains("project_id", "employee_id")
    def _check_labor_team(self):
        for line in self:
            project = line.project_id
            if project.labor_restrict_team and line.employee_id and line.employee_id not in project.labor_employee_ids:
                raise ValidationError(_(
                    "%(emp)s no pertenece al equipo de obra del proyecto %(project)s.",
                    emp=line.employee_id.name, project=project.name,
                ))
