# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    bizflow_synced = fields.Boolean(
        string="Synced from BizFlow editor",
        default=False,
        copy=False,
        help="Replaced when the construction editor syncs materials/labor.",
    )
    bizflow_proposal_line_id = fields.Many2one(
        "bizflow.proposal.line",
        string="BizFlow line",
        ondelete="set null",
        copy=False,
    )

    @api.depends(
        "product_id",
        "linked_line_id",
        "linked_line_ids",
        "bizflow_synced",
        "bizflow_proposal_line_id",
        "bizflow_proposal_line_id.name",
    )
    def _compute_name(self):
        bizflow_lines = self.filtered(
            lambda l: l.bizflow_synced and l.bizflow_proposal_line_id
        )
        for line in bizflow_lines:
            line.name = (line.bizflow_proposal_line_id.name or "").strip() or line.name
        others = self - bizflow_lines
        if others:
            super(SaleOrderLine, others)._compute_name()

    @api.depends("product_id", "name", "order_id.partner_id", "bizflow_synced")
    def _compute_label(self):
        bizflow_lines = self.filtered("bizflow_synced")
        for line in bizflow_lines:
            # Show only the item name, not "[BIZFLOW_MATERIAL] Construction materials\n..."
            line.label = line.name or ""
        others = self - bizflow_lines
        if others:
            super(SaleOrderLine, others)._compute_label()
