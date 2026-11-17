# -*- coding: utf-8 -*-
"""AI price assist: exhaustive takeoff + HD/Lowes / referential shopping cascade."""
from __future__ import annotations

import logging
import re

from odoo.exceptions import UserError

from . import llm
from . import plan_render

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


def _proposal_has_site_assets(proposal):
    return bool(
        proposal.asset_ids.filtered(
            lambda a: (a.slot or "") in ("before", "plan")
            or (a.mime or "").startswith("application/pdf")
            or (a.filename or "").lower().endswith(".pdf")
        )
        or proposal.before_image
    )


def default_materials_prompt(proposal, lang_es):
    brief = proposal.brief or proposal.description or ""
    work = proposal.work_brief or ""
    location = proposal.location or ""
    project_type = proposal.project_type or ""
    has_plan = bool(
        proposal.asset_ids.filtered(
            lambda a: (a.slot or "") == "plan"
            or (a.mime or "").startswith("application/pdf")
            or (a.filename or "").lower().endswith(".pdf")
        )
    )
    plan_hint_es = (
        "Si hay plano/PDF adjunto, úsalo para cantidades, materiales, tiempos y presupuesto. "
        if has_plan
        else ""
    )
    plan_hint_en = (
        "If a plan/PDF is attached, use it for quantities, materials, timeline, and budget. "
        if has_plan
        else ""
    )
    if lang_es:
        return (
            "Analiza la(s) foto(s) del sitio y el plano (si hay) junto al brief para proponer "
            "materiales y mano de obra de construcción residencial en EE.UU. (NY/CT).\n"
            f"{plan_hint_es}"
            "Identifica el proyecto real del plano (p. ej. screened-in porch addition vs deck simple). "
            "Usa schedules, framing y detalles; no reduzcas el alcance.\n"
            f"Ubicación: {location}\nTipo: {project_type}\n"
            f"BRIEF:\n{brief}\n\nWORK:\n{work}\n"
            "Cantidades coherentes con medidas del brief/plano. "
            "Buscar precios en Home Depot / Lowe's cuando sea posible."
        )
    return (
        "Analyze site photo(s) and plan (if any) plus the brief to propose residential "
        "construction materials and labor in the US (NY/CT).\n"
        f"{plan_hint_en}"
        "Identify the real project from the plan (e.g. screened-in porch addition vs simple deck). "
        "Use schedules, framing, and details; do not under-scope.\n"
        f"Location: {location}\nProject type: {project_type}\n"
        f"BRIEF:\n{brief}\n\nWORK:\n{work}\n"
        "Quantities must match measurements in the brief/plan. "
        "Prefer Home Depot / Lowe's pricing when possible."
    )


def _build_bom_system_prompt(lang_es, user_prompt):
    rules = (
        "CRITICAL takeoff rules (derive EVERYTHING from the plan + brief + site photo — no preset budget): "
        "1) Identify the REAL project from the plan title/cover OR brief (e.g. Screened-In Porch "
        "Addition, chimney stucco repair). Do NOT invent a porch/deck takeoff when the brief is a "
        "repair/finish job. Do NOT reduce a porch/room addition to a tiny free-standing deck takeoff. "
        "2) If a plan PDF is present: read EVERY useful page (floor plans, elevations, schedules, "
        "foundation/framing, details). If only a site photo + brief: takeoff from measured areas and "
        "scope steps in the brief (sq ft, coats, prep). "
        "3) BOM must be EXHAUSTIVE for scope shown. For framed additions: PT ledger/joists/beams/posts, "
        "LVL/TJI, Sonotube/forms, concrete, Simpson hangers/bases/ties, fasteners, decking, ceiling, "
        "siding, trim, sheathing, flashing, screens/doors. For stucco/chimney repairs: stucco mix bags "
        "from area (~20 sq ft/bag +10% waste), bonding agent in gallons (NOT dozens of buckets), "
        "fiberglass mesh or lath, corner/casing bead, fasteners — separate lines. "
        "Respect EXCLUSIONS in the brief — put them in exclusions[] and NEVER include them as materials "
        "(e.g. paint, EPDM, plumbing, electrical, permits, brick crown if excluded). "
        "4) QUANTITY METHOD (mandatory): measure plan dimensions or use brief sq ft; count members at "
        "stated spacing; convert area to bags/LF/boards; add ~10% waste. "
        "FORBIDDEN: quantity=1 for lumber, stucco bags, mesh, hangers, decking, siding, trim, fasteners "
        "when the plan/brief shows repeated members or measurable areas. "
        "5) labor_items: split by trade. For EACH trade set hours = crew_size × work_days × hours_per_day "
        "(typical day 8h). Use Northeast trade hourly_rate $75–$95. Put crew math in rationale. "
        "Do NOT aim at any dollar budget — price emerges later from qty×retailer + hours×rate. "
        "6) query = English HD/Lowes search terms. Do NOT invent unit material prices."
    )
    schema = (
        "{"
        '"project_read":"short label of what the plan actually is",'
        '"materials":[{"name":"...","query":"english HD/Lowes search terms",'
        '"quantity":1.0,"uom":"each|bag|bucket|sq ft|lf|box","category":"lumber|hardware|concrete|...",'
        '"notes":"...","required":true}],'
        '"labor_items":[{"name":"...","trade":"framing|footings|finish|doors|...",'
        '"hours":0,"hourly_rate":0,"total":0,"rationale":"crew-days basis"}],'
        '"exclusions":["..."],'
        '"assumptions":["..."]'
        "}"
    )
    if lang_es:
        return (
            "Eres un estimator / takeoff de construcción residencial en EE.UU. (NY/CT/Northeast). "
            f"Devuelve SOLO JSON con esta forma:\n{schema}\n"
            "Incluye un BOM exhaustivo y realista con cantidades del plano. "
            f"{rules}\nIdioma de nombres visibles: español.\n"
            f"Instrucciones del usuario:\n{user_prompt}"
        )
    return (
        "You are a US residential construction estimator (NY/CT/Northeast) doing a full plan takeoff. "
        f"Return ONLY JSON:\n{schema}\n"
        "Include an exhaustive realistic BOM with quantities from the plan. "
        f"{rules}\n"
        f"User instructions:\n{user_prompt}"
    )


