# -*- coding: utf-8 -*-
from odoo import fields, models


class BizflowAiModel(models.Model):
    _name = "bizflow.ai.model"
    _description = "AI Model Configuration"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    purpose = fields.Selection(
        [
            ("before_after", "Before / After photos"),
            ("proposal_copy", "Proposal copy"),
            ("price_assist", "Price assist"),
        ],
        required=True,
        default="before_after",
    )
    provider = fields.Selection(
        [
            ("openai", "OpenAI"),
            ("openai_compatible", "OpenAI-compatible"),
        ],
        required=True,
        default="openai",
    )
    model_name = fields.Char(required=True, help="e.g. gpt-image-1, dall-e-3, gpt-4o")
    api_base_url = fields.Char(string="API Base URL")
    api_key = fields.Char(string="API Key")
    default_prompt = fields.Text(
        string="Default prompt template",
        help="Use {project_type}, {notes}, {scope} placeholders.",
    )
    extra_params = fields.Json(string="Extra params", default=dict)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
