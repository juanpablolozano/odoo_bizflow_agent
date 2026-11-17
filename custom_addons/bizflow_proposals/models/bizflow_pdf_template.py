# -*- coding: utf-8 -*-
from odoo import fields, models


class BizflowPdfTemplate(models.Model):
    _name = "bizflow.pdf.template"
    _description = "Proposal PDF Template"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    is_default = fields.Boolean(string="Default")
    primary_color = fields.Char(default="#1B4D3E")
    accent_color = fields.Char(default="#C4A35A")
    font_family = fields.Char(default="Georgia, serif")
    header_html = fields.Html(string="Header")
    footer_html = fields.Html(string="Footer")
    custom_css = fields.Text(string="Custom CSS")
    show_cover_image = fields.Boolean(default=True)
    show_before_after = fields.Boolean(default=True)
    show_utility = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
