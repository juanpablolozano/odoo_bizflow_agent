# BizFlow Proposals

Construction-oriented proposal module for Odoo 20 (Wise `/proposals` principles, construction domain).

## Features

- CRM integration: create/open proposals from leads
- OWL split editor (sections + live HTML preview)
- Construction sections: cover, description, before/after, scope, materials, timeline, pricing, **utility** (not ROI), legal, PDF design
- Configurable suppliers: Home Depot / Lowe's (website search), custom API, own catalog
- Own material prices + provider search import
- Multi AI model config for before/after, copy, price assist
- Configurable PDF templates
- Public share links with optional access code

## Install

1. Ensure `addons_path` includes `custom_addons` (already in `odoo.conf`).
2. Restart Odoo.
3. Apps → Update Apps List → install **BizFlow Proposals**.
4. Settings → Users → enable group **BizFlow Proposals / User** (or Manager).
5. Configure AI API keys under **BizFlow Proposals → Configuration → AI models**.

## Editor

Open **BizFlow Proposals → Editor** or use **Open editor** on a proposal form.
