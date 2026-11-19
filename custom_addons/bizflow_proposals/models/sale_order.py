# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    bizflow_proposal_ids = fields.One2many(
        "bizflow.proposal", "order_id", string="BizFlow editor docs"
    )
    bizflow_proposal_id = fields.Many2one(
        "bizflow.proposal",
        string="Construction editor",
        compute="_compute_bizflow_proposal_id",
        store=False,
    )
    bizflow_proposal_count = fields.Integer(compute="_compute_bizflow_proposal_id")

    @api.depends("bizflow_proposal_ids")
    def _compute_bizflow_proposal_id(self):
        for order in self:
            proposal = order.bizflow_proposal_ids[:1]
            order.bizflow_proposal_id = proposal
            order.bizflow_proposal_count = len(order.bizflow_proposal_ids)

    def _bizflow_ensure_proposal(self):
        """Create the internal 1:1 editor document for this quotation if missing."""
        self.ensure_one()
        if self.bizflow_proposal_id:
            return self.bizflow_proposal_id
        partner = self.partner_id
        lead = self.opportunity_id
        return self.env["bizflow.proposal"].create(
            {
                "name": self.name or self.origin or self.env._("Quotation"),
                "order_id": self.id,
                "partner_id": partner.id if partner else False,
                "lead_id": lead.id if lead else False,
                # Leave brief empty so Open editor shows onboarding for blank quotes.
                "description": (self.note or "").strip(),
                "brief": (self.note or "").strip(),
                "location": (partner.city if partner else "") or "",
            }
        )

    def action_open_bizflow_editor(self):
        self.ensure_one()
        proposal = self._bizflow_ensure_proposal()
        return proposal.action_open_editor()

    def action_sync_bizflow_lines(self):
        self.ensure_one()
        proposal = self._bizflow_ensure_proposal()
        from odoo.addons.bizflow_proposals.models.services import order_sync

        order_sync.sync_proposal_to_sale_order(proposal)
        return True

    def action_preview_bizflow_pdf(self):
        self.ensure_one()
        proposal = self._bizflow_ensure_proposal()
        return {
            "type": "ir.actions.act_url",
            "url": f"/bizflow/proposals/{proposal.id}/export/pdf",
            "target": "new",
        }
