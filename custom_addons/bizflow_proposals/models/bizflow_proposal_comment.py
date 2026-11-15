# -*- coding: utf-8 -*-
from odoo import fields, models


class BizflowProposalComment(models.Model):
    _name = "bizflow.proposal.comment"
    _description = "Proposal share review comment"
    _order = "id asc"

    proposal_id = fields.Many2one(
        "bizflow.proposal",
        required=True,
        ondelete="cascade",
        index=True,
    )
    share_link_id = fields.Many2one("bizflow.share.link", ondelete="set null")
    section = fields.Char(default="")
    author_name = fields.Char(required=True, default="Client")
    author_email = fields.Char()
    body = fields.Text(required=True)
    resolved = fields.Boolean(default=False)
    is_internal = fields.Boolean(default=False)


class BizflowProposalDecision(models.Model):
    _name = "bizflow.proposal.decision"
    _description = "Client review decision on shared proposal"
    _order = "id desc"

    proposal_id = fields.Many2one(
        "bizflow.proposal",
        required=True,
        ondelete="cascade",
        index=True,
    )
    share_link_id = fields.Many2one("bizflow.share.link", ondelete="set null")
    decision = fields.Selection(
        [
            ("approve", "Approve"),
            ("changes", "Request changes"),
        ],
        required=True,
    )
    note = fields.Text()
    author_name = fields.Char(default="Client")
    author_email = fields.Char()
