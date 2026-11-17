# -*- coding: utf-8 -*-
import hashlib
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


class BizflowSigningRequest(models.Model):
    _name = "bizflow.signing.request"
    _description = "Proposal signing request"
    _order = "id desc"

    proposal_id = fields.Many2one(
        "bizflow.proposal",
        required=True,
        ondelete="cascade",
        index=True,
    )
    token = fields.Char(
        required=True,
        copy=False,
        index=True,
        default=lambda self: secrets.token_urlsafe(24),
    )
    access_code = fields.Char(default=lambda self: secrets.token_hex(3).upper())
    status = fields.Selection(
        [
            ("open", "Open"),
            ("signed", "Signed"),
            ("revoked", "Revoked"),
        ],
        default="open",
        required=True,
    )
    signer_name = fields.Char()
    signer_email = fields.Char()
    company_signed = fields.Boolean(default=False)
    company_signed_at = fields.Datetime()
    company_signer = fields.Char()
    client_signed = fields.Boolean(default=False)
    client_signed_at = fields.Datetime()
    client_signer = fields.Char()
    frozen_html = fields.Text()
    frozen_hash = fields.Char()
    kickoff_date = fields.Date()
    revoked = fields.Boolean(default=False)

    def get_full_url(self):
        self.ensure_one()
        base = self.env["ir.config_parameter"].sudo().get_str("web.base.url", "").rstrip("/")
        return f"{base}/bizflow/sign/{self.token}"

    def freeze_document(self):
        self.ensure_one()
        html = str(self.proposal_id.render_html())
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        self.write({"frozen_html": html, "frozen_hash": digest})
        return digest

    def action_company_sign(self, signer_name=""):
        self.ensure_one()
        if self.status != "open" or self.revoked:
            raise UserError(self.env._("This signing round is closed."))
        if self.company_signed:
            raise UserError(self.env._("Company already signed."))
        if not self.frozen_html:
            self.freeze_document()
        self.write(
            {
                "company_signed": True,
                "company_signed_at": fields.Datetime.now(),
                "company_signer": signer_name or self.env.user.name,
            }
        )
        self._maybe_complete()

    def action_client_sign(self, signer_name="", kickoff_date=None):
        self.ensure_one()
        if self.status != "open" or self.revoked:
            raise UserError(self.env._("This signing round is closed."))
        if self.client_signed:
            raise UserError(self.env._("Client already signed."))
        if kickoff_date:
            min_date = fields.Date.today() + timedelta(days=7)
            if fields.Date.to_date(kickoff_date) < min_date:
                raise UserError(self.env._("Kickoff must be at least 7 days from today."))
            self.kickoff_date = kickoff_date
        self.write(
            {
                "client_signed": True,
                "client_signed_at": fields.Datetime.now(),
                "client_signer": signer_name or self.signer_name or "Client",
            }
        )
        self._maybe_complete()

    def action_revoke(self):
        self.write({"status": "revoked", "revoked": True})

    def action_new_code(self):
        self.access_code = secrets.token_hex(3).upper()

    def _maybe_complete(self):
        self.ensure_one()
        if self.company_signed and self.client_signed:
            self.status = "signed"
            self.proposal_id.write({"state": "accepted"})

    @api.model
    def _get_by_token(self, token):
        return self.sudo().search(
            [("token", "=", token), ("revoked", "=", False)],
            limit=1,
        )
