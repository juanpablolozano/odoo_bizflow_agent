from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    labor_employee_ids = fields.Many2many(
        "hr.employee",
        "bizflow_project_labor_employee_rel",
        "project_id",
        "employee_id",
        string="Equipo de obra",
        help="Empleados asignados a este proyecto.",
    )
    labor_restrict_team = fields.Boolean(
        "Solo el equipo registra horas",
        help="Si está activo, solo los empleados del equipo de obra pueden registrar horas en este proyecto.",
    )
    labor_cost_line_ids = fields.One2many("bizflow.labor.line.project", "project_id", string="Costo de mano de obra")
    labor_premium_cost = fields.Monetary(
        "Recargos por extras", compute="_compute_labor_costs", currency_field="currency_id"
    )
    labor_hours_overtime = fields.Float("Horas extra liquidadas", compute="_compute_labor_costs")

    def _compute_labor_costs(self):
        data = {
            project.id: (premium, hours)
            for project, premium, hours in self.env["bizflow.labor.line.project"]._read_group(
                [("project_id", "in", self.ids), ("period_state", "in", ("posted", "paid"))],
                ["project_id"],
                ["cost_premium:sum", "hours_extra_total:sum"],
            )
        }
        for project in self:
            project.labor_premium_cost, project.labor_hours_overtime = data.get(project.id, (0.0, 0.0))

    def action_open_labor_cost(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("bizflow_labor.action_labor_project_cost")
        action["domain"] = [("project_id", "=", self.id)]
        action["context"] = {"search_default_group_week": 1}
        return action

    def action_open_weekly_entry(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Carga semanal de cuadrilla",
            "res_model": "bizflow.labor.weekly.entry",
            "view_mode": "form",
            "target": "new",
            "context": {"default_project_id": self.id},
        }
