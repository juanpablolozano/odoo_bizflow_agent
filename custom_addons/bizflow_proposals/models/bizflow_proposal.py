# -*- coding: utf-8 -*-
import hashlib
import json
import logging
import secrets
import threading

from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.modules.registry import Registry

from .schema import SECTION_LABELS, SECTION_ORDER, empty_content, normalize_content

_logger = logging.getLogger(__name__)


class BizflowProposal(models.Model):
    _name = "bizflow.proposal"
    _description = "Construction Proposal"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(copy=False, readonly=True, default="/")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("review", "Review"),
            ("sent", "Sent"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
        ],
        default="draft",
        tracking=True,
    )
    partner_id = fields.Many2one("res.partner", string="Client", tracking=True)
    lead_id = fields.Many2one("crm.lead", string="CRM Lead/Opportunity", tracking=True)
    order_id = fields.Many2one(
        "sale.order",
        string="Quotation",
        ondelete="cascade",
        index=True,
        tracking=True,
        help="Commercial quotation this editor document belongs to.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Salesperson",
        default=lambda self: self.env.user,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
    )
    language = fields.Selection(
        [("en", "English"), ("es", "Spanish")],
        default="es",
    )
    project_type = fields.Char(string="Construction type")
    location = fields.Char(string="Job location")
    description = fields.Text(string="Project description")
    brief = fields.Text(
        string="What the project is about",
        help="Client context, problem, measurements, systems, meeting notes.",
    )
    work_brief = fields.Text(
        string="What must be built",
        help="Tasks, trades, finishes, constraints for the crew.",
    )
    kind = fields.Selection(
        [
            ("proposal", "Proposal"),
            ("assessment", "Site assessment"),
        ],
        default="proposal",
        required=True,
    )
    content = fields.Json(string="Section content", default=empty_content)
    section_order = fields.Json(default=lambda self: list(SECTION_ORDER))
    hidden_sections = fields.Json(default=list)
    version = fields.Integer(default=1)
    generation = fields.Json(
        string="Generation state",
        default=lambda self: {"status": "idle", "percent": 0, "section": "", "error": ""},
    )
    access_code = fields.Char(copy=False)
    access_required = fields.Boolean(default=False)
    access_fail_count = fields.Integer(default=0)
    access_locked_until = fields.Datetime()
    ai_message_ids = fields.One2many(
        "bizflow.proposal.message", "proposal_id", string="AI chat messages"
    )
    change_ids = fields.One2many("bizflow.proposal.change", "proposal_id", string="History")
    asset_ids = fields.One2many("bizflow.proposal.asset", "proposal_id", string="Assets")
    presence_ids = fields.One2many("bizflow.proposal.presence", "proposal_id", string="Presence")
    comment_ids = fields.One2many("bizflow.proposal.comment", "proposal_id", string="Comments")
    decision_ids = fields.One2many("bizflow.proposal.decision", "proposal_id", string="Decisions")
    signing_ids = fields.One2many("bizflow.signing.request", "proposal_id", string="Signing")
    cost_ids = fields.One2many("bizflow.proposal.cost", "proposal_id", string="Internal costs")
    archived = fields.Boolean(default=False, index=True)
    materials_report_mode = fields.Selection(
        [
            ("detailed_prices", "Items with prices"),
            ("detailed_no_prices", "Items without prices"),
            ("total_only", "Materials total only"),
        ],
        default="detailed_prices",
        required=True,
        string="Client materials display",
        help="How materials appear on the client proposal / PDF.",
    )
    pdf_template_id = fields.Many2one(
        "bizflow.pdf.template",
        string="PDF template",
        default=lambda self: self.env["bizflow.pdf.template"].search(
            [("is_default", "=", True)], limit=1
        ),
    )
    before_after_model_id = fields.Many2one(
        "bizflow.ai.model",
        string="Before/After AI model",
        domain=[("purpose", "=", "before_after")],
    )
    copy_model_id = fields.Many2one(
        "bizflow.ai.model",
        string="Copy AI model",
        domain=[("purpose", "=", "proposal_copy")],
    )
    price_model_id = fields.Many2one(
        "bizflow.ai.model",
        string="Price AI model",
        domain=[("purpose", "=", "price_assist")],
    )
    line_ids = fields.One2many("bizflow.proposal.line", "proposal_id", string="Lines")
    share_link_ids = fields.One2many("bizflow.share.link", "proposal_id", string="Share links")
    before_image = fields.Binary(string="Before photo", attachment=True)
    after_image = fields.Binary(string="After photo", attachment=True)
    cover_image = fields.Binary(string="Cover image", attachment=True)

    amount_materials = fields.Monetary(compute="_compute_amounts", store=True)
    amount_labor = fields.Monetary(compute="_compute_amounts", store=True)
    amount_subtotal = fields.Monetary(compute="_compute_amounts", store=True)
    amount_markup = fields.Monetary(compute="_compute_amounts", store=True)
    amount_total = fields.Monetary(compute="_compute_amounts", store=True)
    amount_cost = fields.Monetary(compute="_compute_amounts", store=True)
    amount_utility = fields.Monetary(
        string="Project utility",
        compute="_compute_amounts",
        store=True,
        help="Revenue minus costs (materials + labor + overhead/contingency from utility section).",
    )
    utility_margin = fields.Float(
        string="Utility margin %",
        compute="_compute_amounts",
        store=True,
    )

    @api.model
    def _ensure_quotation_for_vals(self, vals):
        """Quotation-first: create sale.order when saving from editor/CRM without one."""
        if vals.get("order_id"):
            return vals
        partner_id = vals.get("partner_id") or False
        lead_id = vals.get("lead_id") or False
        lead = self.env["crm.lead"].browse(lead_id) if lead_id else self.env["crm.lead"]
        if lead and not partner_id:
            partner_id = lead.partner_id.id or False
        name = vals.get("name") or (lead.name if lead else None) or self.env._("Quotation")
        if not partner_id:
            partner = self.env["res.partner"].create({"name": name})
            partner_id = partner.id
            vals["partner_id"] = partner_id
        order_vals = {
            "partner_id": partner_id,
            "origin": name,
        }
        if lead_id:
            order_vals["opportunity_id"] = lead_id
            if lead.team_id:
                order_vals["team_id"] = lead.team_id.id
            if lead.user_id:
                order_vals["user_id"] = lead.user_id.id
        order = self.env["sale.order"].create(order_vals)
        vals["order_id"] = order.id
        if not vals.get("partner_id"):
            vals["partner_id"] = partner_id
        if lead_id and not vals.get("lead_id"):
            vals["lead_id"] = lead_id
        if not vals.get("name"):
            vals["name"] = name
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            vals = dict(vals)
            if vals.get("code", "/") == "/":
                vals["code"] = self.env["ir.sequence"].next_by_code("bizflow.proposal") or "/"
            vals["content"] = normalize_content(vals.get("content") or {})
            if not vals.get("access_code"):
                vals["access_code"] = secrets.token_hex(3).upper()
            if vals.get("lead_id") and not vals.get("partner_id"):
                lead = self.env["crm.lead"].browse(vals["lead_id"])
                vals["partner_id"] = lead.partner_id.id or False
                if not vals.get("name"):
                    vals["name"] = lead.name
            if vals.get("description") and not vals.get("brief"):
                vals["brief"] = vals["description"]
            if vals.get("brief") and not vals.get("description"):
                vals["description"] = vals["brief"]
            # Skip auto-quotation when creating the internal doc from sale.order helper
            # (order_id already set) or when explicitly skipped.
            if not vals.get("order_id") and not self.env.context.get("bizflow_skip_auto_order"):
                vals = self._ensure_quotation_for_vals(vals)
            prepared.append(vals)
        records = super().create(prepared)
        for rec in records:
            rec.log_change("create", summary="Created quotation editor doc", restorable=False)
        return records

    def write(self, vals):
        vals = dict(vals)
        if "content" in vals:
            vals["content"] = normalize_content(vals.get("content") or {})
        bump = any(k in vals for k in ("content", "line_ids", "hidden_sections", "section_order"))
        if bump and "version" not in vals:
            for rec in self:
                super(BizflowProposal, rec).write({**vals, "version": (rec.version or 1) + 1})
            return True
        return super().write(vals)

    @api.depends(
        "line_ids.price_subtotal",
        "line_ids.cost_subtotal",
        "line_ids.line_type",
        "content",
    )
    def _compute_amounts(self):
        for rec in self:
            materials = sum(rec.line_ids.filtered(lambda l: l.line_type == "material").mapped("price_subtotal"))
            labor = sum(rec.line_ids.filtered(lambda l: l.line_type == "labor").mapped("price_subtotal"))
            cost = sum(rec.line_ids.mapped("cost_subtotal"))
            content = normalize_content(rec.content or {})
            pricing = content.get("pricing") or {}
            utility = content.get("utility") or {}
            markup_pct = float(pricing.get("markup_percent") or 0.0)
            contingency_pct = float(pricing.get("contingency_percent") or 0.0)
            subtotal = materials + labor
            markup = subtotal * markup_pct / 100.0
            contingency = subtotal * contingency_pct / 100.0
            total = subtotal + markup + contingency
            overhead = float(utility.get("overhead_cost") or 0.0)
            utility_contingency = float(utility.get("contingency_cost") or contingency)
            revenue = float(utility.get("revenue") or total)
            total_cost = cost + overhead + utility_contingency
            utility_amount = revenue - total_cost
            rec.amount_materials = materials
            rec.amount_labor = labor
            rec.amount_subtotal = subtotal
            rec.amount_markup = markup
            rec.amount_total = total
            rec.amount_cost = total_cost
            rec.amount_utility = utility_amount
            rec.utility_margin = (utility_amount / revenue * 100.0) if revenue else 0.0

    def is_editor_setup_needed(self):
        """True when the editor should open the blank-project onboarding UI."""
        self.ensure_one()
        has_text = bool((self.brief or "").strip() or (self.work_brief or "").strip())
        return not has_text and not self.asset_ids and not self.line_ids

    def action_open_editor(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "bizflow_proposal_editor",
            "name": self.env._("Quotation editor"),
            "params": {
                "proposal_id": self.id,
                "order_id": self.order_id.id if self.order_id else False,
                "setup": self.is_editor_setup_needed(),
            },
            "target": "current",
        }

    def action_sync_to_quotation(self):
        self.ensure_one()
        if not self.order_id:
            vals = self._ensure_quotation_for_vals(
                {
                    "name": self.name,
                    "partner_id": self.partner_id.id or False,
                    "lead_id": self.lead_id.id or False,
                }
            )
            self.order_id = vals.get("order_id")
        from odoo.addons.bizflow_proposals.models.services import order_sync

        order_sync.sync_proposal_to_sale_order(self)
        return True

    def action_set_review(self):
        self.write({"state": "review"})

    def action_set_sent(self):
        self.write({"state": "sent"})

    def action_set_accepted(self):
        self.write({"state": "accepted"})
        for rec in self.filtered("lead_id"):
            if hasattr(rec.lead_id, "action_set_won"):
                try:
                    rec.lead_id.action_set_won()
                except Exception:
                    rec.lead_id.write({"probability": 100.0})

    def action_set_rejected(self):
        self.write({"state": "rejected"})

    def action_new_access_code(self):
        for rec in self:
            rec.access_code = secrets.token_hex(3).upper()
            rec.access_fail_count = 0
            rec.access_locked_until = False

    def check_access_code(self, code):
        """Validate portal access code with 5-fail / 15-min lockout."""
        self.ensure_one()
        now = fields.Datetime.now()
        if self.access_locked_until and self.access_locked_until > now:
            return {
                "ok": False,
                "locked": True,
                "message": self.env._("Too many attempts. Try again in 15 minutes."),
            }
        if not self.access_required:
            return {"ok": True, "locked": False}
        if (code or "").upper() == (self.access_code or "").upper():
            self.access_fail_count = 0
            return {"ok": True, "locked": False}
        fails = (self.access_fail_count or 0) + 1
        vals = {"access_fail_count": fails}
        if fails >= 5:
            vals["access_locked_until"] = now + timedelta(minutes=15)
            vals["access_fail_count"] = 0
            self.write(vals)
            return {
                "ok": False,
                "locked": True,
                "message": self.env._("Too many attempts. Try again in 15 minutes."),
            }
        self.write(vals)
        return {
            "ok": False,
            "locked": False,
            "message": self.env._("Invalid access code."),
        }

    def action_create_share_link(self, purpose="view", allow_download=True):
        self.ensure_one()
        return self.env["bizflow.share.link"].create(
            {
                "proposal_id": self.id,
                "purpose": purpose,
                "allow_download": allow_download,
                "name": self.env._("Public link"),
            }
        )

    def action_duplicate(self):
        self.ensure_one()
        copy = self.copy(
            {
                "name": f"{self.name} (copy)",
                "state": "draft",
                "code": "/",
                "access_code": secrets.token_hex(3).upper(),
                "generation": {"status": "idle", "percent": 0, "section": "", "error": ""},
                "archived": False,
            }
        )
        # Remap assets (binary copy via Odoo copy on o2m may not include datas)
        for asset in self.asset_ids:
            asset.copy({"proposal_id": copy.id})
        copy.log_change("create", summary=f"Duplicated from {self.code}", restorable=False)
        return copy

    def action_archive_proposal(self):
        self.write({"archived": True})

    def export_html(self):
        self.ensure_one()
        html = str(self.render_html())
        self.log_change("export", summary="Exported HTML", restorable=False)
        return html

    def export_pdf_bytes(self):
        """Try Chromium/wkhtml; raise UserError with code for 501 fallback."""
        self.ensure_one()
        html = self.export_html()
        try:
            import subprocess
            import tempfile
            import os

            with tempfile.TemporaryDirectory() as tmp:
                html_path = os.path.join(tmp, "proposal.html")
                pdf_path = os.path.join(tmp, "proposal.pdf")
                with open(html_path, "w", encoding="utf-8") as fh:
                    fh.write(html)
                chrome = (
                    os.environ.get("CHROME_BIN")
                    or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                )
                if not os.path.exists(chrome):
                    raise FileNotFoundError("chrome")
                subprocess.run(
                    [
                        chrome,
                        "--headless",
                        "--disable-gpu",
                        f"--print-to-pdf={pdf_path}",
                        html_path,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                with open(pdf_path, "rb") as fh:
                    return fh.read()
        except Exception as exc:
            raise UserError(
                self.env._(
                    "PDF export unavailable (no Chromium). Use browser print or install Chrome. (%s)",
                    str(exc),
                )
            ) from exc

    def action_create_signing_request(self, signer_name="", signer_email=""):
        self.ensure_one()
        req = self.env["bizflow.signing.request"].create(
            {
                "proposal_id": self.id,
                "signer_name": signer_name or self.partner_id.name or "",
                "signer_email": signer_email or self.partner_id.email or "",
            }
        )
        req.freeze_document()
        self.log_change("signature", summary="Created signing request", restorable=False)
        return req

    def get_section(self, key):
        self.ensure_one()
        content = normalize_content(self.content or {})
        return content.get(key, {})

    def section_hash(self, key):
        self.ensure_one()
        data = self.get_section(key)
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def section_hashes(self):
        self.ensure_one()
        return {key: self.section_hash(key) for key in (self.section_order or SECTION_ORDER)}

    def log_change(self, kind, section="", before=None, after=None, summary="", restorable=True):
        self.ensure_one()
        return self.env["bizflow.proposal.change"].create(
            {
                "proposal_id": self.id,
                "kind": kind,
                "section": section or "",
                "before": before if before is not None else {},
                "after": after if after is not None else {},
                "summary": summary or "",
                "version": self.version,
                "restorable": restorable,
            }
        )

    def save_section(self, key, section_data, base_hash=None):
        self.ensure_one()
        if key not in SECTION_ORDER:
            raise UserError(self.env._("Unknown section: %s", key))
        current_hash = self.section_hash(key)
        if base_hash and base_hash != current_hash:
            return {
                "conflict": True,
                "section": self.get_section(key),
                "hash": current_hash,
                "version": self.version,
            }
        content = normalize_content(self.content or {})
        before = dict(content.get(key) or {})
        current = dict(before)
        if isinstance(section_data, dict):
            current.update(section_data)
        content[key] = current
        self.write({"content": content})
        self.log_change("edit", section=key, before=before, after=current, summary=f"Edited {key}")
        return {
            "conflict": False,
            "section": content[key],
            "hash": self.section_hash(key),
            "version": self.version,
        }

    def start_generation(self, extra_instructions="", user_message=""):
        """Queue full-proposal AI generation. Returns False if already running."""
        self.ensure_one()
        gen = dict(self.generation or {})
        if gen.get("status") == "running":
            return False
        if user_message:
            self.env["bizflow.proposal.message"].create(
                {
                    "proposal_id": self.id,
                    "thread": "",
                    "role": "user",
                    "text": user_message,
                }
            )
        self.write(
            {
                "generation": {
                    "status": "running",
                    "percent": 0,
                    "section": "cover",
                    "error": "",
                    "started_by": self.env.user.name,
                }
            }
        )
        self.env.cr.commit()
        db_name = self.env.cr.dbname
        proposal_id = self.id
        uid = self.env.uid
        context = dict(self.env.context)
        instructions = extra_instructions or ""

        def _run():
            registry = Registry(db_name)
            with registry.cursor() as cr:
                env = api.Environment(cr, uid, context)
                proposal = env["bizflow.proposal"].browse(proposal_id)
                try:
                    from odoo.addons.bizflow_proposals.models.services import generate as gen_svc

                    def on_progress(percent, section):
                        proposal.write(
                            {
                                "generation": {
                                    "status": "running",
                                    "percent": int(percent),
                                    "section": section or "",
                                    "error": "",
                                    "started_by": proposal.generation.get("started_by")
                                    if isinstance(proposal.generation, dict)
                                    else "",
                                }
                            }
                        )
                        cr.commit()

                    sections = gen_svc.generate_proposal(
                        proposal, extra_instructions=instructions, on_progress=on_progress
                    )
                    meta = sections.pop("_meta", {})
                    touched = gen_svc.apply_generated_sections(proposal, sections)
                    for key in touched:
                        proposal.log_change(
                            "ai",
                            section=key,
                            after=proposal.get_section(key),
                            summary=f"AI wrote {key}",
                        )
                    # 2A: after generate with site photos/plan, build Materials pricing review
                    # (preview only — never auto-apply lines).
                    try:
                        from odoo.addons.bizflow_proposals.models.services import pricing as pricing_svc

                        on_progress(92, "materials")
                        pricing_svc.run_pricing_preview_after_generate(proposal)
                    except Exception:
                        _logger.exception(
                            "post-generate pricing preview failed for proposal %s", proposal.id
                        )
                    reply = (
                        "Listo: escribí todas las secciones a partir de tu descripción."
                        if (proposal.language or "es") == "es"
                        else "Done: I wrote every section from your description."
                    )
                    if not meta.get("used_ai"):
                        reply = (
                            "Armé un borrador completo a partir de tu descripción "
                            "(sin clave de IA). Puedes refinarlo por chat."
                            if (proposal.language or "es") == "es"
                            else "I drafted every section from your description "
                            "(no AI key). Refine it in chat."
                        )
                    content_pricing = (proposal.content or {}).get("pricing") or {}
                    if content_pricing.get("last_review"):
                        reply = (
                            f"{reply} Revisa materiales y mano de obra en la pestaña Materiales."
                            if (proposal.language or "es") == "es"
                            else f"{reply} Review materials and labor in the Materials tab."
                        )
                    env["bizflow.proposal.message"].create(
                        {
                            "proposal_id": proposal.id,
                            "thread": "",
                            "role": "assistant",
                            "text": reply,
                            "sections_touched": touched,
                        }
                    )
                    proposal.write(
                        {
                            "generation": {
                                "status": "done",
                                "percent": 100,
                                "section": "",
                                "error": "",
                            }
                        }
                    )
                    cr.commit()
                except Exception as exc:
                    proposal.write(
                        {
                            "generation": {
                                "status": "error",
                                "percent": 0,
                                "section": "",
                                "error": str(exc),
                            }
                        }
                    )
                    env["bizflow.proposal.message"].create(
                        {
                            "proposal_id": proposal.id,
                            "thread": "",
                            "role": "error",
                            "text": str(exc),
                        }
                    )
                    proposal.log_change("error", summary=str(exc), restorable=False)
                    cr.commit()

        threading.Thread(target=_run, daemon=True).start()
        return True

    def apply_chat(self, message, thread="", focus="", only=False, redo=False):
        self.ensure_one()
        Message = self.env["bizflow.proposal.message"]
        if redo:
            Message.create(
                {
                    "proposal_id": self.id,
                    "thread": thread or focus or "",
                    "role": "reset",
                    "text": "redo",
                }
            )
        Message.create(
            {
                "proposal_id": self.id,
                "thread": thread if thread is not None else (focus or ""),
                "role": "user",
                "text": message,
            }
        )
        from odoo.addons.bizflow_proposals.models.services import chat as chat_svc

        reply, sections, visibility, is_error = chat_svc.edit_proposal(
            self,
            message,
            thread=thread if thread is not None else (focus or ""),
            focus=focus or "",
            only=only,
        )
        touched = []
        if not is_error and (sections or visibility.get("hide") or visibility.get("show")):
            touched = chat_svc.apply_chat_result(self, sections, visibility)
            for key in touched:
                self.log_change("ai", section=key, after=self.get_section(key), summary=f"Chat edited {key}")
        msg = Message.create(
            {
                "proposal_id": self.id,
                "thread": thread if thread is not None else (focus or ""),
                "role": "error" if is_error else "assistant",
                "text": reply,
                "sections_touched": touched,
            }
        )
        return msg

    def reset_chat(self, thread=""):
        self.ensure_one()
        return self.env["bizflow.proposal.message"].create(
            {
                "proposal_id": self.id,
                "thread": thread or "",
                "role": "reset",
                "text": "new chat",
            }
        )

    def get_state_payload(self):
        self.ensure_one()
        gen = self.generation if isinstance(self.generation, dict) else {}
        return {
            "id": self.id,
            "version": self.version,
            "state": self.state,
            "write_date": fields.Datetime.to_string(self.write_date) if self.write_date else "",
            "generation": {
                "status": gen.get("status") or "idle",
                "percent": int(gen.get("percent") or 0),
                "section": gen.get("section") or "",
                "error": gen.get("error") or "",
                "started_by": gen.get("started_by") or "",
            },
            "presence": self.env["bizflow.proposal.presence"].active_for(self),
            "hashes": self.section_hashes(),
        }

    def sync_utility_from_lines(self):
        """Push line totals into utility/pricing sections."""
        self.ensure_one()
        content = normalize_content(self.content or {})
        pricing = dict(content.get("pricing") or {})
        utility = dict(content.get("utility") or {})
        pricing["materials_total"] = self.amount_materials
        pricing["labor_total"] = self.amount_labor
        utility["material_cost"] = sum(
            self.line_ids.filtered(lambda l: l.line_type == "material").mapped("cost_subtotal")
        )
        utility["labor_cost"] = sum(
            self.line_ids.filtered(lambda l: l.line_type == "labor").mapped("cost_subtotal")
        )
        utility["revenue"] = self.amount_total
        content["pricing"] = pricing
        content["utility"] = utility
        self.write({"content": content})
        return content

    def render_html(self):
        """Client-facing letter PDF layout (Lisa-style single sheet)."""
        self.ensure_one()
        content = normalize_content(self.content or {})
        template = self.pdf_template_id
        lang_es = (self.language or "es") == "es"
        accent = (template.accent_color if template else "#C4A35A") or "#C4A35A"
        primary = (template.primary_color if template else "#111111") or "#111111"
        font = (
            (template.font_family if template else "Helvetica, Arial, sans-serif")
            or "Helvetica, Arial, sans-serif"
        )
        custom_css = (template.custom_css if template else "") or content.get("design", {}).get("css") or ""
        show_ba = True if not template else bool(template.show_before_after)
        show_utility = bool(template.show_utility) if template else False

        t = self._doc_i18n(lang_es)
        company = self.company_id or self.env.company
        company_name = escape(company.name or "Company")
        logo_html = self._company_logo_html(company)

        cover = content.get("cover") or {}
        description = content.get("description") or {}
        scope = content.get("scope") or {}
        before_after = content.get("before_after") or {}
        pricing = content.get("pricing") or {}
        legal = content.get("legal") or {}

        title = cover.get("title") or self.project_type or t["proposal_fallback"]
        prepared = self.partner_id.name or self.name or self.location or ""
        intro = (
            description.get("intro")
            or description.get("solution")
            or self.brief
            or self.description
            or ""
        )
        work_caption = scope.get("intro") or before_after.get("intro") or t["work_caption"]
        price_note = pricing.get("intro") or t["price_includes"]

        ba_blocks = self._render_before_after_pairs(t)

        materials_amt = float(self.amount_materials or 0.0)
        labor_amt = float(self.amount_labor or 0.0)
        total_amt = float(
            self.amount_total or (materials_amt + labor_amt + float(self.amount_markup or 0))
        )

        payments = pricing.get("payments") or [
            {"label": t["pay1"], "percent": 40, "detail": t["pay1_d"]},
            {"label": t["pay2"], "percent": 30, "detail": t["pay2_d"]},
            {"label": t["pay3"], "percent": 30, "detail": t["pay3_d"]},
        ]
        payment_cards = []
        for idx, pay in enumerate(payments[:3], start=1):
            pct = float(pay.get("percent") or 0)
            amount = total_amt * pct / 100.0
            payment_cards.append(
                "<div class='pay-card'>"
                f"<div class='pay-num'>{idx}</div>"
                f"<div class='pay-label'>{escape(pay.get('label') or '')}"
                f"<span class='pay-pct'>{pct:g}%</span></div>"
                f"<p class='pay-detail'>{escape(pay.get('detail') or '')}</p>"
                f"<div class='pay-amt'>${amount:,.2f}</div>"
                "</div>"
            )

        exclusions = legal.get("notes") or ""
        if not exclusions and (self.brief or ""):
            for line in (self.brief or "").splitlines():
                low = line.lower()
                if "no incluye" in low or "not include" in low:
                    exclusions = line.strip()
                    break

        scope_modules = ""
        for mod in scope.get("modules") or []:
            scope_modules += (
                f"<li><strong>{escape(mod.get('title') or '')}</strong> — "
                f"{escape(mod.get('description') or '')}</li>"
            )

        sheet = [
            "<div class='sheet'>",
            "<div data-section='cover'>",
            "<header class='doc-header'>",
            f"<div class='brand'>{logo_html}<div class='brand-text'><div class='co-name'>{company_name}</div>",
            f"<div class='co-tag'>{escape(t['tagline'])}</div></div></div>",
            f"<div class='rule' style='background:{accent}'></div>",
            "</header>",
            f"<h1 class='doc-title'>{escape(title)}</h1>",
            f"<p class='prepared'>{escape(t['prepared_for'])} <strong>{escape(prepared)}</strong></p>",
            "</div>",
            f"<div data-section='description'><p class='intro'>{escape(intro)}</p></div>",
        ]
        if show_ba and ba_blocks:
            sheet.append(f"<div data-section='before_after'>{ba_blocks}</div>")
        sheet.append("<div data-section='scope'>")
        sheet.append(f"<h2 class='section-title'>{escape(t['the_work'])}</h2>")
        sheet.append(f"<p class='work-caption'>{escape(work_caption)}</p>")
        if scope_modules:
            sheet.append(f"<ul class='scope-list'>{scope_modules}</ul>")
        sheet.append("</div>")
        sheet.append("<div data-section='materials'>")
        sheet.append(f"<p class='muted'>{escape(price_note)}</p>")
        sheet.append(self._render_materials_labor_table(t, materials_amt, labor_amt, total_amt))
        sheet.append("</div>")
        sheet.append("<div data-section='pricing'>")
        if payment_cards:
            sheet.append(f"<div class='pay-grid'>{''.join(payment_cards)}</div>")
        sheet.append("</div>")
        if show_utility:
            sheet.append(
                "<div data-section='utility'>"
                + self._render_utility_section(content).replace("page", "utility-block")
                + "</div>"
            )
        sheet.append("<div data-section='legal'>")
        if exclusions:
            sheet.append(f"<p class='exclusions'>{escape(exclusions)}</p>")
        sheet.append(self._render_terms_and_signatures(content, t, company, accent))
        sheet.append("</div>")
        sheet.append("</div>")

        css = f"""
body{{margin:0;padding:28px;background:#e8edf2;color:#111;font-family:{font};}}
.sheet{{max-width:780px;margin:0 auto;background:#fff;padding:36px 40px 28px;
box-shadow:0 8px 28px rgba(0,0,0,.12);border:1px solid #ddd;}}
[data-section]{{cursor:pointer;transition:outline-color .15s;outline:2px solid transparent;outline-offset:2px;}}
[data-section]:hover{{outline-color:rgba(61,126,191,.35);}}
[data-section].bf-active{{outline-color:#3D7EBF;}}
@media print{{[data-section]{{outline:0!important;cursor:default;}}}}
.doc-header{{margin-bottom:18px;}}
.brand{{display:flex;align-items:center;gap:14px;}}
.brand img{{width:56px;height:56px;object-fit:contain;border-radius:6px;}}
.co-name{{font-size:18px;font-weight:700;letter-spacing:.02em;text-transform:uppercase;color:{primary};}}
.co-tag{{font-size:12px;color:#555;margin-top:2px;}}
.rule{{height:2px;margin:14px 0 18px;}}
.doc-title{{font-size:28px;margin:0 0 6px;color:#111;}}
.prepared{{margin:0 0 14px;font-size:14px;}}
.intro{{font-size:15px;line-height:1.45;margin:0 0 18px;}}
.ba-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:0 0 14px;}}
.ba-label{{font-size:12px;font-weight:700;margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em;}}
.ba-stack{{display:flex;flex-direction:column;gap:8px;}}
.ba-photo img{{width:100%;max-height:220px;object-fit:cover;border-radius:4px;border:1px solid #e5e5e5;}}
.ba-stack .ba-photo img{{max-height:180px;}}
.section-title{{font-size:16px;margin:8px 0 6px;}}
.work-caption{{margin:0 0 8px;font-size:14px;}}
.scope-list{{margin:0 0 12px;padding-left:18px;font-size:13px;}}
.muted{{color:#666;font-size:13px;}}
.price-table{{width:100%;border-collapse:collapse;margin-top:10px;}}
.price-table td{{background:#f3efe6;padding:10px 12px;border-bottom:1px solid #fff;font-size:14px;}}
.price-table .num{{text-align:right;font-weight:600;}}
.total-bar{{display:flex;justify-content:space-between;align-items:center;
background:#111;color:#fff;padding:12px 14px;margin-top:2px;font-weight:600;}}
.total-amt{{font-size:22px;}}
.pay-grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin:16px 0;}}
.pay-card{{background:#f3efe6;padding:12px;border-radius:4px;}}
.pay-num{{width:22px;height:22px;border-radius:50%;background:{accent};color:#111;
font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;margin-bottom:6px;}}
.pay-label{{font-weight:700;font-size:13px;}}
.pay-pct{{float:right;color:#666;font-weight:600;}}
.pay-detail{{font-size:12px;color:#555;min-height:36px;margin:6px 0;}}
.pay-amt{{font-weight:700;font-size:15px;}}
.exclusions{{font-size:13px;color:#444;margin:8px 0 16px;}}
.line-table{{width:100%;border-collapse:collapse;margin:10px 0 4px;font-size:13px;}}
.line-table th{{text-align:left;background:#f3efe6;padding:8px 10px;font-size:11px;
text-transform:uppercase;letter-spacing:.03em;border-bottom:1px solid #e5e0d4;}}
.line-table td{{padding:8px 10px;border-bottom:1px solid #eee;}}
.line-table .num{{text-align:right;white-space:nowrap;}}
.line-table .section-row td{{background:#faf8f4;font-weight:700;font-size:12px;padding-top:12px;}}
.line-origin{{font-size:11px;color:#777;}}
.terms-block{{margin:18px 0 10px;font-size:12px;line-height:1.45;color:#333;}}
.terms-block h3{{font-size:14px;margin:0 0 8px;}}
.terms-block h4{{font-size:12px;margin:12px 0 4px;}}
.sig-grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:20px 0 12px;}}
.sig-block{{min-height:90px;}}
.sig-line{{border-bottom:1px solid #333;height:48px;margin-bottom:6px;
display:flex;align-items:flex-end;}}
.sig-line img{{max-height:44px;max-width:100%;object-fit:contain;}}
.sig-name{{font-size:13px;font-weight:600;}}
.sig-title{{font-size:11px;color:#666;}}
.doc-footer{{margin-top:8px;font-size:13px;}}
.contact-row{{display:flex;flex-wrap:wrap;gap:10px 18px;font-size:12px;color:#444;margin-top:6px;}}
.contact-row a{{color:#111;}}
{custom_css}
"""
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
            f"<style>{css}</style></head><body>{''.join(sheet)}</body></html>"
        )
        return Markup(html)

    def _doc_i18n(self, lang_es):
        if lang_es:
            return {
                "tagline": "General Contractor",
                "prepared_for": "Preparado para",
                "proposal_fallback": "Propuesta de construcción",
                "before": "Antes",
                "after": "Después",
                "before_empty": "Sube la foto del sitio",
                "after_empty": "Visualización después",
                "the_work": "El trabajo",
                "work_caption": "Este precio incluye materiales y mano de obra del alcance descrito.",
                "price_includes": "Este precio incluye materiales y mano de obra.",
                "materials": "Materiales",
                "labor": "Mano de obra",
                "total": "Inversión total",
                "pay1": "Para empezar",
                "pay1_d": "Al firmar, antes de programar",
                "pay2": "Punto medio",
                "pay2_d": "Superficie lista, antes del acabado final",
                "pay3": "Al completar",
                "pay3_d": "Obra terminada y walkthrough",
                "questions": "¿Preguntas? Contáctanos.",
                "qty": "Cant.",
                "unit": "Unitario",
                "line_total": "Total",
                "origin": "Origen",
                "terms_company": "Términos y condiciones",
                "terms_proposal": "Términos de esta propuesta",
                "responsibilities": "Responsabilidades",
                "warranty": "Garantía",
                "signatures": "Firmas",
                "client_sig": "Firma del cliente",
                "company_sig": "Firma de la compañía",
            }
        return {
            "tagline": "General Contractor",
            "prepared_for": "Prepared for",
            "proposal_fallback": "Construction proposal",
            "before": "Before",
            "after": "After",
            "before_empty": "Upload a site photo",
            "after_empty": "After visualization",
            "the_work": "The work",
            "work_caption": "This price includes labor and materials for the described scope.",
            "price_includes": "This price includes labor and materials.",
            "materials": "Materials",
            "labor": "Labor",
            "total": "Total investment",
            "pay1": "To start",
            "pay1_d": "At signing, before work is scheduled",
            "pay2": "Midpoint",
            "pay2_d": "Surface prepared, before final finish",
            "pay3": "At completion",
            "pay3_d": "Work complete and walkthrough done",
                "questions": "Questions? Call us anytime.",
                "qty": "Qty",
                "unit": "Unit",
                "line_total": "Total",
                "origin": "Source",
                "terms_company": "Terms and conditions",
                "terms_proposal": "Terms for this proposal",
                "responsibilities": "Responsibilities",
                "warranty": "Warranty",
                "signatures": "Signatures",
                "client_sig": "Client signature",
                "company_sig": "Company signature",
            }

    def _render_materials_labor_table(self, t, materials_amt, labor_amt, total_amt):
        """Materials + labor for client document. Never show notes/source/manual."""
        self.ensure_one()
        mode = self.materials_report_mode or "detailed_prices"
        materials = self.line_ids.filtered(lambda l: l.line_type == "material")
        labor_lines = self.line_ids.filtered(lambda l: l.line_type == "labor")

        if mode == "total_only" or (not materials and not labor_lines):
            return (
                "<table class='price-table'><tbody>"
                f"<tr><td>{escape(t['materials'])}</td><td class='num'>${materials_amt:,.2f}</td></tr>"
                f"<tr><td>{escape(t['labor'])}</td><td class='num'>${labor_amt:,.2f}</td></tr>"
                "</tbody></table>"
                f"<div class='total-bar'><span>{escape(t['total'])}</span>"
                f"<span class='total-amt'>${total_amt:,.2f}</span></div>"
            )

        show_prices = mode == "detailed_prices"
        if show_prices:
            rows = [
                "<table class='line-table'><thead><tr>"
                f"<th>{escape(t['materials'])}</th>"
                f"<th class='num'>{escape(t['qty'])}</th>"
                f"<th class='num'>{escape(t['unit'])}</th>"
                f"<th class='num'>{escape(t['line_total'])}</th>"
                "</tr></thead><tbody>"
            ]
        else:
            # 1B: name + quantity only (no $)
            rows = [
                "<table class='line-table'><thead><tr>"
                f"<th>{escape(t['materials'])}</th>"
                "</tr></thead><tbody>"
            ]

        if materials:
            for line in materials:
                qty_label = f"{line.quantity:g} {line.uom_name or ''}".strip()
                name = escape(line.name or "")
                if show_prices:
                    rows.append(
                        "<tr>"
                        f"<td>{name}</td>"
                        f"<td class='num'>{escape(qty_label)}</td>"
                        f"<td class='num'>${line.price_unit:,.2f}</td>"
                        f"<td class='num'>${line.price_subtotal:,.2f}</td>"
                        "</tr>"
                    )
                else:
                    rows.append(
                        f"<tr><td>{name} — {escape(qty_label)}</td></tr>"
                    )
        elif show_prices:
            rows.append(
                f"<tr><td colspan='3'>{escape(t['materials'])}</td>"
                f"<td class='num'>${materials_amt:,.2f}</td></tr>"
            )

        if show_prices:
            rows.append(
                f"<tr class='section-row'><td colspan='3'>{escape(t['labor'])}</td>"
                f"<td class='num'>${labor_amt:,.2f}</td></tr>"
            )
            for line in labor_lines:
                qty_label = f"{line.quantity:g} {line.uom_name or ''}".strip()
                rows.append(
                    "<tr>"
                    f"<td>{escape(line.name or '')}</td>"
                    f"<td class='num'>{escape(qty_label)}</td>"
                    f"<td class='num'>${line.price_unit:,.2f}</td>"
                    f"<td class='num'>${line.price_subtotal:,.2f}</td>"
                    "</tr>"
                )
        else:
            rows.append(f"<tr class='section-row'><td>{escape(t['labor'])}</td></tr>")
            for line in labor_lines:
                qty_label = f"{line.quantity:g} {line.uom_name or ''}".strip()
                rows.append(
                    f"<tr><td>{escape(line.name or '')} — {escape(qty_label)}</td></tr>"
                )

        rows.append("</tbody></table>")
        rows.append(
            f"<div class='total-bar'><span>{escape(t['total'])}</span>"
            f"<span class='total-amt'>${total_amt:,.2f}</span></div>"
        )
        return "".join(rows)

    def _render_terms_and_signatures(self, content, t, company, accent):
        """Company terms + proposal terms + signature blocks + contact footer."""
        self.ensure_one()
        Profile = self.env["bizflow.company.profile"]
        profile = Profile.search([("company_id", "=", company.id)], limit=1)
        legal = content.get("legal") or {}
        parts = []

        company_terms = []
        if profile:
            if profile.terms_text:
                company_terms.append(
                    f"<h4>{escape(t['terms_company'])}</h4>"
                    f"<div>{profile.terms_text}</div>"
                )
            if profile.responsibilities_text:
                company_terms.append(
                    f"<h4>{escape(t['responsibilities'])}</h4>"
                    f"<div>{profile.responsibilities_text}</div>"
                )
            if profile.warranty_text:
                company_terms.append(
                    f"<h4>{escape(t['warranty'])}</h4>"
                    f"<div>{profile.warranty_text}</div>"
                )
        if company_terms:
            parts.append(f"<div class='terms-block'>{''.join(company_terms)}</div>")

        proposal_terms = []
        if (legal.get("terms_md") or "").strip():
            proposal_terms.append(
                f"<h4>{escape(t['terms_proposal'])}</h4>"
                f"<p>{escape(legal.get('terms_md'))}</p>"
            )
        if (legal.get("responsibilities_md") or "").strip():
            proposal_terms.append(
                f"<h4>{escape(t['responsibilities'])}</h4>"
                f"<p>{escape(legal.get('responsibilities_md'))}</p>"
            )
        if proposal_terms:
            parts.append(f"<div class='terms-block'>{''.join(proposal_terms)}</div>")

        # Signatures
        sig_blocks = []
        base = self.env["ir.config_parameter"].sudo().get_str("web.base.url", "").rstrip("/")
        signers = profile.signer_ids.filtered(lambda s: s.active) if profile else self.env["bizflow.company.signer"]
        if signers:
            for signer in signers:
                img = ""
                if signer.signature_image:
                    img = (
                        f"<img src='{escape(base)}/web/image/bizflow.company.signer/"
                        f"{signer.id}/signature_image' alt='signature'/>"
                    )
                sig_blocks.append(
                    "<div class='sig-block'>"
                    f"<div class='sig-line'>{img}</div>"
                    f"<div class='sig-name'>{escape(signer.name or '')}</div>"
                    f"<div class='sig-title'>{escape(signer.title or '')}</div>"
                    "</div>"
                )
        else:
            sig_blocks.append(
                "<div class='sig-block'>"
                "<div class='sig-line'></div>"
                f"<div class='sig-name'>{escape(company.name or t['company_sig'])}</div>"
                f"<div class='sig-title'>{escape(t['company_sig'])}</div>"
                "</div>"
            )
        sig_blocks.append(
            "<div class='sig-block'>"
            "<div class='sig-line'></div>"
            f"<div class='sig-name'>{escape(t['client_sig'])}</div>"
            "</div>"
        )
        parts.append(
            f"<h3 class='section-title'>{escape(t['signatures'])}</h3>"
            f"<div class='sig-grid'>{''.join(sig_blocks)}</div>"
        )

        # Contact footer
        contact_bits = []
        if profile:
            if profile.website:
                href = escape(self._absolute_http_url(profile.website))
                contact_bits.append(
                    f"<a href='{href}' target='_blank' rel='noopener noreferrer'>"
                    f"{escape(profile.website)}</a>"
                )
            if profile.phone:
                contact_bits.append(escape(profile.phone))
            if profile.phone_alt:
                contact_bits.append(escape(profile.phone_alt))
            if profile.email:
                contact_bits.append(
                    f"<a href='mailto:{escape(profile.email)}'>{escape(profile.email)}</a>"
                )
            if profile.address:
                contact_bits.append(escape(profile.address.replace("\n", ", ")))
            if profile.social_url:
                href = escape(self._absolute_http_url(profile.social_url))
                contact_bits.append(
                    f"<a href='{href}' target='_blank' rel='noopener noreferrer'>"
                    f"{escape(profile.social_url)}</a>"
                )
        if not contact_bits:
            contact_bits.append(escape(t["questions"]))

        parts.append(
            f"<div class='rule' style='background:{accent}'></div>"
            "<footer class='doc-footer'>"
            f"<p class='muted'>{escape(company.name or '')}</p>"
            f"<div class='contact-row'>{''.join(f'<span>{b}</span>' for b in contact_bits)}</div>"
            "</footer>"
        )
        return "".join(parts)

    @staticmethod
    def _absolute_http_url(url):
        """Ensure external links are absolute so srcdoc iframes do not navigate Odoo."""
        raw = (url or "").strip()
        if not raw:
            return ""
        if raw.startswith(("http://", "https://", "mailto:", "tel:")):
            return raw
        return f"https://{raw.lstrip('/')}"

    def _company_logo_html(self, company):
        base = self.env["ir.config_parameter"].sudo().get_str("web.base.url", "").rstrip("/")
        if company.logo or getattr(company, "logo_web", False):
            url = escape(f"{base}/web/image/res.company/{company.id}/logo")
            return f"<img src='{url}' alt='logo'/>"
        return (
            "<div style='width:56px;height:56px;background:#111;border-radius:6px;'></div>"
        )

    def _render_utility_section(self, content):
        utility = content.get("utility") or {}
        return (
            "<div class='page'><h2>Project utility</h2>"
            f"<p>{escape(utility.get('intro') or '')}</p>"
            "<table><tbody>"
            f"<tr><td>Revenue</td><td>{self.amount_total:.2f}</td></tr>"
            f"<tr><td>Total cost</td><td>{self.amount_cost:.2f}</td></tr>"
            f"<tr><th>Utility</th><th class='accent'>{self.amount_utility:.2f}</th></tr>"
            f"<tr><td>Margin</td><td>{self.utility_margin:.1f}%</td></tr>"
            "</tbody></table></div>"
        )

    def _asset_figure(self, asset):
        url = escape(asset.get_signed_url(ttl_seconds=86400))
        label = escape(asset.label or asset.filename or "image")
        return f"<figure class='ba-photo'><img src='{url}' alt='{label}'/></figure>"

    def _render_slot_image(self, slot, empty_label):
        self.ensure_one()
        assets = self.asset_ids.filtered(lambda a: a.slot == slot)
        if assets:
            figs = [self._asset_figure(asset) for asset in assets]
            return f"<div class='ba-stack'>{''.join(figs)}</div>"
        field_name = "before_image" if slot == "before" else "after_image"
        if slot in ("before", "after") and self[field_name]:
            base = self.env["ir.config_parameter"].sudo().get_str("web.base.url", "").rstrip("/")
            url = escape(f"{base}/web/image/bizflow.proposal/{self.id}/{field_name}")
            return f"<figure class='ba-photo'><img src='{url}' alt='{escape(slot)}'/></figure>"
        return f"<p class='muted'>{escape(empty_label)}</p>"

    def _render_before_after_pairs(self, t):
        """Single Antes/Después headers; stack all photos under each column."""
        self.ensure_one()
        befores = self.asset_ids.filtered(lambda a: a.slot == "before").sorted("id")
        afters = self.asset_ids.filtered(lambda a: a.slot == "after")
        used_after_ids = set()

        def _empty(label):
            return f"<p class='muted'>{escape(label)}</p>"

        def _stack(figs, empty_label):
            if not figs:
                return _empty(empty_label)
            if len(figs) == 1:
                return figs[0]
            return f"<div class='ba-stack'>{''.join(figs)}</div>"

        before_figs = []
        after_figs = []
        if befores:
            for before in befores:
                before_figs.append(self._asset_figure(before))
                child_afters = afters.filtered(lambda a: a.parent_id.id == before.id).sorted("id")
                for child in child_afters:
                    used_after_ids.add(child.id)
                    after_figs.append(self._asset_figure(child))
        else:
            # Legacy binary fields when there are no before assets
            if self.before_image:
                base = self.env["ir.config_parameter"].sudo().get_str("web.base.url", "").rstrip("/")
                url = escape(f"{base}/web/image/bizflow.proposal/{self.id}/before_image")
                before_figs.append(
                    f"<figure class='ba-photo'><img src='{url}' alt='before'/></figure>"
                )
            if self.after_image and not afters:
                base = self.env["ir.config_parameter"].sudo().get_str("web.base.url", "").rstrip("/")
                url = escape(f"{base}/web/image/bizflow.proposal/{self.id}/after_image")
                after_figs.append(
                    f"<figure class='ba-photo'><img src='{url}' alt='after'/></figure>"
                )

        orphan_afters = afters.filtered(lambda a: a.id not in used_after_ids)
        for orphan in orphan_afters.sorted("id"):
            after_figs.append(self._asset_figure(orphan))

        if not before_figs and not after_figs:
            return ""

        return (
            "<div class='ba-grid'>"
            f"<div><div class='ba-label'>{escape(t['before'])}</div>"
            f"{_stack(before_figs, t['before_empty'])}</div>"
            f"<div><div class='ba-label'>{escape(t['after'])}</div>"
            f"{_stack(after_figs, t['after_empty'])}</div>"
            "</div>"
        )

    def to_editor_payload(self):
        self.ensure_one()
        gen = self.generation if isinstance(self.generation, dict) else {}
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "state": self.state,
            "kind": self.kind or "proposal",
            "materials_report_mode": self.materials_report_mode or "detailed_prices",
            "language": self.language or "es",
            "partner_id": self.partner_id.id,
            "partner_name": self.partner_id.display_name,
            "lead_id": self.lead_id.id,
            "order_id": self.order_id.id,
            "order_name": self.order_id.display_name if self.order_id else "",
            "project_type": self.project_type or "",
            "location": self.location or "",
            "description": self.description or "",
            "brief": self.brief or self.description or "",
            "work_brief": self.work_brief or "",
            "content": normalize_content(self.content or {}),
            "section_order": self.section_order or list(SECTION_ORDER),
            "hidden_sections": self.hidden_sections or [],
            "section_labels": SECTION_LABELS,
            "hashes": self.section_hashes(),
            "version": self.version,
            "generation": {
                "status": gen.get("status") or "idle",
                "percent": int(gen.get("percent") or 0),
                "section": gen.get("section") or "",
                "error": gen.get("error") or "",
                "started_by": gen.get("started_by") or "",
            },
            "access_code": self.access_code or "",
            "access_required": self.access_required,
            "amounts": {
                "materials": self.amount_materials,
                "labor": self.amount_labor,
                "subtotal": self.amount_subtotal,
                "markup": self.amount_markup,
                "total": self.amount_total,
                "cost": self.amount_cost,
                "utility": self.amount_utility,
                "utility_margin": self.utility_margin,
                "currency": self.currency_id.name,
            },
            "lines": [
                {
                    "id": line.id,
                    "name": line.name,
                    "line_type": line.line_type,
                    "quantity": line.quantity,
                    "uom_name": line.uom_name,
                    "price_unit": line.price_unit,
                    "cost_unit": line.cost_unit,
                    "price_subtotal": line.price_subtotal,
                    "supplier_id": line.supplier_id.id,
                    "supplier_name": line.supplier_id.name or "",
                    "material_id": line.material_id.id,
                    "source": line.source,
                    "notes": line.notes or "",
                    "product_url": line.product_url or "",
                }
                for line in self.line_ids
            ],
            "assets": [a.to_dict() for a in self.asset_ids],
            "share_links": [
                {
                    "id": link.id,
                    "name": link.name,
                    "purpose": link.purpose,
                    "token": link.token,
                    "url": link.get_full_url(),
                    "allow_download": link.allow_download,
                    "enabled": link.enabled,
                    "views": link.views,
                    "qualified_views": link.qualified_views,
                }
                for link in self.share_link_ids
            ],
            "comments": [
                {
                    "id": c.id,
                    "section": c.section or "",
                    "author_name": c.author_name,
                    "body": c.body,
                    "resolved": c.resolved,
                    "create_date": fields.Datetime.to_string(c.create_date) if c.create_date else "",
                }
                for c in self.comment_ids
            ],
            "decisions": [
                {
                    "id": d.id,
                    "decision": d.decision,
                    "note": d.note or "",
                    "author_name": d.author_name,
                    "create_date": fields.Datetime.to_string(d.create_date) if d.create_date else "",
                }
                for d in self.decision_ids
            ],
            "signing": [
                {
                    "id": s.id,
                    "status": s.status,
                    "url": s.get_full_url(),
                    "access_code": s.access_code,
                    "company_signed": s.company_signed,
                    "client_signed": s.client_signed,
                    "kickoff_date": fields.Date.to_string(s.kickoff_date) if s.kickoff_date else "",
                    "frozen_hash": s.frozen_hash or "",
                }
                for s in self.signing_ids
            ],
            "costs": [
                {
                    "id": c.id,
                    "name": c.name,
                    "category": c.category,
                    "quantity": c.quantity,
                    "unit": c.unit,
                    "rate": c.rate,
                    "amount": c.amount,
                    "notes": c.notes or "",
                }
                for c in self.cost_ids
            ],
            "pdf_template_id": self.pdf_template_id.id,
            "before_after_model_id": self.before_after_model_id.id,
            "html": str(self.render_html()),
        }
