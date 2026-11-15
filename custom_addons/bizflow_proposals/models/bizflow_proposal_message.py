# -*- coding: utf-8 -*-
from odoo import fields, models


class BizflowProposalMessage(models.Model):
    _name = "bizflow.proposal.message"
    _description = "Proposal AI chat message"
    _order = "id asc"

    proposal_id = fields.Many2one(
        "bizflow.proposal",
        required=True,
        ondelete="cascade",
        index=True,
    )
    thread = fields.Char(
        required=True,
        default="",
        index=True,
        help="Empty = whole proposal. Section key, or design:<key>.",
    )
    role = fields.Selection(
        [
            ("user", "User"),
            ("assistant", "Assistant"),
            ("error", "Error"),
            ("reset", "Reset"),
        ],
        required=True,
        default="user",
    )
    text = fields.Text(required=True, default="")
    sections_touched = fields.Json(default=list)
    change_ids = fields.Json(default=list)
    author_id = fields.Many2one("res.users", default=lambda self: self.env.user)
    create_date = fields.Datetime(readonly=True)
