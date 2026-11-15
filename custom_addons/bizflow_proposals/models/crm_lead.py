# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    bizflow_proposal_ids = fields.One2many("bizflow.proposal", "lead_id", string="Proposals")
    bizflow_proposal_count = fields.Integer(compute="_compute_bizflow_proposal_count")

    @api.depends("bizflow_proposal_ids")
    def _compute_bizflow_proposal_count(self):
        for lead in self:
            lead.bizflow_proposal_count = len(lead.bizflow_proposal_ids)

    def action_view_bizflow_proposals(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "bizflow_proposals.action_bizflow_proposal"
        )
        action["domain"] = [("lead_id", "=", self.id)]
        action["context"] = {
            "default_lead_id": self.id,
            "default_partner_id": self.partner_id.id,
            "default_name": self.name,
        }
        return action

    def action_create_bizflow_proposal(self):
        self.ensure_one()
        proposal = self.env["bizflow.proposal"].create(
            {
                "name": self.name,
                "lead_id": self.id,
                "partner_id": self.partner_id.id,
                "description": self.description or "",
                "location": self.contact_name or self.partner_id.city or "",
            }
        )
        return proposal.action_open_editor()
