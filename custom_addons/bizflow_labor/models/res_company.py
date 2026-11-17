from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    labor_period_type = fields.Selection(
        [("weekly", "Semanal"), ("biweekly", "Quincenal")],
        string="Periodicidad de pago",
        default="weekly",
        required=True,
    )
    labor_daily_hours = fields.Float("Jornada diaria (h)", default=8.0)
    labor_weekly_hours = fields.Float("Jornada semanal (h)", default=40.0)
    labor_overtime_factor = fields.Float(
        "Factor horas suplementarias",
        default=1.5,
        help="Multiplicador de la tarifa para horas que exceden la jornada diaria en días laborables.",
    )
    labor_extra_factor = fields.Float(
        "Factor horas extraordinarias",
        default=2.0,
        help="Multiplicador de la tarifa para horas en fin de semana o feriado.",
    )
    labor_weekend_is_extra = fields.Boolean(
        "Fin de semana = extraordinarias",
        default=True,
        help="Si está activo, toda hora trabajada en sábado o domingo se paga como extraordinaria.",
    )
    labor_cost_factor = fields.Float(
        "Factor de costo cargado",
        default=1.40,
        help="Costo por hora del empleado = tarifa de pago × este factor "
        "(aporte patronal, décimos, vacaciones, fondos de reserva). Confirmar con contabilidad.",
    )
    labor_max_overtime_week = fields.Float("Tope semanal de suplementarias (h)", default=12.0)
    labor_journal_id = fields.Many2one(
        "account.journal",
        string="Diario de liquidaciones",
        domain="[('type', '=', 'general'), ('company_id', '=', id)]",
    )
    labor_payment_journal_id = fields.Many2one(
        "account.journal",
        string="Diario de pago",
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', id)]",
    )
    labor_expense_account_id = fields.Many2one(
        "account.account",
        string="Cuenta de gasto de mano de obra",
        domain="[('account_type', 'in', ('expense', 'expense_direct_cost'))]",
    )
    labor_payable_account_id = fields.Many2one(
        "account.account",
        string="Cuenta de sueldos por pagar",
        domain="[('account_type', '=', 'liability_payable')]",
    )

    def _bizflow_labor_setup_accounting(self):
        """Crea (si faltan) las cuentas y asigna los diarios por defecto."""
        Account = self.env["account.account"].sudo()
        Journal = self.env["account.journal"].sudo()
        for company in self:
            acc_ctx = Account.with_company(company)
            if not company.labor_expense_account_id:
                company.labor_expense_account_id = acc_ctx.search(
                    [("code", "=", "630100"), ("company_ids", "in", company.id)], limit=1
                ) or acc_ctx.create({
                    "name": "Mano de obra directa - obras",
                    "code": "630100",
                    "account_type": "expense_direct_cost",
                    "company_ids": [(6, 0, company.ids)],
                })
            if not company.labor_payable_account_id:
                company.labor_payable_account_id = acc_ctx.search(
                    [("code", "=", "230500"), ("company_ids", "in", company.id)], limit=1
                ) or acc_ctx.create({
                    "name": "Sueldos por pagar - obra",
                    "code": "230500",
                    "account_type": "liability_payable",
                    "reconcile": True,
                    "company_ids": [(6, 0, company.ids)],
                })
            if not company.labor_journal_id:
                company.labor_journal_id = Journal.search(
                    [("type", "=", "general"), ("company_id", "=", company.id), ("code", "=", "MISC")], limit=1
                ) or Journal.search([("type", "=", "general"), ("company_id", "=", company.id)], limit=1)
            if not company.labor_payment_journal_id:
                company.labor_payment_journal_id = Journal.search(
                    [("type", "=", "bank"), ("company_id", "=", company.id)], limit=1
                )
