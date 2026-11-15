# -*- coding: utf-8 -*-
"""AI price assist: BOM via suppliers + labor by location; keep manual lines."""
from __future__ import annotations

import logging
import re

from odoo.exceptions import UserError

from . import llm

_logger = logging.getLogger(__name__)

PROVIDER_CODES = ("HOME_DEPOT", "LOWES")


def resolve_price_model(env, proposal=None):
    Ai = env["bizflow.ai.model"]
    model = proposal.price_model_id if proposal else False
    if not model:
        model = Ai.search([("purpose", "=", "price_assist"), ("active", "=", True)], limit=1)
    return model


def _provider_suppliers(env):
    """Active suppliers preferred (HD/Lowe's first when present)."""
    Supplier = env["bizflow.supplier"]
    preferred = Supplier.search(
        [("code", "in", list(PROVIDER_CODES)), ("active", "=", True)]
    )
    if preferred:
        return preferred
    return Supplier.search([("active", "=", True)])


def _parse_location(location):
    raw = (location or "").strip()
    zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\b", raw)
    return {"raw": raw, "zip": zip_m.group(1) if zip_m else ""}


def _location_query_suffix(location):
    info = _parse_location(location)
    if info["zip"]:
        return f" near {info['zip']}"
    if info["raw"]:
        city = info["raw"].split(",")[0].strip()
        return f" {city}" if city else ""
    return ""


def default_materials_prompt(proposal, lang_es):
    brief = proposal.brief or proposal.description or ""
    work = proposal.work_brief or ""
    location = proposal.location or ""
    project_type = proposal.project_type or ""
    if lang_es:
        return (
            "Analiza la imagen del sitio (si hay) y el brief para proponer materiales "
            "y mano de obra de construcción residencial en EE.UU.\n"
            f"Ubicación: {location}\nTipo: {project_type}\n"
            f"BRIEF:\n{brief}\n\nWORK:\n{work}\n"
            "Cantidades coherentes con medidas del brief. "
            "Buscar precios en Home Depot / Lowe's cuando sea posible."
        )
    return (
        "Analyze the site image (if any) and brief to propose residential "
        "construction materials and labor in the US.\n"
        f"Location: {location}\nProject type: {project_type}\n"
        f"BRIEF:\n{brief}\n\nWORK:\n{work}\n"
        "Quantities must match measurements in the brief. "
        "Prefer Home Depot / Lowe's pricing when possible."
    )


def _build_bom_system_prompt(lang_es, user_prompt):
    rules = (
        "CRITICAL: quantity must cover the FULL measured area in the brief "
        "(e.g. 250 sq ft stucco → many bags/buckets, not quantity=1). "
        "Use trade yields (approx 1 bag of stucco mix per ~15–25 sq ft as a guide). "
        "labor.hourly_rate must reflect the local market (CT/Northeast often $65–$95/hr). "
        "labor.hours must cover prep + apply for the full area (often 20–40+ hrs for 250 sq ft). "
        "labor.total = hours * hourly_rate. "
        "exclusions: short phrases from the brief (no siding, windows, AC, grill area, brick crown)."
    )
    schema = (
        "{"
        '"materials":[{"name":"...","query":"search terms in English for HD/Lowes",'
        '"quantity":1.0,"uom":"bag|bucket|sq ft|each","notes":"..."}],'
        '"labor":{"name":"...","hours":0,"hourly_rate":0,"total":0,'
        '"rationale":"based on location and scope"},'
        '"exclusions":["..."]'
        "}"
    )
    if lang_es:
        return (
            "Eres un estimator de construcción residencial en EE.UU. "
            f"Devuelve SOLO JSON con esta forma:\n{schema}\n"
            "Incluye materiales típicos con cantidades coherentes con las medidas. "
            f"{rules}\nIdioma de nombres visibles: español.\n"
            f"Instrucciones del usuario:\n{user_prompt}"
        )
    return (
        "You are a US residential construction estimator. "
        f"Return ONLY JSON:\n{schema}\n"
        "Include typical materials with quantities matching measurements. "
        f"{rules}\n"
        f"User instructions:\n{user_prompt}"
    )


def _pick_price_prompt(item, hits, location, lang_es):
    compact = [
        {
            "supplier": h.get("supplier_name") or h.get("supplier_code") or "",
            "name": h.get("name"),
            "price": h.get("price"),
            "url": h.get("url") or "",
            "sku": h.get("sku") or "",
        }
        for h in (hits or [])[:8]
    ]
    if lang_es:
        return (
            "Elige el mejor precio UNITARIO de proveedor para este material. "
            "Devuelve SOLO JSON: "
            '{"price":0,"supplier_name":"","url":"","sku":"","name":"","estimated":false,"reason":""}. '
            "price = costo por unidad (uom del material), NUNCA el total de línea. "
            "Si no hay precio usable en hits, estimated=true y price=estimación unitaria de mercado EE.UU. "
            f"para la ubicación {location}.\n"
            f"Material: {item}\nHits: {compact}"
        )
    return (
        "Pick the best UNIT supplier price for this construction material. "
        "Return ONLY JSON: "
        '{"price":0,"supplier_name":"","url":"","sku":"","name":"","estimated":false,"reason":""}. '
        "price = cost per unit (material uom), NEVER the line total. "
        "If no usable hit price, set estimated=true and price to a US market unit estimate "
        f"for location {location}.\n"
        f"Material: {item}\nHits: {compact}"
    )


