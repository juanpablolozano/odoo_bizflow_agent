from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

DAY_FIELDS = ["h0", "h1", "h2", "h3", "h4", "h5", "h6"]


def _monday(day):
    return day - timedelta(days=day.weekday())


class LaborWeeklyEntry(models.TransientModel):
    _name = "bizflow.labor.weekly.entry"
    _description = "Carga semanal de horas de cuadrilla"

    project_id = fields.Many2one("project.project", "Proyecto", required=True, domain=[("is_template", "=", False)])
    task_id = fields.Many2one(
        "project.task", "Tarea", required=True,
        domain="[('project_id', '=', project_id), ('is_template', '=', False)]",
    )
    week_start = fields.Date(
        "Semana del (lunes)", required=True, default=lambda self: _monday(fields.Date.context_today(self))
    )
    name = fields.Char("Descripción", required=True, default="Trabajo en obra")
    days_label = fields.Char(compute="_compute_days_label")
    line_ids = fields.One2many("bizflow.labor.weekly.entry.line", "wizard_id", string="Cuadrilla")
    total_hours = fields.Float("Total horas", compute="_compute_total")

    @api.depends("week_start")
    def _compute_days_label(self):
        names = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for wizard in self:
            start = _monday(wizard.week_start) if wizard.week_start else False
            wizard.days_label = " · ".join(
                f"{names[i]} {(start + timedelta(days=i)).strftime('%d/%m')}" for i in range(7)
            ) if start else ""

    @api.depends("line_ids.total")
    def _compute_total(self):
        for wizard in self:
            wizard.total_hours = sum(wizard.line_ids.mapped("total"))

    @api.onchange("project_id")
    def _onchange_project_id(self):
        if self.task_id.project_id != self.project_id:
            self.task_id = False
        if self.project_id and not self.line_ids:
            self.line_ids = [(0, 0, {"employee_id": e.id}) for e in self.project_id.labor_employee_ids]

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        project = self.env["project.project"].browse(res.get("project_id") or self.env.context.get("default_project_id"))
        if project and "line_ids" in fields_list and not res.get("line_ids"):
            res["line_ids"] = [(0, 0, {"employee_id": e.id}) for e in project.labor_employee_ids]
        return res

    def action_confirm(self):
        self.ensure_one()
        start = _monday(self.week_start)
        vals_list = []
        for line in self.line_ids:
            for i, fname in enumerate(DAY_FIELDS):
                hours = line[fname]
                if hours < 0:
                    raise UserError(_("Las horas no pueden ser negativas (%s).", line.employee_id.name))
                if hours:
                    vals_list.append({
                        "name": self.name,
                        "project_id": self.project_id.id,
                        "task_id": self.task_id.id,
                        "employee_id": line.employee_id.id,
                        "unit_amount": hours,
                        "date": start + timedelta(days=i),
                    })
        if not vals_list:
            raise UserError(_("No se ingresaron horas."))
        timesheets = self.env["account.analytic.line"].create(vals_list)
        return {
            "type": "ir.actions.act_window",
            "name": _("Horas registradas"),
            "res_model": "account.analytic.line",
            "view_mode": "list,form",
            "domain": [("id", "in", timesheets.ids)],
            "context": {"group_by": ["employee_id"]},
        }


class LaborWeeklyEntryLine(models.TransientModel):
    _name = "bizflow.labor.weekly.entry.line"
    _description = "Horas de un empleado en la carga semanal"

    wizard_id = fields.Many2one("bizflow.labor.weekly.entry", required=True, ondelete="cascade")
    employee_id = fields.Many2one("hr.employee", "Empleado", required=True)
    h0 = fields.Float("Lun")
    h1 = fields.Float("Mar")
    h2 = fields.Float("Mié")
    h3 = fields.Float("Jue")
    h4 = fields.Float("Vie")
    h5 = fields.Float("Sáb")
    h6 = fields.Float("Dom")
    total = fields.Float("Total", compute="_compute_total")

    @api.depends(*DAY_FIELDS)
    def _compute_total(self):
        for line in self:
            line.total = sum(line[f] for f in DAY_FIELDS)
