# -*- coding: utf-8 -*-
from odoo import api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    bizflow_proposal_ids = fields.One2many("bizflow.proposal", "lead_id", string="Editor docs")
    bizflow_proposal_count = fields.Integer(compute="_compute_bizflow_proposal_count")

    @api.depends("bizflow_proposal_ids")
    def _compute_bizflow_proposal_count(self):
        for lead in self:
            lead.bizflow_proposal_count = len(lead.bizflow_proposal_ids)

    def action_new_quotation(self):
        """Open a new quotation form under the standard Quotations action (action-350/sales)."""
        self.ensure_one()
        if not self.partner_id:
            return self.env["ir.actions.actions"]._for_xml_id(
                "sale_crm.crm_quotation_partner_action"
            )
        action = self.env["ir.actions.actions"]._for_xml_id(
            "sale.action_quotations_with_onboarding"
        )
        action = dict(action)
        form_view = self.env.ref("sale.view_order_form")
        action["views"] = [(form_view.id, "form")]
        action["view_mode"] = "form"
        action.pop("res_id", None)
        action["context"] = self._prepare_opportunity_quotation_context()
        return action
