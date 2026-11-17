# -*- coding: utf-8 -*-
import base64
import json
import logging
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.bizflow_proposals.models.services.binary_utils import binary_to_b64

_logger = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = "gpt-image-2.5-flare"


class BizflowAiController(http.Controller):

    @http.route("/bizflow/ai/models", type="http", auth="user", methods=["GET"], csrf=False)
    def list_models(self, purpose=None, **kwargs):
        domain = [("active", "=", True)]
        if purpose:
            domain.append(("purpose", "=", purpose))
        models = request.env["bizflow.ai.model"].search(domain)
        return request.make_json_response(
            {
                "items": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "purpose": m.purpose,
                        "provider": m.provider,
                        "model_name": m.model_name,
                    }
                    for m in models
                ]
            }
        )

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/ai/before_after",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def generate_before_after(self, proposal_id, **payload):
        proposal = request.env["bizflow.proposal"].browse(int(proposal_id)).exists()
        if not proposal:
            return {"error": "not_found"}
        model = proposal.before_after_model_id
        if payload.get("model_id"):
            model = request.env["bizflow.ai.model"].browse(int(payload["model_id"])).exists()
        if not model:
            models = request.env["bizflow.ai.model"].search(
                [("purpose", "=", "before_after"), ("active", "=", True)], limit=1
            )
            model = models[:1]
        if not model:
            return {
                "error": "no_model",
                "message": request.env._("Configure an AI model for before/after photos."),
            }
        before_asset = False
        if payload.get("before_asset_id"):
            before_asset = (
                request.env["bizflow.proposal.asset"]
                .browse(int(payload["before_asset_id"]))
                .exists()
            )
            if not before_asset or before_asset.proposal_id.id != proposal.id:
                return {
                    "error": "not_found",
                    "message": request.env._("Before image not found on this proposal."),
                }
            if (before_asset.slot or "") != "before":
                return {
                    "error": "invalid_slot",
                    "message": request.env._("Select a before image to generate its after."),
                }

        before_image = (before_asset.datas if before_asset else False) or proposal.before_image or payload.get(
            "before_image"
        )
        if not before_image:
            return {
                "error": "no_before_image",
                "message": request.env._("Upload a before photo first."),
            }
        if not before_asset:
            return {
                "error": "before_asset_required",
                "message": request.env._("Choose which before image to generate from."),
            }

        prompt = payload.get("prompt") or model.default_prompt or (
            "Photorealistic construction remodel after photo. Project type: {project_type}. Notes: {notes}."
        )
        prompt = prompt.format(
            project_type=proposal.project_type or "residential remodel",
            notes=payload.get("notes") or (proposal.get_section("before_after").get("notes") or ""),
            scope=proposal.description or proposal.brief or "",
        )
        try:
            image_b64 = self._generate_image(model, prompt, before_image)
        except Exception as exc:
            _logger.exception("Before/after generation failed")
            return {"error": "generation_failed", "message": str(exc)}

        proposal.write({"after_image": image_b64})
        if not proposal.before_image:
            proposal.before_image = before_asset.datas
        section = proposal.get_section("before_after")
        section["after_prompt"] = prompt
        proposal.save_section("before_after", section)

        before_label = before_asset.label or before_asset.filename or f"#{before_asset.id}"
        asset = request.env["bizflow.proposal.asset"].create(
            {
                "proposal_id": proposal.id,
                "kind": "generated",
                "label": payload.get("label") or f"After · {before_label}",
                "slot": "after",
                "parent_id": before_asset.id,
                "prompt": prompt,
                "mime": "image/png",
                "datas": image_b64,
                "filename": f"after_{before_asset.id}.png",
            }
        )
        proposal.log_change(
            "image",
            summary=f"Generated after for {before_label}",
            restorable=False,
        )
        return {
            "ok": True,
            "prompt": prompt,
            "asset": asset.to_dict(),
            "html": str(proposal.render_html()),
            "proposal": proposal.to_editor_payload(),
            "message": request.env._("After image generated and saved on the proposal."),
        }

    def _generate_image(self, model, prompt, before_image=False):
        if not model.api_key:
            raise UserError(request.env._("AI model %s has no API key.", model.name))
        base = (model.api_base_url or "https://api.openai.com/v1").rstrip("/")
        model_name = model.model_name or DEFAULT_IMAGE_MODEL
        before_b64 = binary_to_b64(before_image)

        if before_b64:
            url = f"{base}/images/edits"
            body = {
                "model": model_name,
                "prompt": prompt,
                "images": [{"image_url": f"data:image/jpeg;base64,{before_b64}"}],
                "n": 1,
                "size": "1024x1024",
            }
        else:
            url = f"{base}/images/generations"
            body = {
                "model": model_name,
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
            }

        if isinstance(model.extra_params, dict):
            body.update(model.extra_params)

        data = json.dumps(body).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {model.api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            try:
                err = json.loads(detail).get("error") or {}
                message = err.get("message") or detail
            except Exception:
                message = detail or str(exc)
            raise UserError(
                request.env._("AI image request failed (%s): %s", exc.code, message)
            ) from exc

        items = payload.get("data") or []
        if not items:
            raise UserError(request.env._("AI provider returned no image."))
        item = items[0]
        if item.get("b64_json"):
            return item["b64_json"]
        if item.get("url"):
            with urlopen(item["url"], timeout=120) as img_resp:
                return base64.b64encode(img_resp.read()).decode("ascii")
        raise UserError(request.env._("Unexpected AI image payload."))

    @http.route(
        "/bizflow/proposals/<int:proposal_id>/ai/pricing",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
    )
    def analyze_pricing(self, proposal_id, **payload):
        """BOM via suppliers. Default: preview candidates (2C). apply=true writes lines."""
        proposal = request.env["bizflow.proposal"].browse(int(proposal_id)).exists()
        if not proposal:
            return {"error": "not_found"}
        try:
            from odoo.addons.bizflow_proposals.models.services import pricing as pricing_svc

            return pricing_svc.analyze_and_apply_pricing(
                proposal,
                materials_prompt=payload.get("materials_prompt"),
                apply=bool(payload.get("apply")),
                candidates=payload.get("candidates"),
                labor=payload.get("labor"),
                labor_items=payload.get("labor_items"),
                exclusions=payload.get("exclusions"),
            )
        except Exception as exc:
            _logger.exception("Pricing analysis failed")
            return {"error": "pricing_failed", "message": str(exc)}
