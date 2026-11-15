# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models


class BizflowProposalPresence(models.Model):
    _name = "bizflow.proposal.presence"
    _description = "Proposal editor presence"
    _order = "last_seen desc"

    proposal_id = fields.Many2one(
        "bizflow.proposal",
        required=True,
        ondelete="cascade",
        index=True,
    )
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    section = fields.Char(default="")
    last_seen = fields.Datetime(required=True, default=fields.Datetime.now)

    _uniq_proposal_user = models.Constraint(
        "unique(proposal_id, user_id)",
        "One presence row per user per proposal.",
    )

    @api.model
    def heartbeat(self, proposal, section=""):
        now = fields.Datetime.now()
        existing = self.search(
            [("proposal_id", "=", proposal.id), ("user_id", "=", self.env.user.id)],
            limit=1,
        )
        vals = {"section": section or "", "last_seen": now}
        if existing:
            existing.write(vals)
            return existing
        return self.create({"proposal_id": proposal.id, "user_id": self.env.user.id, **vals})

    @api.model
    def active_for(self, proposal, seconds=60):
        cutoff = fields.Datetime.now() - timedelta(seconds=seconds)
        rows = self.search(
            [("proposal_id", "=", proposal.id), ("last_seen", ">=", cutoff)]
        )
        return [
            {
                "user_id": r.user_id.id,
                "name": r.user_id.name,
                "section": r.section or "",
                "last_seen": fields.Datetime.to_string(r.last_seen),
            }
            for r in rows
        ]
