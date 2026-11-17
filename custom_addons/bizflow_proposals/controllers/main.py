# -*- coding: utf-8 -*-
import base64
import json

from odoo import http
from odoo.http import request

from odoo.addons.bizflow_proposals.models.schema import SECTION_LABELS, SECTION_ORDER
from odoo.addons.bizflow_proposals.models.services.binary_utils import binary_to_bytes


class BizflowProposalController(http.Controller):

    def _json(self, payload, status=200):
        return request.make_json_response(payload, status=status)

    def _proposal(self, proposal_id):
        return request.env["bizflow.proposal"].browse(int(proposal_id)).exists()

    @http.route("/bizflow/proposals", type="http", auth="user", methods=["GET"], csrf=False)
    def list_proposals(self, **kwargs):
        domain = [("archived", "=", False)]
        if kwargs.get("lead_id"):
            domain.append(("lead_id", "=", int(kwargs["lead_id"])))
        if kwargs.get("state"):
            domain.append(("state", "=", kwargs["state"]))
        if kwargs.get("q"):
            domain += ["|", ("name", "ilike", kwargs["q"]), ("code", "ilike", kwargs["q"])]
        rows = request.env["bizflow.proposal"].search(domain, limit=100)
        return self._json(
            {
                "items": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "code": p.code,
                        "state": p.state,
                        "partner_name": p.partner_id.display_name,
                        "amount_total": p.amount_total,
                        "amount_utility": p.amount_utility,
                        "lead_id": p.lead_id.id,
                    }
                    for p in rows
                ]
            }
        )

    @http.route("/bizflow/proposals", type="jsonrpc", auth="user", methods=["POST"])
    def create_proposal(self, **payload):
        brief = payload.get("brief") or payload.get("description") or ""
        vals = {
            "name": payload.get("name") or request.env._("New construction proposal"),
            "lead_id": payload.get("lead_id") or False,
            "partner_id": payload.get("partner_id") or False,
            "project_type": payload.get("project_type") or "",
            "location": payload.get("location") or "",
            "description": brief,
            "brief": brief,
            "work_brief": payload.get("work_brief") or "",
            "language": payload.get("language") or "es",
            "kind": payload.get("kind") or "proposal",
        }
        proposal = request.env["bizflow.proposal"].create(vals)
        write_with_ai = bool(payload.get("write_with_ai") or payload.get("generate"))
        if write_with_ai:
            proposal.start_generation(
                extra_instructions=payload.get("instructions") or "",
                user_message=request.env._("Write the full proposal from this description."),
            )
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_proposal(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        return self._json(proposal.to_editor_payload())

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/meta",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def update_meta(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        allowed = {
            "name",
            "state",
            "partner_id",
            "lead_id",
            "project_type",
            "location",
            "description",
            "brief",
            "work_brief",
            "language",
            "kind",
            "materials_report_mode",
            "access_required",
            "hidden_sections",
            "section_order",
            "pdf_template_id",
            "before_after_model_id",
            "copy_model_id",
            "price_model_id",
        }
        vals = {k: payload[k] for k in allowed if k in payload}
        if vals:
            proposal.write(vals)
            proposal.log_change("meta", summary="Updated details", before={}, after=vals)
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/sections/<string:key>",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def save_section(self, proposal_id, key, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        data = payload.get("data") if "data" in payload else payload
        if isinstance(data, str):
            data = json.loads(data)
        result = proposal.save_section(key, data or {}, base_hash=payload.get("base_hash"))
        if result.get("conflict"):
            return {
                "conflict": True,
                "section": result["section"],
                "hash": result["hash"],
                "version": result["version"],
                "message": request.env._("Someone else changed this page. Keep yours or use theirs."),
            }
        return {
            "conflict": False,
            "section": result["section"],
            "hash": result["hash"],
            "version": proposal.version,
            "html": str(proposal.render_html()),
            "amounts": proposal.to_editor_payload()["amounts"],
        }

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/render",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def render_proposal(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        return self._json({"html": str(proposal.render_html())})

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/lines",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def upsert_line(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        Line = request.env["bizflow.proposal.line"]
        line_id = payload.get("id")
        # Editor edits always win: never block price/qty changes based on AI/provider source.
        source = payload.get("source") or "manual"
        if line_id and payload.get("price_unit") is not None:
            source = payload.get("source") or "manual"
        vals = {
            "proposal_id": proposal.id,
            "name": payload.get("name") or request.env._("Line"),
            "line_type": payload.get("line_type") or "material",
            "quantity": float(payload.get("quantity") or 1.0),
            "uom_name": payload.get("uom_name") or "unit",
            "price_unit": float(payload.get("price_unit") or 0.0),
            "cost_unit": float(payload.get("cost_unit") or payload.get("price_unit") or 0.0),
            "supplier_id": payload.get("supplier_id") or False,
            "material_id": payload.get("material_id") or False,
            "source": source,
            "product_url": payload.get("product_url") or "",
            "notes": payload.get("notes") or "",
        }
        if line_id:
            line = Line.browse(int(line_id)).exists()
            if line:
                # Partial-safe write: only touch provided economic fields from editor.
                line.write(vals)
            else:
                line = Line.create(vals)
        else:
            line = Line.create(vals)
        proposal.sync_utility_from_lines()
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/lines/<int:line_id>",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def delete_line(self, proposal_id, line_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        line = request.env["bizflow.proposal.line"].browse(line_id).exists()
        if line and line.proposal_id.id == proposal.id:
            line.unlink()
        proposal.sync_utility_from_lines()
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/share",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def create_share(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        link = proposal.action_create_share_link(
            purpose=payload.get("purpose") or "view",
            allow_download=bool(payload.get("allow_download", True)),
        )
        if payload.get("name"):
            link.name = payload["name"]
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/access_code",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def rotate_access_code(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        if "access_required" in payload:
            proposal.access_required = bool(payload["access_required"])
        if payload.get("rotate"):
            proposal.action_new_access_code()
        return {
            "access_code": proposal.access_code,
            "access_required": proposal.access_required,
        }

    @http.route("/bizflow/meta/sections", type="http", auth="user", methods=["GET"], csrf=False)
    def meta_sections(self, **kwargs):
        return self._json({"order": SECTION_ORDER, "labels": SECTION_LABELS})

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/sync_utility",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def sync_utility(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        proposal.sync_utility_from_lines()
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/generate",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def generate_proposal(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        if payload.get("brief") or payload.get("work_brief"):
            vals = {}
            if payload.get("brief"):
                vals["brief"] = payload["brief"]
                vals["description"] = payload["brief"]
            if payload.get("work_brief"):
                vals["work_brief"] = payload["work_brief"]
            proposal.write(vals)
        started = proposal.start_generation(
            extra_instructions=payload.get("instructions") or "",
            user_message=payload.get("message")
            or request.env._("Write the full proposal from this description."),
        )
        if not started:
            return {
                "error": "busy",
                "message": request.env._("Already writing this proposal. Wait until it finishes."),
                "proposal": proposal.to_editor_payload(),
            }
        return {"ok": True, "status": 202, "proposal": proposal.to_editor_payload()}

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/state",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_state(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        return self._json(proposal.get_state_payload())

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/state",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def post_state(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        request.env["bizflow.proposal.presence"].heartbeat(
            proposal, section=payload.get("section") or ""
        )
        return proposal.get_state_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/chat",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_chat(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        thread = kwargs.get("thread") or ""
        rows = request.env["bizflow.proposal.message"].search(
            [("proposal_id", "=", proposal.id), ("thread", "=", thread)],
            order="id asc",
        )
        return self._json(
            {
                "messages": [
                    {
                        "id": m.id,
                        "role": m.role,
                        "text": m.text,
                        "thread": m.thread,
                        "sections_touched": m.sections_touched or [],
                        "author": m.author_id.name,
                        "create_date": m.create_date,
                    }
                    for m in rows
                ]
            }
        )

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/chat",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def post_chat(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        message = (payload.get("message") or "").strip()
        if not message:
            return {"error": "empty", "message": request.env._("Write a message first.")}
        proposal.apply_chat(
            message,
            thread=payload.get("thread") or "",
            focus=payload.get("focus") or "",
            only=bool(payload.get("only")),
            redo=bool(payload.get("redo")),
        )
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/chat/reset",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def reset_chat(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        proposal.reset_chat(thread=payload.get("thread") or "")
        return {"ok": True}

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/pages/<string:key>/chat",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def page_chat(self, proposal_id, key, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        message = (payload.get("message") or "").strip()
        if not message:
            return {"error": "empty"}
        proposal.apply_chat(
            message,
            thread=key,
            focus=key,
            only=True,
            redo=bool(payload.get("redo")),
        )
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/changes",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_changes(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        domain = [("proposal_id", "=", proposal.id)]
        if kwargs.get("section"):
            domain.append(("section", "=", kwargs["section"]))
        if kwargs.get("kind"):
            domain.append(("kind", "=", kwargs["kind"]))
        rows = request.env["bizflow.proposal.change"].search(domain, limit=300)
        return self._json(
            {
                "items": [
                    {
                        "id": c.id,
                        "kind": c.kind,
                        "section": c.section,
                        "summary": c.summary,
                        "version": c.version,
                        "restorable": c.restorable,
                        "author": c.author_id.name,
                        "create_date": c.create_date,
                        "before": c.before,
                        "after": c.after,
                    }
                    for c in rows
                ]
            }
        )

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/changes/<int:change_id>/restore",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def restore_change(self, proposal_id, change_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        change = request.env["bizflow.proposal.change"].browse(change_id).exists()
        if not change or change.proposal_id.id != proposal.id or not change.restorable:
            return {"error": "not_restorable"}
        target = payload.get("to") or "before"
        data = change.before if target == "before" else change.after
        if change.section and isinstance(data, dict):
            proposal.save_section(change.section, data)
            proposal.log_change(
                "restore",
                section=change.section,
                after=data,
                summary=f"Restored {change.section}",
            )
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/assets",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_assets(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        return self._json({"items": [a.to_dict() for a in proposal.asset_ids]})

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/assets",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def upload_asset(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        datas = payload.get("datas") or payload.get("file")
        if not datas:
            return {
                "error": "no_file",
                "message": request.env._("Choose an image or PDF plan to upload."),
            }
        # Strip data-uri prefix if present
        if isinstance(datas, str) and "," in datas and datas.strip().startswith("data:"):
            header, datas = datas.split(",", 1)
            mime = header.split(";")[0].replace("data:", "") or "image/png"
        else:
            mime = payload.get("mime") or "image/png"
        filename = payload.get("filename") or (
            "plan.pdf" if "pdf" in (mime or "").lower() else "upload.png"
        )
        slot = (payload.get("slot") or "").strip()
        if not slot and (
            "pdf" in (mime or "").lower() or str(filename).lower().endswith(".pdf")
        ):
            slot = "plan"
        asset = request.env["bizflow.proposal.asset"].create(
            {
                "proposal_id": proposal.id,
                "kind": "upload",
                "label": (payload.get("label") or "")[:200],
                "slot": slot,
                "mime": mime,
                "datas": datas,
                "filename": filename,
            }
        )
        # Place into known binary fields when slot matches (images only)
        is_pdf = "pdf" in (mime or "").lower() or str(filename).lower().endswith(".pdf")
        if not is_pdf:
            if slot == "cover":
                proposal.cover_image = datas
            elif slot == "before":
                proposal.before_image = datas
            elif slot == "after":
                proposal.after_image = datas
        proposal.log_change("image", summary=f"Uploaded {asset.label or asset.filename}", restorable=False)
        return {"asset": asset.to_dict(), "proposal": proposal.to_editor_payload()}

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/assets/<int:asset_id>",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def update_asset(self, proposal_id, asset_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        asset = request.env["bizflow.proposal.asset"].browse(asset_id).exists()
        if not asset or asset.proposal_id.id != proposal.id:
            return {"error": "not_found"}
        if payload.get("delete"):
            if asset.is_referenced() and not payload.get("force"):
                return {
                    "error": "in_use",
                    "message": request.env._("This image is used in the proposal. Remove it from the page first."),
                }
            slot = asset.slot or ""
            # Removing a before also removes its generated after children.
            children = request.env["bizflow.proposal.asset"].search(
                [("parent_id", "=", asset.id)]
            )
            if children:
                children.unlink()
            asset.unlink()
            # Keep cover/before/after binary fields in sync with remaining slot assets.
            remaining = proposal.asset_ids.filtered(lambda a: a.slot == slot)[:1]
            if slot == "cover":
                proposal.cover_image = remaining.datas if remaining else False
            elif slot == "before":
                proposal.before_image = remaining.datas if remaining else False
            elif slot == "after":
                proposal.after_image = remaining.datas if remaining else False
            proposal.log_change("image", summary=f"Removed {slot or 'image'}", restorable=False)
            return {"ok": True, "proposal": proposal.to_editor_payload()}
        vals = {}
        if "label" in payload:
            vals["label"] = (payload.get("label") or "")[:200]
        if "slot" in payload:
            vals["slot"] = payload.get("slot") or ""
        if vals:
            asset.write(vals)
        return {"asset": asset.to_dict(), "proposal": proposal.to_editor_payload()}

    @http.route(
        "/bizflow/assets/file/<string:token>/",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def asset_file(self, token, **kwargs):
        Asset = request.env["bizflow.proposal.asset"].sudo()
        try:
            asset = Asset.verify_token(token)
        except Exception:
            return request.not_found()
        raw = binary_to_bytes(asset.datas)
        if not raw:
            return request.not_found()
        return request.make_response(
            raw,
            headers=[
                ("Content-Type", asset.mime or "image/png"),
                ("Content-Length", str(len(raw))),
                ("Cache-Control", "private, max-age=3600"),
            ],
        )

    @http.route("/bizflow/proposals/next-number", type="http", auth="user", methods=["GET"], csrf=False)
    def next_number(self, **kwargs):
        seq = request.env["ir.sequence"].search([("code", "=", "bizflow.proposal")], limit=1)
        number = (seq.number_next_actual if seq else 1) or 1
        prefix = (seq.prefix if seq else "PROP/") or "PROP/"
        padding = seq.padding if seq else 4
        return self._json({"preview": f"{prefix}{str(number).zfill(padding)}", "hint": "PROP/####"})

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/duplicate",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def duplicate_proposal(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        copy = proposal.action_duplicate()
        return copy.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/archive",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def archive_proposal(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        if payload.get("permanent"):
            proposal.unlink()
            return {"ok": True, "deleted": True}
        proposal.action_archive_proposal()
        return {"ok": True, "archived": True}

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/export/html",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def export_html(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        html = proposal.export_html()
        return request.make_response(
            html,
            headers=[
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Disposition", f'attachment; filename="{proposal.code}.html"'),
            ],
        )

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/export/pdf",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def export_pdf(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        try:
            pdf = proposal.export_pdf_bytes()
        except Exception as exc:
            return self._json(
                {
                    "error": "pdf_unavailable",
                    "message": str(exc),
                    "fallback": "print",
                },
                status=501,
            )
        return request.make_response(
            pdf,
            headers=[
                ("Content-Type", "application/pdf"),
                ("Content-Disposition", f'attachment; filename="{proposal.code}.pdf"'),
            ],
        )

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/design",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def design_chat(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        message = (payload.get("message") or "").strip()
        if not message:
            return {"error": "empty"}
        proposal.apply_chat(message, thread="design", focus="design", only=True)
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/extras",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def extras_upsert(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        content = dict(proposal.content or {})
        extras = dict(content.get("extras") or {})
        pages = list(extras.get("pages") or [])
        if payload.get("delete_index") is not None:
            idx = int(payload["delete_index"])
            if 0 <= idx < len(pages):
                pages.pop(idx)
        else:
            page = {
                "title": payload.get("title") or request.env._("Extra page"),
                "body": payload.get("body") or "",
            }
            if payload.get("index") is not None and 0 <= int(payload["index"]) < len(pages):
                pages[int(payload["index"])] = page
            else:
                pages.append(page)
        extras["pages"] = pages
        content["extras"] = extras
        proposal.write({"content": content})
        proposal.log_change("edit", section="extras", after=extras, summary="Updated extras")
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/extras/<int:page_index>/ai",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def extras_ai(self, proposal_id, page_index, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        message = (payload.get("message") or "Write this extra page for the client.").strip()
        proposal.apply_chat(
            f"[extra page {page_index}] {message}",
            thread=f"extras:{page_index}",
            focus="extras",
            only=True,
        )
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/utility-analysis",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def utility_analysis(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        utility = proposal.get_section("utility")
        return self._json(
            {
                "revenue": proposal.amount_total,
                "cost": proposal.amount_cost,
                "utility": proposal.amount_utility,
                "margin": proposal.utility_margin,
                "intro": utility.get("intro") or "",
                "assumptions": utility.get("assumptions") or [],
                "needs_repair": proposal.amount_utility < 0 or not utility.get("intro"),
            }
        )

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/comments",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_comments(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        return self._json({"items": proposal.to_editor_payload()["comments"]})

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/comments",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def add_comment(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        comment = request.env["bizflow.proposal.comment"].create(
            {
                "proposal_id": proposal.id,
                "section": payload.get("section") or "",
                "author_name": payload.get("author_name") or request.env.user.name,
                "author_email": payload.get("author_email") or request.env.user.email or "",
                "body": payload.get("body") or "",
                "is_internal": bool(payload.get("is_internal")),
            }
        )
        if payload.get("resolve_id"):
            resolved = request.env["bizflow.proposal.comment"].browse(int(payload["resolve_id"])).exists()
            if resolved:
                resolved.resolved = True
        return {"comment": {"id": comment.id, "body": comment.body}, "proposal": proposal.to_editor_payload()}

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/comments/<int:comment_id>",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def update_comment(self, proposal_id, comment_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        comment = request.env["bizflow.proposal.comment"].browse(comment_id).exists()
        if not comment or comment.proposal_id.id != proposal.id:
            return {"error": "not_found"}
        if payload.get("delete"):
            comment.unlink()
        elif payload.get("resolve"):
            comment.resolved = True
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/signing",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_signing(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        return self._json({"items": proposal.to_editor_payload()["signing"]})

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/signing/requests",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def create_signing(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        req = proposal.action_create_signing_request(
            signer_name=payload.get("signer_name") or "",
            signer_email=payload.get("signer_email") or "",
        )
        return {"request": proposal.to_editor_payload()["signing"][-1], "url": req.get_full_url()}

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/signing/<int:request_id>/company",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def company_sign(self, proposal_id, request_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        req = request.env["bizflow.signing.request"].browse(request_id).exists()
        if not req or req.proposal_id.id != proposal.id:
            return {"error": "not_found"}
        if payload.get("withdraw"):
            req.write({"company_signed": False, "company_signed_at": False, "company_signer": ""})
        else:
            req.action_company_sign(signer_name=payload.get("signer_name") or "")
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/signing/<int:request_id>/revoke",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def revoke_signing(self, proposal_id, request_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        req = request.env["bizflow.signing.request"].browse(request_id).exists()
        if not req or req.proposal_id.id != proposal.id:
            return {"error": "not_found"}
        if payload.get("new_code"):
            req.action_new_code()
        else:
            req.action_revoke()
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/kickoff",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def set_kickoff(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        req = proposal.signing_ids[:1]
        if not req:
            return {"error": "no_signing"}
        req.kickoff_date = payload.get("kickoff_date")
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/costs",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_costs(self, proposal_id, **kwargs):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return self._json({"error": "not_found"}, status=404)
        return self._json({"items": proposal.to_editor_payload()["costs"]})

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/costs",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def upsert_cost(self, proposal_id, **payload):
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        Cost = request.env["bizflow.proposal.cost"]
        vals = {
            "proposal_id": proposal.id,
            "name": payload.get("name") or request.env._("Cost line"),
            "category": payload.get("category") or "labor",
            "quantity": float(payload.get("quantity") or 1.0),
            "unit": payload.get("unit") or "hour",
            "rate": float(payload.get("rate") or 0.0),
            "notes": payload.get("notes") or "",
        }
        if payload.get("id"):
            row = Cost.browse(int(payload["id"])).exists()
            if row:
                if payload.get("delete"):
                    row.unlink()
                    return proposal.to_editor_payload()
                row.write(vals)
            else:
                Cost.create(vals)
        else:
            Cost.create(vals)
        return proposal.to_editor_payload()

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/images",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def generate_image(self, proposal_id, **payload):
        """Generate/refine image; reuses before/after AI path and stores an asset."""
        proposal = self._proposal(proposal_id)
        if not proposal:
            return {"error": "not_found"}
        from odoo.addons.bizflow_proposals.controllers.ai import BizflowAiController

        result = BizflowAiController().generate_before_after(proposal_id, **payload)
        if result.get("error"):
            return result
        if proposal.after_image:
            asset = request.env["bizflow.proposal.asset"].create(
                {
                    "proposal_id": proposal.id,
                    "kind": "generated",
                    "label": payload.get("label") or "After visualization",
                    "slot": payload.get("slot") or "after",
                    "prompt": payload.get("prompt") or payload.get("notes") or "",
                    "mime": "image/png",
                    "datas": proposal.after_image,
                    "filename": "after.png",
                }
            )
            return {"asset": asset.to_dict(), "proposal": proposal.to_editor_payload()}
        return {"proposal": proposal.to_editor_payload(), **result}
