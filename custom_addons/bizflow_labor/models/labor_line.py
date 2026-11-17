from collections import defaultdict

from odoo import api, fields, models

WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


class LaborLine(models.Model):
    _name = "bizflow.labor.line"
    _description = "Liquidación de mano de obra por empleado"
    _order = "period_id desc, employee_id"
    _rec_name = "employee_id"

    period_id = fields.Many2one("bizflow.labor.period", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="period_id.company_id", store=True)
    currency_id = fields.Many2one(related="period_id.currency_id")
    state = fields.Selection(related="period_id.state", store=True, string="Estado")
    date_from = fields.Date(related="period_id.date_from", store=True)
    date_to = fields.Date(related="period_id.date_to", store=True)
    employee_id = fields.Many2one("hr.employee", "Empleado", required=True, index=True)
    job_title = fields.Char(related="employee_id.job_title", string="Cargo")

    pay_rate = fields.Monetary("Tarifa/h")
    overtime_factor = fields.Float("Factor suplem.", default=1.5)
    extra_factor = fields.Float("Factor extraord.", default=2.0)
    hours_normal = fields.Float("H. normales")
    hours_overtime = fields.Float("H. suplem.")
    hours_extra = fields.Float("H. extraord.")
    hours_total = fields.Float("H. totales", compute="_compute_amounts", store=True)
    amount_normal = fields.Monetary("Pago normales", compute="_compute_amounts", store=True)
    amount_overtime = fields.Monetary("Pago suplem.", compute="_compute_amounts", store=True)
    amount_extra = fields.Monetary("Pago extraord.", compute="_compute_amounts", store=True)
    amount_total = fields.Monetary("Total a pagar", compute="_compute_amounts", store=True)

    project_line_ids = fields.One2many("bizflow.labor.line.project", "line_id", string="Por proyecto")
    cost_timesheet = fields.Monetary("Costo horas", compute="_compute_costs", store=True)
    cost_premium = fields.Monetary("Recargo extras", compute="_compute_costs", store=True)
    warning = fields.Text("Alertas")
    move_id = fields.Many2one("account.move", "Asiento", readonly=True, copy=False)
    payment_id = fields.Many2one("account.payment", "Pago", readonly=True, copy=False)

    _unique_employee_period = models.Constraint(
        "UNIQUE(period_id, employee_id)", "Un empleado solo puede aparecer una vez por periodo."
    )

    @api.depends("pay_rate", "overtime_factor", "extra_factor", "hours_normal", "hours_overtime", "hours_extra")
    def _compute_amounts(self):
        for line in self:
            cur = line.currency_id or line.env.company.currency_id
            line.hours_total = line.hours_normal + line.hours_overtime + line.hours_extra
            line.amount_normal = cur.round(line.hours_normal * line.pay_rate)
            line.amount_overtime = cur.round(line.hours_overtime * line.pay_rate * line.overtime_factor)
            line.amount_extra = cur.round(line.hours_extra * line.pay_rate * line.extra_factor)
            line.amount_total = line.amount_normal + line.amount_overtime + line.amount_extra

    @api.depends("project_line_ids.cost_timesheet", "project_line_ids.cost_premium")
    def _compute_costs(self):
        for line in self:
            line.cost_timesheet = sum(line.project_line_ids.mapped("cost_timesheet"))
            line.cost_premium = sum(line.project_line_ids.mapped("cost_premium"))

    def _get_timesheets(self):
        self.ensure_one()
        return self.period_id.timesheet_ids.filtered(lambda t: t.employee_id == self.employee_id)

    def _get_daily_summary(self):
        """Detalle por día para el rol de pago."""
        self.ensure_one()
        days = defaultdict(lambda: {"projects": set(), "n": 0.0, "o": 0.0, "e": 0.0})
        for ts, normal, overtime, extra in self.period_id._split_timesheets(self.employee_id, self._get_timesheets()):
            d = days[ts.date]
            d["projects"].add(ts.project_id.name)
            d["n"] += normal
            d["o"] += overtime
            d["e"] += extra
        return [
            {
                "date": day,
                "weekday": WEEKDAYS[day.weekday()],
                "projects": ", ".join(sorted(v["projects"])),
                "hours_normal": v["n"],
                "hours_overtime": v["o"],
                "hours_extra": v["e"],
                "hours_total": v["n"] + v["o"] + v["e"],
            }
            for day, v in sorted(days.items())
        ]

    def action_print_payslip(self):
        return self.env.ref("bizflow_labor.action_report_labor_payslip").report_action(self)


class LaborLineProject(models.Model):
    _name = "bizflow.labor.line.project"
    _description = "Costo de mano de obra por proyecto"
    _order = "date_from desc, project_id"

    line_id = fields.Many2one("bizflow.labor.line", required=True, ondelete="cascade", index=True)
    period_id = fields.Many2one(related="line_id.period_id", store=True, index=True)
    period_state = fields.Selection(related="line_id.period_id.state", store=True, string="Estado")
    date_from = fields.Date(related="line_id.period_id.date_from", store=True, string="Semana")
    employee_id = fields.Many2one(related="line_id.employee_id", store=True)
    company_id = fields.Many2one(related="line_id.company_id", store=True)
    currency_id = fields.Many2one(related="line_id.currency_id")
    project_id = fields.Many2one("project.project", "Proyecto", required=True, index=True)
    hours_normal = fields.Float("H. normales")
    hours_overtime = fields.Float("H. suplem.")
    hours_extra = fields.Float("H. extraord.")
    hours_extra_total = fields.Float("H. extra", compute="_compute_totals", store=True)
    hours_total = fields.Float("H. totales", compute="_compute_totals", store=True)
    cost_timesheet = fields.Monetary("Costo horas")
    cost_premium = fields.Monetary("Recargo extras")
    cost_total = fields.Monetary("Costo total", compute="_compute_totals", store=True)

    @api.depends("hours_normal", "hours_overtime", "hours_extra", "cost_timesheet", "cost_premium")
    def _compute_totals(self):
        for pl in self:
            pl.hours_extra_total = pl.hours_overtime + pl.hours_extra
            pl.hours_total = pl.hours_normal + pl.hours_extra_total
            pl.cost_total = pl.cost_timesheet + pl.cost_premium
