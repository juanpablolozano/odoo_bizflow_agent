# -*- coding: utf-8 -*-
import hashlib
import hmac
import time

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import config


class BizflowProposalAsset(models.Model):
    _name = "bizflow.proposal.asset"
    _description = "Proposal image asset"
    _order = "id desc"

    proposal_id = fields.Many2one(
        "bizflow.proposal",
        required=True,
        ondelete="cascade",
        index=True,
    )
    kind = fields.Selection(
        [
            ("upload", "Upload"),
            ("generated", "Generated"),
            ("refined", "Refined"),
        ],
        default="upload",
        required=True,
    )
    label = fields.Char(default="")
    prompt = fields.Text()
    slot = fields.Char(
        help="Optional placement hint: cover, before, after, extra, …",
        default="",
    )
    parent_id = fields.Many2one("bizflow.proposal.asset", ondelete="set null")
    mime = fields.Char(default="image/png")
    datas = fields.Binary(required=True, attachment=True)
    filename = fields.Char(default="image.png")
    author_id = fields.Many2one("res.users", default=lambda self: self.env.user)

    def to_dict(self, with_url=True):
        self.ensure_one()
        data = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label or "",
            "prompt": self.prompt or "",
            "slot": self.slot or "",
            "parent_id": self.parent_id.id,
            "mime": self.mime or "image/png",
            "filename": self.filename or "image.png",
            "author": self.author_id.name,
            "create_date": fields.Datetime.to_string(self.create_date) if self.create_date else "",
        }
        if with_url:
            data["url"] = self.get_signed_url()
        return data

    def get_signed_url(self, ttl_seconds=3600):
        self.ensure_one()
        token = self._sign_token(self.id, ttl_seconds)
        base = self.env["ir.config_parameter"].sudo().get_str("web.base.url", "").rstrip("/")
        return f"{base}/bizflow/assets/file/{token}/"

    @api.model
    def _sign_secret(self):
        return (config.get("secret_key") or "bizflow-assets").encode()

    @api.model
    def _sign_token(self, asset_id, ttl_seconds=3600):
        # Bucket expiry so the same asset keeps a stable URL within the TTL window.
        # Random nonces made every render_html() rewrite srcdoc and reload images.
        ttl = max(int(ttl_seconds), 60)
        now = int(time.time())
        exp = ((now // ttl) + 1) * ttl
        nonce = hmac.new(
            self._sign_secret(),
            f"nonce:{asset_id}:{exp}".encode(),
            hashlib.sha256,
        ).hexdigest()[:8]
        payload = f"{asset_id}:{exp}:{nonce}"
        sig = hmac.new(self._sign_secret(), payload.encode(), hashlib.sha256).hexdigest()[:24]
        return f"{payload}:{sig}"

    @api.model
    def verify_token(self, token):
        try:
            asset_id_s, exp_s, nonce, sig = token.split(":", 3)
            asset_id = int(asset_id_s)
            exp = int(exp_s)
        except Exception as exc:
            raise UserError(self.env._("Invalid asset token")) from exc
        if exp < int(time.time()):
            raise UserError(self.env._("Asset link expired"))
        payload = f"{asset_id}:{exp}:{nonce}"
        expected = hmac.new(self._sign_secret(), payload.encode(), hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(expected, sig):
            raise UserError(self.env._("Invalid asset token"))
        asset = self.browse(asset_id).exists()
        if not asset:
            raise UserError(self.env._("Asset not found"))
        return asset

    def is_referenced(self):
        """True if any section JSON mentions this asset id."""
        self.ensure_one()
        blob = str(self.proposal_id.content or "")
        return f'"asset_id": {self.id}' in blob or f'"asset_id":{self.id}' in blob or f"/assets/{self.id}" in blob
