# -*- coding: utf-8 -*-
from odoo import api, fields, models


class BizflowCompanyProfile(models.Model):
    _name = "bizflow.company.profile"
    _description = "BizFlow company profile (contact, terms, signatures)"
    _rec_name = "company_id"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        ondelete="cascade",
        default=lambda self: self.env.company,
    )
    website = fields.Char()
    phone = fields.Char()
    phone_alt = fields.Char(string="Alternate phone")
    email = fields.Char()
    address = fields.Text()
    social_url = fields.Char(string="Social / extra link")

    terms_text = fields.Html(string="Terms and conditions")
    responsibilities_text = fields.Html(string="Responsibilities")
    warranty_text = fields.Html(string="Warranty")

    signer_ids = fields.One2many(
        "bizflow.company.signer",
        "profile_id",
        string="Registered signatures",
    )

    _company_uniq = models.Constraint(
        "unique(company_id)",
        "Only one BizFlow company profile per company.",
    )

    @api.model
    def get_or_create_for_company(self, company=None):
        company = company or self.env.company
        profile = self.search([("company_id", "=", company.id)], limit=1)
        if profile:
            return profile
        address = ""
        if company.partner_id:
            address = company.partner_id.contact_address or ""
        return self.create(
            {
                "company_id": company.id,
                "website": company.website or "",
                "phone": company.phone or "",
                "email": company.email or "",
                "address": address,
            }
        )


class BizflowCompanySigner(models.Model):
    _name = "bizflow.company.signer"
    _description = "Registered company signature for proposals"
    _order = "sequence, id"

    profile_id = fields.Many2one(
        "bizflow.company.profile",
        required=True,
        ondelete="cascade",
    )
    name = fields.Char(required=True)
    title = fields.Char(string="Title / role")
    signature_image = fields.Binary(attachment=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
