# -*- coding: utf-8 -*-
from odoo import api, fields, models


class BizflowProposalLine(models.Model):
    _name = "bizflow.proposal.line"
    _description = "Proposal Line (material/labor)"
    _order = "sequence, id"

    proposal_id = fields.Many2one(
        "bizflow.proposal",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    line_type = fields.Selection(
        [
            ("material", "Material"),
            ("labor", "Labor"),
            ("other", "Other"),
        ],
        default="material",
        required=True,
    )
    material_id = fields.Many2one("bizflow.material")
    supplier_id = fields.Many2one("bizflow.supplier")
    source = fields.Selection(
        [
            ("manual", "Own price"),
            ("provider", "Provider"),
            ("import", "Import"),
        ],
        default="manual",
    )
    quantity = fields.Float(default=1.0)
    uom_name = fields.Char(string="UoM", default="unit")
    price_unit = fields.Float(string="Unit price", digits="Product Price")
    cost_unit = fields.Float(string="Unit cost", digits="Product Price")
    price_subtotal = fields.Float(compute="_compute_subtotals", store=True)
    cost_subtotal = fields.Float(compute="_compute_subtotals", store=True)
    product_url = fields.Char()
    notes = fields.Char()

    @api.depends("quantity", "price_unit", "cost_unit")
    def _compute_subtotals(self):
        for line in self:
            line.price_subtotal = line.quantity * line.price_unit
            line.cost_subtotal = line.quantity * line.cost_unit

    @api.onchange("material_id")
    def _onchange_material_id(self):
        if self.material_id:
            self.name = self.material_id.name
            self.supplier_id = self.material_id.supplier_id
            self.uom_name = self.material_id.uom_name or "unit"
            self.price_unit = self.material_id.get_unit_price()
            self.cost_unit = self.material_id.cost_price or self.material_id.list_price
            self.product_url = self.material_id.product_url
            self.source = "manual" if self.material_id.cost_price else "provider"