def _before_image_data_url(proposal):
    """Return data URL for first before asset or legacy before_image field."""
    assets = proposal.asset_ids.filtered(lambda a: a.slot == "before" and a.datas)
    if assets:
        asset = assets[0]
        mime = asset.mime or "image/jpeg"
        raw = asset.datas
        if isinstance(raw, bytes):
            raw = raw.decode("ascii")
        return f"data:{mime};base64,{raw}"
    if proposal.before_image:
        raw = proposal.before_image
        if isinstance(raw, bytes):
            raw = raw.decode("ascii")
        return f"data:image/jpeg;base64,{raw}"
    return ""


def _bom_user_message(system_text, image_data_url):
    if not image_data_url:
        return {"role": "user", "content": system_text}
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": system_text},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ],
    }


def analyze_and_apply_pricing(proposal, materials_prompt=None):
    """Search suppliers + AI labor; keep source=manual lines. Returns summary dict."""
    proposal.ensure_one()
    model = resolve_price_model(proposal.env, proposal)
    if not model:
        raise UserError(
            proposal.env._("Configure an AI model for price assist (Configuration → AI models).")
        )
    lang_es = (proposal.language or "es") == "es"
    location = proposal.location or "United States"
    loc_info = _parse_location(location)

    content = dict(proposal.content or {})
    pricing = dict(content.get("pricing") or {})
    prompt_text = (materials_prompt or "").strip() or (pricing.get("materials_prompt") or "").strip()
    if not prompt_text:
        prompt_text = default_materials_prompt(proposal, lang_es)
    pricing["materials_prompt"] = prompt_text
    content["pricing"] = pricing
    proposal.write({"content": content})

    bom_prompt = _build_bom_system_prompt(lang_es, prompt_text)
    image_url = _before_image_data_url(proposal)
    raw = llm.chat_completion(
        model,
        [
            {"role": "system", "content": "Return valid JSON only."},
            _bom_user_message(bom_prompt, image_url),
        ],
        temperature=0.2,
        response_json=True,
        timeout=120,
    )
    bom = llm.parse_json_object(raw)
    materials = bom.get("materials") or []
    labor = bom.get("labor") or {}
    exclusions = bom.get("exclusions") or []

    suppliers = _provider_suppliers(proposal.env)
    supplier_ids = suppliers.ids
    Supplier = proposal.env["bizflow.supplier"]
    query_suffix = _location_query_suffix(location)

    line_vals = []
    material_notes = []
    for item in materials:
        query = (item.get("query") or item.get("name") or "").strip()
        qty = float(item.get("quantity") or 1.0)
        uom = item.get("uom") or "unit"
        hits = []
        search_q = f"{query}{query_suffix}".strip() if query else ""
        if search_q and supplier_ids:
            try:
                hits = Supplier.search_all_providers(
                    search_q, supplier_ids=supplier_ids, limit=8, location=location
                )
            except Exception:
                _logger.exception("Provider search failed for %s", search_q)
                hits = []

        pick_raw = llm.chat_completion(
            model,
            [
                {"role": "system", "content": "Return valid JSON only."},
                {
                    "role": "user",
                    "content": _pick_price_prompt(item, hits, location, lang_es),
                },
            ],
            temperature=0.1,
            response_json=True,
            timeout=90,
        )
        pick = llm.parse_json_object(pick_raw)
        price = float(pick.get("price") or 0.0)
        estimated = bool(pick.get("estimated")) or price <= 0
        if price <= 0:
            estimated = True
            price = max(float(item.get("fallback_price") or 0), 1.0)

        supplier = False
        sname = (pick.get("supplier_name") or "").lower()
        for s in suppliers:
            if s.name.lower() in sname or (s.code or "").lower() in sname:
                supplier = s
                break
        if not supplier and hits:
            hid = hits[0].get("supplier_id")
            if hid:
                supplier = proposal.env["bizflow.supplier"].browse(hid)

        note_bits = []
        if estimated:
            note_bits.append("estimado" if lang_es else "estimated")
            if loc_info["raw"]:
                note_bits.append(loc_info["raw"][:40])
        elif supplier:
            note_bits.append(supplier.name)
        if pick.get("reason"):
            note_bits.append(str(pick.get("reason"))[:120])
        if item.get("notes"):
            note_bits.append(str(item.get("notes"))[:80])

        line_vals.append(
            {
                "proposal_id": proposal.id,
                "name": pick.get("name") or item.get("name") or query or "Material",
                "line_type": "material",
                "quantity": qty,
                "uom_name": uom,
                "price_unit": price,
                "cost_unit": price,
                "supplier_id": supplier.id if supplier else False,
                # Never "manual" — manuals are user-owned and survive re-analyze.
                "source": "provider",
                "product_url": pick.get("url") or "",
                "notes": " · ".join(note_bits),
                "sequence": 10 + len(line_vals),
            }
        )
        material_notes.append(
            {
                "name": line_vals[-1]["name"],
                "price": price,
                "estimated": estimated,
                "supplier": supplier.name if supplier else "",
            }
        )

    labor_total = float(labor.get("total") or 0.0)
    hours = float(labor.get("hours") or 0.0)
    hourly = float(labor.get("hourly_rate") or 0.0)
    if hours > 0 and hourly > 0:
        labor_total = max(labor_total, hours * hourly)
    area_hint = 0.0
    for blob in (proposal.brief or "", proposal.description or "", proposal.work_brief or ""):
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|pies\s*cuadrados|ft2|ft²)", blob, re.I)
        if m:
            area_hint = max(area_hint, float(m.group(1)))
    if area_hint > 0:
        labor_total = max(labor_total, area_hint * 8.0)
        if hours <= 0:
            hours = max(area_hint / 10.0, 16.0)
            hourly = max(hourly, 70.0)
            labor_total = max(labor_total, hours * hourly)
    if labor_total <= 0:
        labor_total = max(hours * max(hourly, 65.0), 500.0)
    labor_name = labor.get("name") or (
        "Mano de obra — reparación de stucco" if lang_es else "Labor — stucco repair"
    )
    labor_notes = labor.get("rationale") or ""
    if "estimado" not in (labor_notes or "").lower() and "estimat" not in (labor_notes or "").lower():
        prefix = "estimado por ubicación" if lang_es else "location estimate"
        labor_notes = f"{prefix}: {labor_notes}".strip(": ")

    line_vals.append(
        {
            "proposal_id": proposal.id,
            "name": labor_name,
            "line_type": "labor",
            "quantity": hours if hours > 0 else 1.0,
            "uom_name": "hour" if hours > 0 else "job",
            "price_unit": hourly if hours > 0 else labor_total,
            "cost_unit": hourly if hours > 0 else labor_total,
            "source": "import",
            "notes": labor_notes[:200],
            "sequence": 100,
        }
    )

    # Keep user manuals; replace AI/provider/import material+labor lines.
    to_replace = proposal.line_ids.filtered(
        lambda l: l.line_type in ("material", "labor") and l.source != "manual"
    )
    to_replace.unlink()
    proposal.env["bizflow.proposal.line"].create(line_vals)

    content = dict(proposal.content or {})
    pricing = dict(content.get("pricing") or {})
    pricing["materials_prompt"] = prompt_text
    pricing["payments"] = pricing.get("payments") or [
        {
            "label": "Para empezar" if lang_es else "To start",
            "percent": 40,
            "detail": "Al firmar, antes de programar" if lang_es else "At signing, before work is scheduled",
        },
        {
            "label": "Punto medio" if lang_es else "Midpoint",
            "percent": 30,
            "detail": "Tras resanar, antes del stucco final"
            if lang_es
            else "Surface prepped, before final stucco",
        },
        {
            "label": "Al completar" if lang_es else "At completion",
            "percent": 30,
            "detail": "Trabajo terminado y walkthrough"
            if lang_es
            else "Work complete and walkthrough done",
        },
    ]
    pricing["notes"] = (
        ("Materiales vía proveedores activos; mano de obra estimada por ubicación. "
         if lang_es
         else "Materials via active suppliers; labor estimated by location. ")
        + (pricing.get("notes") or "")
    )
    content["pricing"] = pricing
    legal = dict(content.get("legal") or {})
    if exclusions:
        joined = ", ".join(str(x).strip().rstrip(".") for x in exclusions if str(x).strip())
        prefix = "No incluye: " if lang_es else "Not included: "
        legal["notes"] = f"{prefix}{joined}." if joined else ""
    content["legal"] = legal
    proposal.write({"content": content})
    proposal.invalidate_recordset()
    try:
        proposal.sync_utility_from_lines()
    except Exception:
        _logger.exception("sync_utility after pricing failed")

    return {
        "ok": True,
        "materials": material_notes,
        "labor": {
            "name": labor_name,
            "total": labor_total,
            "hours": hours,
            "hourly_rate": hourly,
            "notes": labor_notes,
        },
        "exclusions": exclusions,
        "amounts": {
            "materials": proposal.amount_materials,
            "labor": proposal.amount_labor,
            "total": proposal.amount_total,
        },
        "html": str(proposal.render_html()),
        "proposal": proposal.to_editor_payload(),
        "message": proposal.env._("Pricing analyzed; manual lines kept."),
    }
