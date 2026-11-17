# -*- coding: utf-8 -*-
from odoo import api, fields, models


class BizflowProposalCost(models.Model):
    """Internal costs — never exposed in client PDF, AI public copy, or share portal."""

    _name = "bizflow.proposal.cost"
    _description = "Internal proposal cost rate"
    _order = "sequence, id"

    proposal_id = fields.Many2one(
        "bizflow.proposal",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    category = fields.Selection(
        [
            ("labor", "Labor"),
            ("material", "Material"),
            ("overhead", "Overhead"),
            ("equipment", "Equipment"),
            ("other", "Other"),
        ],
        default="labor",
        required=True,
    )
    quantity = fields.Float(default=1.0)
    unit = fields.Char(default="hour")
    rate = fields.Float(string="Internal rate")
    amount = fields.Float(compute="_compute_amount", store=True)
    notes = fields.Char()

    @api.depends("quantity", "rate")
    def _compute_amount(self):
        for rec in self:
            rec.amount = (rec.quantity or 0.0) * (rec.rate or 0.0)
