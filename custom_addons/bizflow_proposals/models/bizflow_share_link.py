# -*- coding: utf-8 -*-
import secrets

from odoo import api, fields, models


class BizflowShareLink(models.Model):
    _name = "bizflow.share.link"
    _description = "Proposal Share Link"
    _order = "id desc"

    proposal_id = fields.Many2one(
        "bizflow.proposal",
        required=True,
        ondelete="cascade",
        index=True,
    )
    name = fields.Char(required=True, default="Public link")
    purpose = fields.Selection(
        [("view", "View"), ("review", "Review")],
        default="view",
        required=True,
    )
    token = fields.Char(required=True, copy=False, index=True, default=lambda self: secrets.token_urlsafe(24))
    enabled = fields.Boolean(default=True)
    allow_download = fields.Boolean(default=True)
    views = fields.Integer(default=0)
    qualified_views = fields.Integer(
        default=0,
        help="Views lasting at least 3 seconds (tracked via beacon).",
    )
    last_viewed_at = fields.Datetime()
    partner_name = fields.Char()
    partner_email = fields.Char()

    def get_full_url(self):
        self.ensure_one()
        base = self.env["ir.config_parameter"].sudo().get_str("web.base.url", "").rstrip("/")
        return f"{base}/bizflow/p/{self.token}"

    def action_open_link(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self.get_full_url(),
            "target": "new",
        }

    def track_view(self, duration_seconds=0):
        self.ensure_one()
        vals = {
            "views": self.views + 1,
            "last_viewed_at": fields.Datetime.now(),
        }
        if float(duration_seconds or 0) >= 3.0:
            vals["qualified_views"] = self.qualified_views + 1
        self.write(vals)

    @api.model
    def _get_by_token(self, token):
        return self.sudo().search([("token", "=", token), ("enabled", "=", True)], limit=1)