def _bom_user_message(system_text, image_data_urls):
    """Build multimodal user message from one or more image data URLs."""
    if isinstance(image_data_urls, str):
        image_data_urls = [image_data_urls] if image_data_urls else []
    image_data_urls = [u for u in (image_data_urls or []) if u]
    if not image_data_urls:
        return {"role": "user", "content": system_text}
    content = [{"type": "text", "text": system_text}]
    for url in image_data_urls[:8]:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": "user", "content": content}


def _normalize_labor_item(item, lang_es, area_hint=0.0):
    item = item or {}
    hours = float(item.get("hours") or 0.0)
    hourly = float(item.get("hourly_rate") or 0.0)
    total = float(item.get("total") or 0.0)
    rationale = item.get("rationale") or ""
    # Prefer explicit crew math when the model under-fills the hours field.
    computed = 0.0
    crew_math = re.search(
        r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)",
        rationale,
        re.I,
    )
    if crew_math:
        computed = (
            float(crew_math.group(1))
            * float(crew_math.group(2))
            * float(crew_math.group(3))
        )
    else:
        workers_days = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:workers?|crew)\s*(?:of\s*)?(?:for\s*)?(\d+(?:\.\d+)?)\s*(?:work\s*)?days?",
            rationale,
            re.I,
        )
        if not workers_days:
            workers_days = re.search(
                r"crew\s*(?:size\s*)?(?:of\s*)?(\d+(?:\.\d+)?)\s*(?:workers?\s*)?(?:for\s*)?(\d+(?:\.\d+)?)\s*(?:work\s*)?days?",
                rationale,
                re.I,
            )
        if workers_days:
            computed = float(workers_days.group(1)) * float(workers_days.group(2)) * 8.0
    if computed > hours:
        hours = computed
    if hours > 0 and hourly > 0:
        total = max(total, hours * hourly)
    blob = f"{item.get('name') or ''} {item.get('trade') or ''} {rationale}".lower()
    is_area_trade = any(
        k in blob for k in ("stucco", "plaster", "coating", "siding", "paint", "render")
    )
    if hours <= 0 and area_hint > 0:
        hours = max(area_hint / 12.0, 16.0)
        hourly = max(hourly, 75.0)
        total = max(total, hours * hourly)
    elif hours <= 0:
        hours = 8.0
        hourly = max(hourly, 75.0)
        total = max(total, hours * hourly)
    elif is_area_trade and area_hint > 0 and hours < max(area_hint / 15.0, 16.0):
        # Under-counted stucco/coating labor for measured area.
        hours = max(area_hint / 12.0, 16.0)
        hourly = max(hourly, 75.0)
        total = max(total, hours * hourly)
    if hourly <= 0:
        hourly = 75.0
    if hours <= 0:
        hours = 8.0
    if total <= 0:
        total = hours * hourly
    name = item.get("name") or (
        "Mano de obra" if lang_es else "Labor"
    )
    trade = (item.get("trade") or "").strip() or "general"
    return {
        "id": item.get("id") or f"labor-{trade}-{name}"[:40],
        "name": name,
        "trade": trade,
        "hours": round(hours, 2),
        "hourly_rate": round(hourly, 2),
        "total": round(total, 2),
        "uom": "hour",
        "rationale": rationale[:200],
        "included": True if item.get("included") is None else bool(item.get("included")),
    }


def _area_hint(proposal):
    """Largest sq-ft figure in brief / description / materials prompt."""
    area_hint = 0.0
    pricing = dict((proposal.content or {}).get("pricing") or {})
    blobs = (
        proposal.brief or "",
        proposal.description or "",
        proposal.work_brief or "",
        pricing.get("materials_prompt") or "",
    )
    pattern = re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|pies\s*cuadrados|ft2|ft²)",
        re.I,
    )
    for blob in blobs:
        for m in pattern.finditer(blob):
            area_hint = max(area_hint, float(m.group(1)))
    return area_hint


def _is_stucco_mix_item(item):
    """True for stucco mix / coats — not primers, bonders, mesh, or beads."""
    blob = f"{item.get('name') or ''} {item.get('query') or ''} {item.get('category') or ''}".lower()
    if any(
        k in blob
        for k in (
            "bonding",
            "bonder",
            "primer",
            "mesh",
            "lath",
            "bead",
            "tape",
            "fastener",
            "screw",
            "nail",
        )
    ):
        return False
    return any(
        k in blob
        for k in ("stucco mix", "stucco bag", "finish coat", "base coat", "scratch coat", "stucco")
    )


