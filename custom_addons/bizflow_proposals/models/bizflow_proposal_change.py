# -*- coding: utf-8 -*-
from odoo import fields, models


class BizflowProposalChange(models.Model):
    _name = "bizflow.proposal.change"
    _description = "Proposal change history"
    _order = "id desc"

    proposal_id = fields.Many2one(
        "bizflow.proposal",
        required=True,
        ondelete="cascade",
        index=True,
    )
    kind = fields.Selection(
        [
            ("create", "Create"),
            ("edit", "Edit"),
            ("ai", "AI"),
            ("restore", "Restore"),
            ("meta", "Metadata"),
            ("status", "Status"),
            ("image", "Image"),
            ("export", "Export"),
            ("share", "Share"),
            ("signature", "Signature"),
            ("error", "Error"),
        ],
        required=True,
        default="edit",
    )
    section = fields.Char(index=True)
    before = fields.Json(default=dict)
    after = fields.Json(default=dict)
    summary = fields.Char()
    version = fields.Integer()
    restorable = fields.Boolean(default=True)
    author_id = fields.Many2one("res.users", default=lambda self: self.env.user)
