# -*- coding: utf-8 -*-
{
    "name": "BizFlow Proposals",
    "version": "1.4.0",
    "category": "Sales/CRM",
    "summary": "Construction proposals with AI generate/chat, materials, utility, and CRM",
    "description": """
Construction-oriented proposal editor for contractors (Wise /proposals parity).
Create from a description + photos, generate the full proposal with AI, edit via chat,
manage images, materials/suppliers, project utility, share links, and PDF.
    """,
    "author": "BizFlow",
    "license": "LGPL-3",
    "depends": [
        "crm",
        "mail",
        "portal",
        "web",
        "contacts",
    ],
    "data": [
        "security/bizflow_security.xml",
        "security/ir.access.csv",
        "data/section_defaults.xml",
        "data/supplier_data.xml",
        "data/ai_model_data.xml",
        "data/pdf_template_data.xml",
        "views/bizflow_proposal_views.xml",
        "views/bizflow_material_views.xml",
        "views/bizflow_supplier_views.xml",
        "views/bizflow_ai_model_views.xml",
        "views/bizflow_pdf_template_views.xml",
        "views/bizflow_company_profile_views.xml",
        "views/crm_lead_views.xml",
        "views/menus.xml",
        "report/proposal_report.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bizflow_proposals/static/src/scss/proposal_editor.scss",
            "bizflow_proposals/static/src/proposal_editor/proposal_editor.xml",
            "bizflow_proposals/static/src/proposal_editor/proposal_editor.js",
        ],
    },
    "application": True,
    "installable": True,
}