def _ensure_repair_bom_minimum(proposal, materials, lang_es):
    """Seed a realistic repair BOM when vision under-scopes photo+brief jobs."""
    materials = list(materials or [])
    blob = " ".join(
        [
            proposal.brief or "",
            proposal.description or "",
            proposal.work_brief or "",
            proposal.project_type or "",
            " ".join(
                f"{m.get('name') or ''} {m.get('query') or ''}" for m in materials
            ),
        ]
    ).lower()
    is_stucco_repair = any(k in blob for k in ("stucco", "estuco", "chimney", "chimenea"))
    if not is_stucco_repair:
        return materials
    area = _area_hint(proposal) or 250.0
    bags = max(round((area / 20.0) * 1.1, 2), 8.0)
    existing = " ".join(
        f"{m.get('name') or ''} {m.get('query') or ''}".lower() for m in materials
    )

    def _has(*keys):
        return any(k in existing for k in keys)

    seeds = []
    if not _has("stucco mix", "stucco bag", "finish coat", "scratch coat", "base coat"):
        seeds.append(
            {
                "name": "Mezcla de stucco" if lang_es else "Stucco Mix",
                "query": "Quikrete stucco mix bag",
                "quantity": bags,
                "uom": "bag",
                "category": "concrete",
                "notes": f"~20 sq ft/bag +10% for {area:g} sq ft",
                "required": True,
            }
        )
    if not _has("bonding", "bonder", "acrylic bonder"):
        seeds.append(
            {
                "name": "Agente adhesivo" if lang_es else "Concrete Bonding Agent",
                "query": "Quikrete concrete bonding adhesive gallon",
                "quantity": max(round(area / 200.0, 2), 1.0),
                "uom": "gallon",
                "category": "concrete",
                "notes": "Surface prep before recoat",
                "required": True,
            }
        )
    if not _has("mesh", "lath", "fiberglass"):
        seeds.append(
            {
                "name": "Malla de refuerzo" if lang_es else "Fiberglass Stucco Mesh",
                "query": "fiberglass stucco mesh roll",
                "quantity": max(round(area * 1.1, 2), area),
                "uom": "sq ft",
                "category": "hardware",
                "notes": "Reinforce repaired areas",
                "required": True,
            }
        )
    if not _has("corner bead", "casing bead", "control joint"):
        seeds.append(
            {
                "name": "Esquinero / bead" if lang_es else "Corner Bead",
                "query": "stucco corner bead",
                "quantity": max(round(area / 40.0, 2), 4.0),
                "uom": "lf",
                "category": "hardware",
                "notes": "Edges and corners",
                "required": True,
            }
        )
    if seeds:
        _logger.info(
            "repair BOM seed added %s lines for thin takeoff (%s existing)",
            len(seeds),
            len(materials),
        )
        materials.extend(seeds)
    # Cap absurd bonding-agent bucket counts that vision sometimes invents.
    for m in materials:
        name_blob = f"{m.get('name') or ''} {m.get('query') or ''}".lower()
        uom = (m.get("uom") or "").lower()
        if any(k in name_blob for k in ("bonding", "bonder")) and uom in (
            "bucket",
            "buckets",
            "bag",
            "bags",
        ):
            m["uom"] = "gallon"
            m["quantity"] = max(round(area / 200.0, 2), 1.0)
            m["query"] = m.get("query") or "concrete bonding adhesive gallon"
    return materials


def _exclusion_blob(proposal, bom=None):
    parts = [
        proposal.brief or "",
        proposal.work_brief or "",
        proposal.description or "",
    ]
    for ex in (bom or {}).get("exclusions") or []:
        parts.append(str(ex))
    return " ".join(parts).lower()


def _is_excluded_material(item, exclusion_blob):
    """Drop materials that the brief/plan exclusions already rule out."""
    blob = f"{item.get('name') or ''} {item.get('query') or ''} {item.get('category') or ''}".lower()
    checks = (
        (("epdm", "rubber membrane", "roofing membrane"), ("epdm", "membrane", "roofing membrane")),
        (("paint", "painting", "primer coat"), ("paint", "painting", "pintura")),
        (("plumbing",), ("plumbing", "plomer")),
        (("electrical", "wiring"), ("electrical", "electric", "wiring")),
        (("permit",), ("permit", "fee")),
    )
    for mat_keys, ex_keys in checks:
        if any(k in exclusion_blob for k in ex_keys) and any(k in blob for k in mat_keys):
            return True
    return False


def _strip_excluded_materials(proposal, bom, materials):
    blob = _exclusion_blob(proposal, bom)
    kept = []
    dropped = 0
    for m in materials or []:
        if _is_excluded_material(m, blob):
            dropped += 1
            continue
        kept.append(m)
    if dropped:
        _logger.info("stripped %s excluded material lines", dropped)
    return kept


def _placeholder_qty_count(materials):
    n = 0
    for m in materials or []:
        qty = float(m.get("quantity") or 0)
        uom = (m.get("uom") or "").lower()
        name = f"{m.get('name') or ''} {m.get('query') or ''}".lower()
        if qty <= 1 and uom in ("unit", "each", "ea", ""):
            if any(
                k in name
                for k in (
                    "joist",
                    "beam",
                    "post",
                    "ledger",
                    "lumber",
                    "2x",
                    "6x6",
                    "concrete",
                    "hanger",
                    "simpson",
                    "decking",
                    "siding",
                    "trim",
                    "fascia",
                    "ceiling",
                    "sheathing",
                    "sonotube",
                    "footing",
                    "fastener",
                    "screw",
                    "nail",
                    "flashing",
                    "blocking",
                )
            ):
                n += 1
    return n


def _is_structural_placeholder(item):
    qty = float(item.get("quantity") or 0)
    if qty > 1:
        return False
    name = f"{item.get('name') or ''} {item.get('query') or ''} {item.get('category') or ''}".lower()
    return any(
        k in name
        for k in (
            "joist",
            "beam",
            "post",
            "ledger",
            "lumber",
            "2x",
            "6x6",
            "concrete",
            "hanger",
            "simpson",
            "decking",
            "siding",
            "trim",
            "fascia",
            "ceiling",
            "sheathing",
            "sonotube",
            "footing",
            "fastener",
            "screw",
            "nail",
            "flashing",
            "blocking",
            "lvl",
            "tji",
            "screen",
        )
    )


def _merge_material_rows(materials, extra):
    existing = " ".join(
        f"{m.get('name')} {m.get('query')} {m.get('category')}" for m in materials
    ).lower()
    added = 0
    for m in extra or []:
        q = (m.get("query") or m.get("name") or "").lower()
        qty = float(m.get("quantity") or 0)
        uom = (m.get("uom") or "").lower()
        if qty <= 1 and uom in ("unit", "each", "ea", "bags", "bag", "sheets", "lf", "sq ft", ""):
            if _is_structural_placeholder(m) or len(q) < 28:
                continue
        if q and q[:20] not in existing:
            materials.append(m)
            existing += " " + q
            added += 1
    return added


