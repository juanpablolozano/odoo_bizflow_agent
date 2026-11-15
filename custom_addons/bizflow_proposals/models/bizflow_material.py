# -*- coding: utf-8 -*-
from odoo import fields, models


class BizflowMaterial(models.Model):
    _name = "bizflow.material"
    _description = "Construction Material"
    _order = "name"

    name = fields.Char(required=True)
    sku = fields.Char(string="SKU")
    category = fields.Selection(
        [
            ("lumber", "Lumber"),
            ("paint", "Paint"),
            ("flooring", "Flooring"),
            ("electrical", "Electrical"),
            ("plumbing", "Plumbing"),
            ("hardware", "Hardware"),
            ("concrete", "Concrete"),
            ("roofing", "Roofing"),
            ("other", "Other"),
        ],
        default="other",
    )
    supplier_id = fields.Many2one("bizflow.supplier", string="Supplier")
    list_price = fields.Float(string="List price", digits="Product Price")
    cost_price = fields.Float(string="Own cost / custom price", digits="Product Price")
    uom_name = fields.Char(string="UoM", default="unit")
    product_url = fields.Char(string="Product URL")
    last_fetched_price = fields.Float(digits="Product Price")
    notes = fields.Text()
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)

    def get_unit_price(self):
        self.ensure_one()
        if self.cost_price:
            return self.cost_price
        if self.last_fetched_price:
            return self.last_fetched_price
        return self.list_price
