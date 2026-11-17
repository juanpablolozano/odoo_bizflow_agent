from odoo import fields, models


class LaborPayWizard(models.TransientModel):
    _name = "bizflow.labor.pay.wizard"
    _description = "Pagar liquidación de mano de obra"

    period_id = fields.Many2one("bizflow.labor.period", required=True, readonly=True)
    company_id = fields.Many2one(related="period_id.company_id")
    currency_id = fields.Many2one(related="period_id.currency_id")
    amount_total = fields.Monetary(related="period_id.amount_total", string="Total a pagar")
    employee_count = fields.Integer(compute="_compute_employee_count", string="Empleados")
    journal_id = fields.Many2one(
        "account.journal",
        "Pagar desde",
        required=True,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
        default=lambda self: self.env.company.labor_payment_journal_id,
    )
    payment_date = fields.Date("Fecha de pago", required=True, default=fields.Date.context_today)

    def _compute_employee_count(self):
        for wizard in self:
            wizard.employee_count = len(wizard.period_id.line_ids.filtered("move_id"))

    def action_pay(self):
        self.period_id._register_payments(self.journal_id, self.payment_date)
        return {"type": "ir.actions.act_window_close"}