def _apply_material_corrections(materials, extra):
    """Merge extra rows; bump quantities when the model corrects under-counts."""
    by_key = {}
    for m in materials:
        key = (m.get("query") or m.get("name") or "").lower()[:40]
        by_key[key] = m
    replaced = 0
    for m in extra or []:
        key = (m.get("query") or m.get("name") or "").lower()[:40]
        new_qty = float(m.get("quantity") or 0)
        if not key or new_qty <= 0:
            continue
        if key in by_key and new_qty > float(by_key[key].get("quantity") or 0):
            by_key[key]["quantity"] = new_qty
            if m.get("uom"):
                by_key[key]["uom"] = m.get("uom")
            if m.get("query"):
                by_key[key]["query"] = m.get("query")
            replaced += 1
    materials = list(by_key.values())
    added = _merge_material_rows(materials, extra)
    return materials, added, replaced


def _expand_bom_from_plan_if_thin(proposal, bom, materials, model, lang_es, image_urls):
    """Second vision pass: missing plan items + fix placeholder quantities — no seeded qty."""
    materials = list(materials or [])
    if not model or not getattr(model, "api_key", None):
        return materials
    if not image_urls:
        return materials
    thin = len(materials) < 28 or _placeholder_qty_count(materials) >= 2
    if not thin:
        return materials
    project = (bom or {}).get("project_read") or proposal.project_type or "construction"
    try:
        expand_prompt = (
            f"The takeoff for '{project}' is incomplete ({len(materials)} lines). "
            "Re-read EVERY attached plan page (schedules, framing, foundation, details). "
            "Return ONLY JSON {\"materials\":[...]} — a FULL corrected materials list "
            "(you may include corrected existing lines AND missing ones). "
            "Compute quantities from plan dimensions, joist/post spacing, footing counts, "
            "door/material schedules (+~10% waste). "
            "REQUIRED categories when shown on plan: Sonotube/forms, concrete, PT ledger, "
            "PT joists (count each), PT beams, PT posts, LVL/TJI if labeled, Simpson hangers/"
            "post bases/straps, thru-bolts/screws, decking species from schedule (LF or boards), "
            "ceiling wood, siding, trim/fascia, sheathing, flashing, bi-fold/screen doors, "
            "screen mesh/panels. "
            "NEVER use quantity=1 for repeated lumber/hardware/concrete. "
            "Omit brief exclusions. No preset dollar budget. "
            f"Current incomplete lines: {[{'name': m.get('name'), 'query': m.get('query'), 'qty': m.get('quantity'), 'uom': m.get('uom')} for m in materials[:45]]}"
        )
        if lang_es:
            expand_prompt += "\nVisible names in Spanish; query in English for retailers."
        raw = llm.chat_completion(
            model,
            [
                {"role": "system", "content": "Return valid JSON only."},
                _bom_user_message(expand_prompt, (image_urls or [])[:6]),
            ],
            temperature=0.15,
            response_json=True,
            timeout=240,
        )
        extra = (llm.parse_json_object(raw) or {}).get("materials") or []
        old_ph = _placeholder_qty_count(materials)
        new_ph = _placeholder_qty_count(extra)
        # Only take a full replacement when it is clearly richer AND not placeholder-heavy.
        use_full = (
            len(extra) >= max(len(materials) + 3, 18)
            and new_ph <= max(2, old_ph)
            and new_ph < max(6, len(extra) // 3)
        )
        if use_full:
            materials = list(extra)
            _logger.info(
                "plan BOM expand replaced full list (%s lines, placeholders=%s)",
                len(materials),
                new_ph,
            )
        else:
            materials, added, replaced = _apply_material_corrections(materials, extra)
            _logger.info(
                "plan BOM expand merge added=%s replaced_qty=%s placeholders %s→%s (now %s)",
                added,
                replaced,
                old_ph,
                _placeholder_qty_count(materials),
                len(materials),
            )
    except Exception:
        _logger.exception("plan BOM expand pass failed")
    return materials


def _fix_quantities_from_plan(proposal, bom, materials, model, lang_es, image_urls):
    """Ask the model only for quantity corrections from plan measurements."""
    materials = list(materials or [])
    if not materials or not model or not image_urls:
        return materials
    if _placeholder_qty_count(materials) < 2 and len(materials) >= 16:
        return materials
    try:
        prompt = (
            "From the plan images, CORRECT the quantities below. Return ONLY JSON "
            '{"materials":[{"name","query","quantity","uom","notes"}]}. '
            "For each line, set quantity from measured plan dimensions / member spacing / "
            "schedule counts (+~10% waste). Example: joists = floor length / spacing + 1; "
            "decking LF = area / board width; concrete bags from footing volume; "
            "hangers ≈ joist count. Do NOT leave structural items at quantity 1. "
            "You may add missing structural lines with real counts. No dollar budget. "
            f"Lines: {[{'name': m.get('name'), 'query': m.get('query'), 'qty': m.get('quantity'), 'uom': m.get('uom')} for m in materials[:40]]}"
        )
        if lang_es:
            prompt += "\nKeep Spanish names; English query."
        raw = llm.chat_completion(
            model,
            [
                {"role": "system", "content": "Return valid JSON only."},
                _bom_user_message(prompt, (image_urls or [])[:5]),
            ],
            temperature=0.1,
            response_json=True,
            timeout=180,
        )
        extra = (llm.parse_json_object(raw) or {}).get("materials") or []
        materials, added, replaced = _apply_material_corrections(materials, extra)
        _logger.info(
            "qty fix added=%s replaced=%s placeholders=%s",
            added,
            replaced,
            _placeholder_qty_count(materials),
        )
    except Exception:
        _logger.exception("quantity fix pass failed")
    return materials


def _refine_labor_from_plan(proposal, bom, labor_items, model, lang_es, image_urls):
    """If labor hours are implausibly low for a framed addition, re-estimate from plan scope."""
    items = list(labor_items or [])
    total_hours = sum(float(x.get("hours") or 0) for x in items)
    blob = " ".join(
        [
            proposal.brief or "",
            proposal.work_brief or "",
            proposal.project_type or "",
            (bom or {}).get("project_read") or "",
        ]
    ).lower()
    is_addition = any(
        k in blob
        for k in ("porch", "addition", "screened", "deck", "framing", "finish")
    )
    # Framed outdoor additions are multi-week; <240h (~3 crew-weeks × small crew)
    # is almost always under-scoped for porch/deck additions with footings+finish.
    if not is_addition or total_hours >= 240 or not model or not image_urls:
        return items
    try:
        prompt = (
            "Re-estimate labor_items for this project from the plan pages + brief. "
            "Return ONLY JSON {\"labor_items\":[{\"name\",\"trade\",\"hours\",\"hourly_rate\",\"rationale\"}]}. "
            "Split trades: footings/foundation, framing, roof/sheathing, exterior finish "
            "(siding/trim/decking/ceiling), doors/screens. "
            "hours = crew_size × work_days × 8. A full screened porch / framed addition "
            "with footings + structure + finish is typically several crew-weeks, not a few days. "
            "Northeast hourly_rate $75–$95. Put crew math in rationale. No dollar budget target. "
            f"project_read: {(bom or {}).get('project_read')}; "
            f"materials_count: {len((bom or {}).get('materials') or [])}; "
            f"prior_labor: {[{'trade': i.get('trade'), 'hours': i.get('hours')} for i in items]}"
        )
        if lang_es:
            prompt += "\nVisible labor names in Spanish."
        raw = llm.chat_completion(
            model,
            [
                {"role": "system", "content": "Return valid JSON only."},
                _bom_user_message(prompt, (image_urls or [])[:4]),
            ],
            temperature=0.2,
            response_json=True,
            timeout=120,
        )
        refined = (llm.parse_json_object(raw) or {}).get("labor_items") or []
        if refined:
            area = _area_hint(proposal)
            new_items = [_normalize_labor_item(x, lang_es, area) for x in refined]
            new_hours = sum(float(x["hours"]) for x in new_items)
            if new_hours > total_hours:
                _logger.info(
                    "labor refined from plan: %.0fh → %.0fh (%s trades)",
                    total_hours,
                    new_hours,
                    len(new_items),
                )
                return new_items
    except Exception:
        _logger.exception("labor refine pass failed")
    return items


def _normalize_labor_items(proposal, bom, lang_es):
    """Prefer labor_items[]; fall back to legacy single labor object."""
    area = _area_hint(proposal)
    raw_items = bom.get("labor_items")
    if isinstance(raw_items, list) and raw_items:
        items = [_normalize_labor_item(x, lang_es, area) for x in raw_items]
    else:
        legacy = bom.get("labor") or {}
        items = [_normalize_labor_item(legacy, lang_es, area)] if legacy or True else []
        if not legacy and area > 0:
            items = [
                _normalize_labor_item(
                    {
                        "name": "Mano de obra" if lang_es else "Labor",
                        "trade": "general",
                        "hours": max(area / 10.0, 16.0),
                        "hourly_rate": 75.0,
                    },
                    lang_es,
                    area,
                )
            ]
    for i, it in enumerate(items):
        it["id"] = f"labor-{i}"
    # Aggregate for backward-compatible single labor card.
    hours = sum(float(x["hours"]) for x in items)
    total = sum(float(x["total"]) for x in items)
    hourly = (total / hours) if hours > 0 else 75.0
    labor = {
        "name": "Mano de obra" if lang_es else "Labor",
        "hours": round(hours, 2),
        "hourly_rate": round(hourly, 2),
        "total": round(total, 2),
        "uom": "hour",
        "rationale": "; ".join(
            f"{x.get('trade')}: {x.get('rationale')}" for x in items if x.get("rationale")
        )[:200],
        "included": True,
    }
    return items, labor


def _price_plausible_for_uom(name, uom, price):
    """Reject SerpAPI mismatches that blow up the budget (wrong unit / product)."""
    if price <= 0:
        return False
    u = (uom or "").lower().strip()
    n = (name or "").lower()
    # Linear foot: reject absurd highs and near-zero junk matches
    if u in ("lf", "lin ft", "linear ft", "linear foot"):
        if any(k in n for k in ("rail", "baluster", "guard")) and (price > 45 or price < 1.5):
            return False
        if any(k in n for k in ("siding", "clapboard", "shiplap")) and (price > 14 or price < 0.8):
            return False
        if any(k in n for k in ("decking", "mahogany", "ceiling", "t&g", "tongue")) and (
            price > 25 or price < 2.5
        ):
            return False
        if price > 90 or price < 0.5:
            return False
    # Area caps / floors
    if u in ("sq ft", "sqft", "sf"):
        if price > 18 or price < 1.5:
            return False
    if u in ("bag", "bags") and (price > 30 or price < 3):
        return False
    if u in ("box",) and (price > 80 or price < 5):
        return False
    if u in ("each", "ea", "unit"):
        if any(k in n for k in ("hanger", "tie", "strap", "post base")) and (
            price > 45 or price < 1.5
        ):
            return False
        if any(k in n for k in ("sonotube", "quik-tube", "quiktube")) and (
            price > 60 or price < 8
        ):
            return False
        if "concrete" in n and "footing" in n and (price > 80 or price < 5):
            return False
        if any(k in n for k in ("screen door", "screen doors")) and (
            price > 450 or price < 40
        ):
            return False
        # Bi-fold patio doors can be expensive — allow up to $4k each.
        if any(k in n for k in ("bi-fold", "bifold", "patio door", "marvin")) and (
            price > 4500 or price < 400
        ):
            return False
        if "lvl" in n and (price > 400 or price < 40):
            return False
        if "tji" in n and (price > 200 or price < 20):
            return False
        if any(k in n for k in ("composite", "trex", "deck board")) and (
            price > 120 or price < 15
        ):
            return False
        if any(k in n for k in ("6x6", "post")) and "treated" in n and (
            price > 120 or price < 15
        ):
            return False
    if u in ("cu yd", "cubic yard") and price < 50:
        # Likely matched a bag price labeled as cu yd.
        return False
    return True


def _options_from_hits(hits, fallback_name, estimated=False, source="verified", uom=""):
    """Build options from hits with price>0 + http URL. No invented prices."""
    options = []
    for h in hits or []:
        price = float(h.get("price") or 0.0)
        url = (h.get("url") or "").strip()
        if price <= 0 or not (url.startswith("http://") or url.startswith("https://")):
            continue
        hit_name = h.get("name") or fallback_name
        if not _price_plausible_for_uom(f"{fallback_name} {hit_name}", uom, price):
            continue
        options.append(
            {
                "key": f"hit-{h.get('supplier_id') or 'ref'}-{len(options)}",
                "supplier_id": h.get("supplier_id") or False,
                "supplier_name": h.get("supplier_name") or (
                    "Referencial" if estimated else ""
                ),
                "name": hit_name,
                "price": price,
                "url": url,
                "estimated": bool(estimated or h.get("estimated")),
                "source": h.get("source") or source,
                "recommended": False,
            }
        )
    if not options:
        return [], ""
    ranked = sorted(options, key=lambda o: (float(o["price"]), o.get("name") or ""))
    for i, opt in enumerate(ranked):
        opt["key"] = f"hit-{opt.get('supplier_id') or 'ref'}-{i}"
        opt["recommended"] = i == 0
    return ranked, ranked[0]["key"]


def _search_referential_shopping(env, query, limit=4):
    """Google Shopping without retailer filter — referential prices (1C step 2)."""
    Supplier = env["bizflow.supplier"]
    sample = Supplier.search([("active", "=", True)], limit=1)
    if not sample:
        return []
    try:
        return sample.search_referential_shopping(query, limit=limit)
    except Exception:
        _logger.exception("Referential shopping failed for %s", query)
        return []


def _sanitize_material_quantity(item, area_hint):
    """Bump under-counted area materials using trade yields (e.g. stucco bags)."""
    qty = float(item.get("quantity") or 1.0)
    if qty <= 0:
        qty = 1.0
    if area_hint <= 0:
        return qty
    uom = (item.get("uom") or "").lower()
    # Stucco mix only — never apply bag yield to bonding agents / primers.
    if _is_stucco_mix_item(item) and uom in ("bag", "bags", "sack", "sacks"):
        needed = max(qty, (area_hint / 20.0) * 1.1)
        if needed > qty * 1.05:
            _logger.info(
                "takeoff qty bump %s: %s → %s for area %s",
                item.get("name"),
                qty,
                round(needed, 2),
                area_hint,
            )
            qty = needed
    return round(qty, 2)


def _price_candidate(env, suppliers, item, existing_urls, idx, area_hint=0.0):
    """1C cascade: HD/Lowes → shopping referential → visible qty without price."""
    query = (item.get("query") or item.get("name") or "").strip()
    qty = _sanitize_material_quantity(item, area_hint)
    uom = item.get("uom") or "unit"
    name = item.get("name") or query or "Material"
    hits = []
    if query and suppliers:
        try:
            hits = env["bizflow.supplier"].search_all_providers(
                query, supplier_ids=suppliers.ids, limit=8, location=None
            )
        except Exception:
            _logger.exception("Provider search failed for %s", query)
            hits = []

    options, selected_key = _options_from_hits(
        hits, name, estimated=False, source="verified", uom=uom
    )
    options = [o for o in options if (o.get("url") or "").strip() not in existing_urls]

    if not options and query:
        ref_hits = _search_referential_shopping(env, query, limit=4)
        options, selected_key = _options_from_hits(
            ref_hits, name, estimated=True, source="referential", uom=uom
        )
        options = [o for o in options if (o.get("url") or "").strip() not in existing_urls]

    if options and not any(o.get("key") == selected_key for o in options):
        selected_key = options[0]["key"]
        options[0]["recommended"] = True
    elif not options:
        # Qty-only fallback so the editor can still add the BOM line to the report.
        selected_key = f"qty-{idx}"
        options = [
            {
                "key": selected_key,
                "supplier_id": False,
                "supplier_name": "",
                "name": name,
                "price": 0.0,
                "url": "",
                "estimated": True,
                "source": "qty",
                "recommended": True,
            }
        ]

    return {
        "id": f"mat-{idx}",
        "name": name,
        "query": query,
        "quantity": qty,
        "uom": uom,
        "category": item.get("category") or "",
        "notes": item.get("notes") or "",
        "required": bool(item.get("required", True)),
        "options": options,
        "selected_key": selected_key,
        "unverified": any(
            (o.get("source") == "qty") or (not float(o.get("price") or 0))
            for o in options
        )
        and not any(
            float(o.get("price") or 0) > 0
            and (o.get("url") or "").startswith(("http://", "https://"))
            for o in options
        ),
    }


def _persist_last_review(proposal, review_payload):
    content = dict(proposal.content or {})
    pricing = dict(content.get("pricing") or {})
    pricing["last_review"] = {
        "candidates": review_payload.get("candidates") or [],
        "labor": review_payload.get("labor") or {},
        "labor_items": review_payload.get("labor_items") or [],
        "exclusions": review_payload.get("exclusions") or [],
        "assumptions": review_payload.get("assumptions") or [],
        "totals": review_payload.get("totals") or {},
    }
    if review_payload.get("materials_prompt"):
        pricing["materials_prompt"] = review_payload["materials_prompt"]
    content["pricing"] = pricing
    proposal.write({"content": content})


def _preview_totals(candidates, labor_items):
    mat_total = 0.0
    for c in candidates or []:
        opts = c.get("options") or []
        key = c.get("selected_key")
        chosen = next((o for o in opts if o.get("key") == key), None)
        if not chosen and opts:
            chosen = opts[0]
        if chosen:
            mat_total += float(chosen.get("price") or 0) * float(c.get("quantity") or 1)
    labor_total = sum(float(x.get("total") or 0) for x in (labor_items or []))
    return {
        "materials": round(mat_total, 2),
        "labor": round(labor_total, 2),
        "grand": round(mat_total + labor_total, 2),
    }


def analyze_pricing_preview(proposal, materials_prompt=None):
    """BOM + supplier/referential hits; return candidates for user confirmation (no line writes)."""
    proposal.ensure_one()
    model = resolve_price_model(proposal.env, proposal)
    if not model:
        raise UserError(
            proposal.env._("Configure an AI model for price assist (Configuration → AI models).")
        )
    if not model.api_key:
        raise UserError(
            proposal.env._("Add an API key on the price-assist AI model.")
        )
    lang_es = (proposal.language or "es") == "es"
    location = proposal.location or "United States"
    del location  # reserved for future geo pricing

    content = dict(proposal.content or {})
    pricing = dict(content.get("pricing") or {})
    prompt_text = (materials_prompt or "").strip() or (pricing.get("materials_prompt") or "").strip()
    if not prompt_text:
        prompt_text = default_materials_prompt(proposal, lang_es)
    pricing["materials_prompt"] = prompt_text
    content["pricing"] = pricing
    proposal.write({"content": content})

    bom_prompt = _build_bom_system_prompt(lang_es, prompt_text)
    image_urls = plan_render.vision_data_urls_for_proposal(proposal, max_images=8)
    _logger.info("vision BOM images=%s", len(image_urls))
    raw = None
    vision_attempts = []
    if image_urls:
        vision_attempts = [image_urls[:8], image_urls[:5], image_urls[:3]]
    last_err = None
    for attempt_urls in vision_attempts:
        try:
            raw = llm.chat_completion(
                model,
                [
                    {"role": "system", "content": "Return valid JSON only."},
                    _bom_user_message(bom_prompt, attempt_urls),
                ],
                temperature=0.2,
                response_json=True,
                timeout=300,
            )
            last_err = None
            break
        except UserError as exc:
            last_err = exc
            _logger.warning(
                "Vision BOM failed with %s images: %s", len(attempt_urls), exc
            )
    if raw is None and not image_urls:
        raise last_err or UserError(
            proposal.env._("No plan images available for takeoff.")
        )
    if raw is None:
        # Last resort — text only (quantities will be weaker without the plan).
        _logger.error("All vision BOM attempts failed; falling back to text-only")
        raw = llm.chat_completion(
            model,
            [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": bom_prompt},
            ],
            temperature=0.2,
            response_json=True,
            timeout=180,
        )
    try:
        bom = llm.parse_json_object(raw)
    except Exception:
        _logger.warning("BOM JSON parse failed; retrying vision with 4 pages")
        retry_urls = (image_urls or [])[:4]
        raw = llm.chat_completion(
            model,
            [
                {"role": "system", "content": "Return valid JSON only. No markdown."},
                _bom_user_message(bom_prompt, retry_urls) if retry_urls else {"role": "user", "content": bom_prompt},
            ],
            temperature=0.1,
            response_json=True,
            timeout=300,
        )
        bom = llm.parse_json_object(raw)
    materials = bom.get("materials") or []
    materials = _strip_excluded_materials(proposal, bom, materials)
    materials = _expand_bom_from_plan_if_thin(
        proposal, bom, materials, model, lang_es, image_urls
    )
    materials = _fix_quantities_from_plan(
        proposal, bom, materials, model, lang_es, image_urls
    )
    materials = _strip_excluded_materials(proposal, bom, materials)
    # Photo+brief repairs (chimney/stucco) often under-scope without a plan PDF.
    if len(materials) < 6:
        materials = _ensure_repair_bom_minimum(proposal, materials, lang_es)
        materials = _strip_excluded_materials(proposal, bom, materials)
    bom["materials"] = materials
    labor_items, labor = _normalize_labor_items(proposal, bom, lang_es)
    labor_items = _refine_labor_from_plan(
        proposal, bom, labor_items, model, lang_es, image_urls
    )
    labor_items, labor = _normalize_labor_items(
        proposal, {**bom, "labor_items": labor_items}, lang_es
    )
    exclusions = bom.get("exclusions") or []
    assumptions = bom.get("assumptions") or []

    suppliers = _provider_suppliers(proposal.env)
    existing_urls = {
        (l.product_url or "").strip()
        for l in proposal.line_ids
        if (l.product_url or "").strip()
    }

    area = _area_hint(proposal)
    candidates = []
    for idx, item in enumerate(materials):
        candidates.append(
            _price_candidate(
                proposal.env, suppliers, item, existing_urls, idx, area_hint=area
            )
        )

    totals = _preview_totals(candidates, labor_items)
    payload = {
        "ok": True,
        "preview": True,
        "candidates": candidates,
        "labor": labor,
        "labor_items": labor_items,
        "exclusions": exclusions,
        "assumptions": assumptions,
        "totals": totals,
        "materials_prompt": prompt_text,
        "message": proposal.env._(
            "Recommended options are pre-selected. Add each item (or apply all) to lines."
        ),
    }
    _persist_last_review(proposal, payload)
    return payload


def apply_pricing_selection(proposal, candidates=None, labor=None, labor_items=None, exclusions=None, materials_prompt=None):
    """Apply user-confirmed pricing selections; keep source=manual lines."""
    proposal.ensure_one()
    lang_es = (proposal.language or "es") == "es"
    candidates = candidates or []
    exclusions = exclusions or []

    # Prefer explicit labor_items; else wrap legacy labor.
    if labor_items is None:
        if labor:
            labor_items = [_normalize_labor_item(labor, lang_es)]
        else:
            labor_items = []
    else:
        labor_items = [_normalize_labor_item(x, lang_es) for x in labor_items]
    if labor is None and labor_items:
        hours = sum(float(x["hours"]) for x in labor_items)
        total = sum(float(x["total"]) for x in labor_items)
        labor = {
            "name": "Mano de obra" if lang_es else "Labor",
            "hours": hours,
            "hourly_rate": (total / hours) if hours else 0,
            "total": total,
            "included": True,
            "uom": "hour",
        }
    labor = labor or {}

    existing_urls = {
        (l.product_url or "").strip()
        for l in proposal.line_ids
        if (l.product_url or "").strip()
    }
    has_labor = any(l.line_type == "labor" for l in proposal.line_ids)
    next_seq = max([l.sequence for l in proposal.line_ids] or [0]) + 10

    line_vals = []
    for item in candidates:
        options = item.get("options") or []
        selected_key = item.get("selected_key")
        chosen = next((o for o in options if o.get("key") == selected_key), None)
        if not chosen and options:
            chosen = next((o for o in options if o.get("recommended")), None) or options[0]
        # Qty-only candidates (no retailer hit) still belong on the report.
        if not chosen:
            name = (item.get("name") or item.get("query") or "").strip()
            if not name:
                continue
            line_vals.append(
                {
                    "proposal_id": proposal.id,
                    "name": name,
                    "line_type": "material",
                    "quantity": float(item.get("quantity") or 1.0),
                    "uom_name": item.get("uom") or "unit",
                    "price_unit": 0.0,
                    "cost_unit": 0.0,
                    "supplier_id": False,
                    "source": "import",
                    "product_url": "",
                    "notes": "qty-only",
                    "sequence": next_seq,
                }
            )
            next_seq += 10
            continue
        price = float(chosen.get("price") or 0.0)
        url = (chosen.get("url") or "").strip()
        is_qty_only = (
            chosen.get("source") == "qty"
            or price <= 0
            or not (url.startswith("http://") or url.startswith("https://"))
        )
        if url and url in existing_urls:
            continue
        if url:
            existing_urls.add(url)
        if is_qty_only:
            source = "import"
            notes = "qty-only"
            price = max(price, 0.0)
            url = ""
        elif chosen.get("estimated") or chosen.get("source") == "referential":
            source = "import"
            notes = "referential"
        else:
            source = "provider"
            notes = ""
        line_vals.append(
            {
                "proposal_id": proposal.id,
                "name": chosen.get("name") or item.get("name") or "Material",
                "line_type": "material",
                "quantity": float(item.get("quantity") or 1.0),
                "uom_name": item.get("uom") or "unit",
                "price_unit": price,
                "cost_unit": price,
                "supplier_id": chosen.get("supplier_id") or False,
                "source": source,
                "product_url": url,
                "notes": notes,
                "sequence": next_seq,
            }
        )
        next_seq += 10

    include_labor = labor.get("included", True)
    if include_labor and not has_labor:
        for li in labor_items:
            if not li.get("included", True):
                continue
            if float(li.get("total") or li.get("hourly_rate") or 0) <= 0:
                continue
            line_vals.append(
                {
                    "proposal_id": proposal.id,
                    "name": li.get("name") or "Labor",
                    "line_type": "labor",
                    "quantity": float(li.get("hours") or 1.0),
                    "uom_name": li.get("uom") or "hour",
                    "price_unit": float(li.get("hourly_rate") or li.get("total") or 0.0),
                    "cost_unit": float(li.get("hourly_rate") or li.get("total") or 0.0),
                    "source": "import",
                    "notes": li.get("trade") or "",
                    "sequence": next_seq,
                }
            )
            next_seq += 10

    if line_vals:
        proposal.env["bizflow.proposal.line"].create(line_vals)
    content = dict(proposal.content or {})
    pricing = dict(content.get("pricing") or {})
    if materials_prompt:
        pricing["materials_prompt"] = materials_prompt
    pricing["last_review"] = False
    pricing["payments"] = pricing.get("payments") or [
        {
            "label": "Para empezar" if lang_es else "To start",
            "percent": 50,
            "detail": "Al firmar" if lang_es else "At signing",
        },
        {
            "label": "Al completar" if lang_es else "At completion",
            "percent": 50,
            "detail": "Trabajo terminado" if lang_es else "Work complete",
        },
    ]
    content["pricing"] = pricing
    legal = dict(content.get("legal") or {})
    if exclusions:
        joined = ", ".join(str(x).strip().rstrip(".") for x in exclusions if str(x).strip())
        prefix = "No incluye: " if lang_es else "Not included: "
        legal["notes"] = f"{prefix}{joined}." if joined else legal.get("notes") or ""
    content["legal"] = legal
    proposal.write({"content": content})
    proposal.invalidate_recordset()
    try:
        proposal.sync_utility_from_lines()
    except Exception:
        _logger.exception("sync_utility after pricing apply failed")

    return {
        "ok": True,
        "preview": False,
        "proposal": proposal.to_editor_payload(),
        "html": str(proposal.render_html()),
        "message": proposal.env._("Pricing applied; manual lines kept."),
    }


def analyze_and_apply_pricing(
    proposal,
    materials_prompt=None,
    apply=False,
    candidates=None,
    labor=None,
    labor_items=None,
    exclusions=None,
):
    """Backward-compatible entry: default is preview; apply=True writes lines."""
    if apply or candidates is not None:
        return apply_pricing_selection(
            proposal,
            candidates=candidates,
            labor=labor,
            labor_items=labor_items,
            exclusions=exclusions,
            materials_prompt=materials_prompt,
        )
    return analyze_pricing_preview(proposal, materials_prompt=materials_prompt)


def run_pricing_preview_after_generate(proposal):
    """2A helper: after Write with AI, build Materials review when site assets exist."""
    proposal.ensure_one()
    if not _proposal_has_site_assets(proposal):
        return None
    try:
        return analyze_pricing_preview(proposal)
    except Exception:
        _logger.exception("Auto pricing preview after generate failed for proposal %s", proposal.id)
        return None
