import calendar
from collections import defaultdict
from datetime import datetime, time, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

STATES = [
    ("draft", "Borrador"),
    ("approved", "Aprobado"),
    ("posted", "Contabilizado"),
    ("paid", "Pagado"),
    ("cancel", "Cancelado"),
]


def period_range(ref_date, period_type):
    """Devuelve (inicio, fin) del periodo que contiene ref_date."""
    if period_type == "biweekly":
        if ref_date.day <= 15:
            return ref_date.replace(day=1), ref_date.replace(day=15)
        last = calendar.monthrange(ref_date.year, ref_date.month)[1]
        return ref_date.replace(day=16), ref_date.replace(day=last)
    start = ref_date - timedelta(days=ref_date.weekday())
    return start, start + timedelta(days=6)


class LaborPeriod(models.Model):
    _name = "bizflow.labor.period"
    _description = "Periodo de liquidación de mano de obra"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_from desc, id desc"
    _check_company_auto = True

    name = fields.Char("Periodo", compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, readonly=True
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date("Desde", required=True, default=lambda self: self._default_dates()[0], tracking=True)
    date_to = fields.Date("Hasta", required=True, default=lambda self: self._default_dates()[1], tracking=True)
    state = fields.Selection(STATES, default="draft", required=True, tracking=True, copy=False)
    line_ids = fields.One2many("bizflow.labor.line", "period_id", string="Empleados", copy=False)
    project_line_ids = fields.One2many("bizflow.labor.line.project", "period_id", string="Por proyecto")
    timesheet_ids = fields.One2many(
        "account.analytic.line", "labor_period_id", string="Partes de horas",
        domain=[("project_id", "!=", False)],
    )
    premium_line_ids = fields.One2many(
        "account.analytic.line", "labor_period_id", string="Recargos analíticos",
        domain=[("project_id", "=", False)],
    )
    note = fields.Html("Notas")

    hours_normal = fields.Float("Horas normales", compute="_compute_totals", store=True)
    hours_overtime = fields.Float("Horas suplementarias", compute="_compute_totals", store=True)
    hours_extra = fields.Float("Horas extraordinarias", compute="_compute_totals", store=True)
    hours_total = fields.Float("Horas totales", compute="_compute_totals", store=True)
    amount_total = fields.Monetary("Total a pagar", compute="_compute_totals", store=True)
    cost_premium = fields.Monetary("Recargos al proyecto", compute="_compute_totals", store=True)
    warning_count = fields.Integer("Alertas", compute="_compute_totals", store=True)
    move_count = fields.Integer(compute="_compute_counts")
    payment_count = fields.Integer(compute="_compute_counts")
    timesheet_count = fields.Integer(compute="_compute_counts")

    # ------------------------------------------------------------------ defaults / computes
    @api.model
    def _default_dates(self):
        company = self.env.company
        today = fields.Date.context_today(self)
        start, _end = period_range(today, company.labor_period_type)
        return period_range(start - timedelta(days=1), company.labor_period_type)

    @api.depends("date_from", "date_to")
    def _compute_name(self):
        for period in self:
            if not (period.date_from and period.date_to):
                period.name = _("Nuevo periodo")
                continue
            fmt = "%d/%m/%Y"
            span = f"{period.date_from.strftime(fmt)} – {period.date_to.strftime(fmt)}"
            if (period.date_to - period.date_from).days == 6 and period.date_from.weekday() == 0:
                iso = period.date_from.isocalendar()
                period.name = _("Semana %(week)s/%(year)s (%(span)s)", week=iso[1], year=iso[0], span=span)
            else:
                period.name = span

    @api.depends(
        "line_ids.hours_normal", "line_ids.hours_overtime", "line_ids.hours_extra",
        "line_ids.amount_total", "line_ids.cost_premium", "line_ids.warning",
    )
    def _compute_totals(self):
        for period in self:
            lines = period.line_ids
            period.hours_normal = sum(lines.mapped("hours_normal"))
            period.hours_overtime = sum(lines.mapped("hours_overtime"))
            period.hours_extra = sum(lines.mapped("hours_extra"))
            period.hours_total = period.hours_normal + period.hours_overtime + period.hours_extra
            period.amount_total = sum(lines.mapped("amount_total"))
            period.cost_premium = sum(lines.mapped("cost_premium"))
            period.warning_count = len(lines.filtered("warning"))

    def _compute_counts(self):
        for period in self:
            period.move_count = len(period.line_ids.move_id)
            period.payment_count = len(period.line_ids.payment_id)
            period.timesheet_count = len(period.timesheet_ids)

    @api.constrains("date_from", "date_to", "company_id", "state")
    def _check_dates(self):
        for period in self:
            if period.date_to < period.date_from:
                raise ValidationError(_("La fecha final debe ser posterior a la inicial."))
            overlap = self.search_count([
                ("id", "!=", period.id),
                ("company_id", "=", period.company_id.id),
                ("state", "!=", "cancel"),
                ("date_from", "<=", period.date_to),
                ("date_to", ">=", period.date_from),
            ])
            if overlap and period.state != "cancel":
                raise ValidationError(_("Ya existe un periodo de liquidación que se cruza con estas fechas."))

    # ------------------------------------------------------------------ helpers
    def _check_state(self, *states):
        for period in self:
            if period.state not in states:
                raise UserError(_(
                    "El periodo %(name)s está en estado '%(state)s' y no permite esta acción.",
                    name=period.name, state=dict(STATES)[period.state],
                ))

    def _check_manager(self):
        if not (self.env.su or self.env.user.has_group("hr_timesheet.group_timesheet_manager")):
            raise AccessError(_("Solo un administrador de partes de horas puede realizar esta acción."))

    def _get_holiday_dates(self, employee):
        """Fechas de feriados (ausencias globales del calendario) dentro del periodo."""
        self.ensure_one()
        calendar_ids = [False] + employee.resource_calendar_id.ids
        leaves = self.env["resource.calendar.leaves"].sudo().search([
            ("resource_id", "=", False),
            ("calendar_id", "in", calendar_ids),
            ("company_id", "in", [False, self.company_id.id]),
            ("date_from", "<=", datetime.combine(self.date_to, time.max)),
            ("date_to", ">=", datetime.combine(self.date_from, time.min)),
        ])
        days = set()
        for leave in leaves:
            day = max(leave.date_from.date(), self.date_from)
            while day <= min(leave.date_to.date(), self.date_to):
                days.add(day)
                day += timedelta(days=1)
        return days

    def _split_timesheets(self, employee, timesheets):
        """Reparte cada parte de horas en (normales, suplementarias, extraordinarias).

        Reglas: en feriados y (si así se configura) fines de semana todas las horas son
        extraordinarias; en días laborables, lo que excede la jornada diaria es suplementario.
        Los partes se procesan en orden cronológico, así el exceso cae en los últimos del día.
        """
        self.ensure_one()
        company = self.company_id
        daily = company.labor_daily_hours or 8.0
        holidays = self._get_holiday_dates(employee)
        used = defaultdict(float)
        result = []
        for ts in timesheets.sorted(lambda t: (t.date, t.id)):
            hours = ts.unit_amount
            if hours <= 0:
                continue
            day = ts.date
            if day in holidays or (company.labor_weekend_is_extra and day.weekday() >= 5):
                result.append((ts, 0.0, 0.0, hours))
                continue
            normal = min(hours, max(daily - used[day], 0.0))
            used[day] += hours
            result.append((ts, normal, hours - normal, 0.0))
        return result

    def _prepare_line_vals(self, employee, timesheets):
        self.ensure_one()
        company = self.company_id
        of, ef = company.labor_overtime_factor, company.labor_extra_factor
        projects = defaultdict(lambda: {"n": 0.0, "o": 0.0, "e": 0.0, "cost": 0.0, "premium": 0.0})
        weekly_overtime = defaultdict(float)
        for ts, normal, overtime, extra in self._split_timesheets(employee, timesheets):
            cost_per_hour = -ts.amount / ts.unit_amount if ts.unit_amount else 0.0
            p = projects[ts.project_id.id]
            p["n"] += normal
            p["o"] += overtime
            p["e"] += extra
            p["cost"] += -ts.amount
            p["premium"] += overtime * cost_per_hour * (of - 1) + extra * cost_per_hour * (ef - 1)
            weekly_overtime[ts.date.isocalendar()[:2]] += overtime

        emp = employee.sudo()
        warnings = []
        if not emp.labor_pay_rate:
            warnings.append(_("Sin tarifa de pago configurada."))
        if not emp.work_contact_id:
            warnings.append(_("Sin contacto de trabajo (necesario para contabilizar el pago)."))
        cap = company.labor_max_overtime_week
        for (year, week), hours in sorted(weekly_overtime.items()):
            if cap and hours > cap:
                warnings.append(_(
                    "Semana %(week)s/%(year)s: %(hours).1f h suplementarias (tope %(cap).0f h).",
                    week=week, year=year, hours=hours, cap=cap,
                ))
        currency = company.currency_id
        return {
            "period_id": self.id,
            "employee_id": employee.id,
            "pay_rate": emp.labor_pay_rate,
            "overtime_factor": of,
            "extra_factor": ef,
            "hours_normal": sum(p["n"] for p in projects.values()),
            "hours_overtime": sum(p["o"] for p in projects.values()),
            "hours_extra": sum(p["e"] for p in projects.values()),
            "warning": "\n".join(warnings) or False,
            "project_line_ids": [
                (0, 0, {
                    "project_id": project_id,
                    "hours_normal": p["n"],
                    "hours_overtime": p["o"],
                    "hours_extra": p["e"],
                    "cost_timesheet": currency.round(p["cost"]),
                    "cost_premium": currency.round(p["premium"]),
                })
                for project_id, p in projects.items()
            ],
        }

    # ------------------------------------------------------------------ actions
    def action_generate(self):
        """(Re)calcula las líneas a partir de los partes de horas del periodo."""
        for period in self:
            period._check_state("draft")
            bypass = self.env["account.analytic.line"].sudo().with_context(bizflow_labor_bypass=True)
            period.line_ids.unlink()
            bypass.search([("labor_period_id", "=", period.id)]).labor_period_id = False
            timesheets = bypass.search([
                ("project_id", "!=", False),
                ("employee_id", "!=", False),
                ("company_id", "=", period.company_id.id),
                ("date", ">=", period.date_from),
                ("date", "<=", period.date_to),
                ("labor_period_id", "=", False),
            ])
            vals_list = [
                period._prepare_line_vals(employee, timesheets.filtered(lambda t, e=employee: t.employee_id == e))
                for employee in timesheets.employee_id
            ]
            self.env["bizflow.labor.line"].create(vals_list)
            timesheets.labor_period_id = period
            period.message_post(body=_(
                "Liquidación calculada: %(emp)s empleados, %(hours).1f horas.",
                emp=len(vals_list), hours=period.hours_total,
            ))
        return True

    def action_approve(self):
        self._check_manager()
        for period in self:
            period._check_state("draft")
            if not period.line_ids:
                raise UserError(_("No hay horas para liquidar en %s. Pulse 'Calcular' primero.", period.name))
            missing = period.line_ids.filtered(lambda l: l.pay_rate <= 0).employee_id
            if missing:
                raise UserError(_(
                    "Configure la tarifa de pago de: %s", ", ".join(missing.mapped("name"))
                ))
            period.state = "approved"
            period.message_post(body=_("Periodo aprobado. Los partes de horas quedaron bloqueados."))
        return True

    def action_reset_draft(self):
        self._check_manager()
        self._check_state("approved", "cancel")
        for period in self:
            if period.state == "cancel":
                # Reabrir: volver a tomar los partes de horas libres
                period.state = "draft"
                period.action_generate()
            else:
                period.state = "draft"
        return True

    def action_post(self):
        """Asiento por empleado (gasto MO / sueldos por pagar, sin analítica) y recargos al proyecto."""
        self._check_manager()
        for period in self:
            period._check_state("approved")
            company = period.company_id
            if company.chart_template and not (
                company.labor_journal_id and company.labor_expense_account_id
                and company.labor_payable_account_id and company.labor_payment_journal_id
            ):
                company._bizflow_labor_setup_accounting()
            if not (company.labor_journal_id and company.labor_expense_account_id and company.labor_payable_account_id):
                raise UserError(_("Configure el diario y las cuentas de mano de obra en Ajustes > Partes de horas."))
            Move = self.env["account.move"].sudo().with_company(company)
            for line in period.line_ids.filtered(lambda l: l.amount_total > 0):
                partner = line.employee_id.sudo().work_contact_id
                if not partner:
                    raise UserError(_("El empleado %s no tiene contacto de trabajo.", line.employee_id.name))
                label = _("Mano de obra %(emp)s - %(period)s", emp=line.employee_id.name, period=period.name)
                move = Move.create({
                    "move_type": "entry",
                    "journal_id": company.labor_journal_id.id,
                    "date": period.date_to,
                    "ref": label,
                    "line_ids": [
                        (0, 0, {"name": label, "account_id": company.labor_expense_account_id.id,
                                "partner_id": partner.id, "debit": line.amount_total, "credit": 0.0}),
                        (0, 0, {"name": label, "account_id": company.labor_payable_account_id.id,
                                "partner_id": partner.id, "debit": 0.0, "credit": line.amount_total}),
                    ],
                })
                move.action_post()
                line.move_id = move
            period._create_premium_analytic_lines()
            period.state = "posted"
            period.message_post(body=_("Contabilizado: %s asientos.", len(period.line_ids.move_id)))
        return True

    def _create_premium_analytic_lines(self):
        """El costo normal ya está en los partes de horas; aquí solo se agrega el recargo por extras."""
        self.ensure_one()
        vals_list = []
        for pl in self.project_line_ids.filtered(lambda p: p.cost_premium and p.project_id.account_id):
            vals_list.append({
                "name": _("Recargo horas extra - %(emp)s - %(period)s", emp=pl.employee_id.name, period=self.name),
                "account_id": pl.project_id.account_id.id,
                "amount": -pl.cost_premium,
                "unit_amount": pl.hours_overtime + pl.hours_extra,
                "date": self.date_to,
                "company_id": self.company_id.id,
                "labor_period_id": self.id,
            })
        self.env["account.analytic.line"].sudo().with_context(bizflow_labor_bypass=True).create(vals_list)

    def action_unpost(self):
        """Revierte la contabilización (solo si aún no se pagó)."""
        self._check_manager()
        for period in self:
            period._check_state("posted")
            moves = period.line_ids.move_id.sudo()
            moves.button_draft()
            moves.button_cancel()
            period.line_ids.move_id = False
            period.premium_line_ids.sudo().with_context(bizflow_labor_bypass=True).unlink()
            period.state = "approved"
            period.message_post(body=_("Contabilización revertida."))
        return True

    def action_open_pay_wizard(self):
        self.ensure_one()
        self._check_state("posted")
        return {
            "type": "ir.actions.act_window",
            "name": _("Pagar mano de obra"),
            "res_model": "bizflow.labor.pay.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_period_id": self.id},
        }

    def _register_payments(self, journal, payment_date):
        self._check_manager()
        for period in self:
            period._check_state("posted")
            for line in period.line_ids.filtered("move_id"):
                payable = line.move_id.sudo().line_ids.filtered(
                    lambda ml: ml.account_id == period.company_id.labor_payable_account_id and not ml.reconciled
                )
                if not payable:
                    continue
                wizard = self.env["account.payment.register"].sudo().with_context(
                    active_model="account.move.line", active_ids=payable.ids
                ).create({"journal_id": journal.id, "payment_date": payment_date})
                line.payment_id = wizard._create_payments()[:1]
            period.state = "paid"
            period.message_post(body=_(
                "Pagado el %(date)s desde %(journal)s.", date=payment_date, journal=journal.display_name
            ))
        return True

    def action_cancel(self):
        self._check_manager()
        self._check_state("draft", "approved")
        bypass = self.env["account.analytic.line"].sudo().with_context(bizflow_labor_bypass=True)
        for period in self:
            bypass.search([("labor_period_id", "=", period.id)]).labor_period_id = False
            period.state = "cancel"
        return True

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(p.state not in ("draft", "cancel") for p in self):
            raise UserError(_("Solo se pueden eliminar periodos en borrador o cancelados."))
        self.env["account.analytic.line"].sudo().with_context(bizflow_labor_bypass=True).search(
            [("labor_period_id", "in", self.ids)]
        ).labor_period_id = False

    # ------------------------------------------------------------------ smart buttons
    def action_view_timesheets(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("hr_timesheet.act_hr_timesheet_line")
        action.update({
            "search_view_id": [self.env.ref("bizflow_labor.hr_timesheet_line_search_labor").id, "search"],
            "domain": [("labor_period_id", "=", self.id), ("project_id", "!=", False)],
            "context": {"create": False, "search_default_groupby_employee": 1},
        })
        return action

    def action_view_moves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Asientos"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.line_ids.move_id.ids)],
            "context": {"create": False},
        }

    def action_view_payments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pagos"),
            "res_model": "account.payment",
            "view_mode": "list,form",
            "domain": [("id", "in", self.line_ids.payment_id.ids)],
            "context": {"create": False},
        }

    def action_print_payslips(self):
        return self.env.ref("bizflow_labor.action_report_labor_period").report_action(self)

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_create_previous_period(self):
        """Crea y calcula el periodo anterior si todavía no existe (se ejecuta a diario)."""
        today = fields.Date.context_today(self)
        for company in self.env["res.company"].search([]):
            start, _end = period_range(today, company.labor_period_type)
            date_from, date_to = period_range(start - timedelta(days=1), company.labor_period_type)
            exists = self.search_count([
                ("company_id", "=", company.id), ("state", "!=", "cancel"),
                ("date_from", "<=", date_to), ("date_to", ">=", date_from),
            ])
            if not exists:
                period = self.with_company(company).create(
                    {"company_id": company.id, "date_from": date_from, "date_to": date_to}
                )
                period.action_generate()
