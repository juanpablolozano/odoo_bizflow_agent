from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    labor_period_type = fields.Selection(related="company_id.labor_period_type", readonly=False)
    labor_daily_hours = fields.Float(related="company_id.labor_daily_hours", readonly=False)
    labor_weekly_hours = fields.Float(related="company_id.labor_weekly_hours", readonly=False)
    labor_overtime_factor = fields.Float(related="company_id.labor_overtime_factor", readonly=False)
    labor_extra_factor = fields.Float(related="company_id.labor_extra_factor", readonly=False)
    labor_weekend_is_extra = fields.Boolean(related="company_id.labor_weekend_is_extra", readonly=False)
    labor_cost_factor = fields.Float(related="company_id.labor_cost_factor", readonly=False)
    labor_max_overtime_week = fields.Float(related="company_id.labor_max_overtime_week", readonly=False)
    labor_journal_id = fields.Many2one(related="company_id.labor_journal_id", readonly=False)
    labor_payment_journal_id = fields.Many2one(related="company_id.labor_payment_journal_id", readonly=False)
    labor_expense_account_id = fields.Many2one(related="company_id.labor_expense_account_id", readonly=False)
    labor_payable_account_id = fields.Many2one(related="company_id.labor_payable_account_id", readonly=False)
