# -*- coding: utf-8 -*-
import json
import logging
import re

from odoo.addons.bizflow_proposals.models.schema import SECTION_ORDER, normalize_content
from odoo.addons.bizflow_proposals.models.services import llm

_logger = logging.getLogger(__name__)

GENERATE_SECTIONS = [
    "cover",
    "description",
    "before_after",
    "scope",
    "materials",
    "timeline",
    "pricing",
    "utility",
    "legal",
]


def _brief_text(proposal):
    parts = [
        f"Language: {proposal.language or 'es'}",
        f"Client: {proposal.partner_id.display_name or proposal.name}",
        f"Project type: {proposal.project_type or ''}",
        f"Location: {proposal.location or ''}",
        f"WHAT THE PROJECT IS ABOUT:\n{proposal.brief or proposal.description or ''}",
        f"WHAT MUST BE BUILT / SCOPE NOTES:\n{proposal.work_brief or ''}",
    ]
    return "\n\n".join(parts)


def _system_prompt(language):
    lang = "Spanish" if language == "es" else "English"
    return (
        f"You write construction proposals for contractors in {lang}. "
        "Return a single JSON object with keys: cover, description, before_after, scope, "
        "materials, timeline, pricing, utility, legal. "
        "Use clear non-technical client language. "
        "Extract measurements from the brief when present (ft, m, sqft, etc.) and use them "
        "in description, scope, materials and pricing notes. "
        "Do not invent suppliers or prices you were not given; leave numeric totals as 0 if unknown. "
        "cover: {title, subtitle, tagline}. "
        "description: {intro, location, project_type, problem, solution, highlights[]}. "
        "before_after: {intro, notes, after_prompt}. "
        "scope: {intro, modules[{title, description}], deliverables[]}. "
        "materials: {intro, notes}. "
        "timeline: {intro, phases[{name, weeks, detail}], tasks[]}. "
        "pricing: {intro, notes, markup_percent, contingency_percent, payments[{label, percent}]}. "
        "utility: {intro, assumptions[], notes}. "
        "legal: {terms_md, responsibilities_md, notes}."
    )


def _fallback_from_brief(proposal):
    """Deterministic draft when no AI key is configured — still fills every section."""
    lang_es = (proposal.language or "es") == "es"
    brief = (proposal.brief or proposal.description or "").strip()
    work = (proposal.work_brief or "").strip()
    client = proposal.partner_id.display_name or proposal.name
    title = proposal.name or (f"Propuesta — {client}" if lang_es else f"Proposal — {client}")
    measures = _extract_measures(brief + "\n" + work)
    measure_note = (
        (", ".join(measures) if measures else "")
        or (lang_es and "según medidas del brief" or "per brief measurements")
    )
    intro = brief or (lang_es and "Proyecto de construcción descrito por el cliente." or "Construction project described by the client.")
    return {
        "cover": {
            "title": title,
            "subtitle": proposal.project_type or (lang_es and "Remodelación" or "Remodel"),
            "tagline": proposal.location or "",
        },
        "description": {
            "intro": intro,
            "location": proposal.location or "",
            "project_type": proposal.project_type or "",
            "problem": brief[:500] if brief else "",
            "solution": work or (lang_es and "Alcance de obra a ejecutar según descripción." or "Scope of work as described."),
            "highlights": measures[:6] if measures else (
                [lang_es and "Medidas incluidas en la descripción" or "Measurements included in the description"]
            ),
        },
        "before_after": {
            "intro": lang_es and "Estado actual y resultado propuesto." or "Current state and proposed result.",
            "notes": "",
            "after_prompt": work or brief,
        },
        "scope": {
            "intro": work or intro,
            "modules": [
                {
                    "title": lang_es and "Preparación y protección" or "Prep and protection",
                    "description": lang_es and "Protección de áreas, demolición selectiva si aplica." or "Area protection, selective demo if needed.",
                },
                {
                    "title": lang_es and "Trabajo principal" or "Main work",
                    "description": work or intro,
                },
                {
                    "title": lang_es and "Acabados y limpieza" or "Finishes and cleanup",
                    "description": lang_es and "Acabados finales y limpieza de obra." or "Final finishes and site cleanup.",
                },
            ],
            "deliverables": [
                lang_es and "Obra completa según alcance" or "Completed work per scope",
                lang_es and f"Medidas consideradas: {measure_note}" or f"Measurements considered: {measure_note}",
            ],
        },
        "materials": {
            "intro": lang_es and "Materiales a confirmar con proveedores." or "Materials to confirm with suppliers.",
            "notes": measure_note,
        },
        "timeline": {
            "intro": lang_es and "Cronograma tentativo sujeto a materiales y acceso." or "Tentative schedule subject to materials and access.",
            "phases": [
                {"name": lang_es and "Inicio" or "Kickoff", "weeks": 1, "detail": lang_es and "Visita, medidas, pedidos." or "Visit, measurements, orders."},
                {"name": lang_es and "Ejecución" or "Build", "weeks": 3, "detail": lang_es and "Trabajo principal en sitio." or "Main on-site work."},
                {"name": lang_es and "Cierre" or "Closeout", "weeks": 1, "detail": lang_es and "Acabados, revisión, entrega." or "Finishes, walkthrough, handoff."},
            ],
            "tasks": [],
        },
        "pricing": {
            "intro": lang_es and "Presupuesto inicial a refinar con materiales y mano de obra." or "Initial budget to refine with materials and labor.",
            "notes": "",
            "markup_percent": 15.0,
            "contingency_percent": 5.0,
            "payments": [
                {"label": lang_es and "Anticipo" or "Deposit", "percent": 40},
                {"label": lang_es and "Avance" or "Progress", "percent": 40},
                {"label": lang_es and "Entrega" or "Completion", "percent": 20},
            ],
        },
        "utility": {
            "intro": lang_es and "Utilidad proyectada del proyecto (ingresos menos costos)." or "Projected project utility (revenue minus costs).",
            "assumptions": [
                lang_es and "Costos de materiales según líneas agregadas" or "Material costs from added lines",
                lang_es and "Mano de obra estimada en presupuesto" or "Labor estimated in budget",
            ],
            "notes": "",
        },
        "legal": {
            "terms_md": lang_es
            and "Los precios son válidos según se indique. El cronograma depende de materiales y condiciones del sitio."
            or "Prices are valid as stated. Timeline depends on material lead times and site conditions.",
            "responsibilities_md": lang_es
            and "El cliente garantiza acceso al sitio y decisiones a tiempo. El contratista ejecuta según el alcance aprobado."
            or "Client provides site access and timely decisions. Contractor performs per approved scope.",
            "notes": "",
        },
    }


def _extract_measures(text):
    patterns = [
        r"\d+(?:[.,]\d+)?\s*(?:x|×)\s*\d+(?:[.,]\d+)?\s*(?:ft|feet|m|metros|cm|in|\"|')?",
        r"\d+(?:[.,]\d+)?\s*(?:sq\s?ft|ft²|m²|sqm|pies|metros)",
        r"\d+(?:[.,]\d+)?\s*(?:ft|feet|m|metros|cm)\b",
    ]
    found = []
    for pat in patterns:
        for match in re.findall(pat, text or "", flags=re.IGNORECASE):
            clean = match.strip()
            if clean and clean not in found:
                found.append(clean)
    return found[:12]


def generate_proposal(proposal, extra_instructions="", on_progress=None):
    """Fill all AI sections from brief. Returns dict of section data."""
    model = llm.resolve_copy_model(proposal.env, proposal)
    stages = list(GENERATE_SECTIONS)

    def progress(pct, section):
        if on_progress:
            on_progress(pct, section)

    progress(5, "cover")
    draft = None
    used_ai = False
    if model and model.api_key:
        try:
            messages = [
                {"role": "system", "content": _system_prompt(proposal.language)},
                {"role": "user", "content": _brief_text(proposal)},
            ]
            if extra_instructions:
                messages.append({"role": "user", "content": f"EXTRA INSTRUCTIONS:\n{extra_instructions}"})
            progress(15, "description")
            raw = llm.chat_completion(model, messages, response_json=True, timeout=180)
            progress(70, "pricing")
            draft = llm.parse_json_object(raw)
            used_ai = True
        except Exception:
            _logger.exception("AI generate failed; using local draft")
            draft = None

    if not isinstance(draft, dict):
        progress(40, "scope")
        draft = _fallback_from_brief(proposal)

    # Keep only known sections and merge safely
    result = {}
    for i, key in enumerate(stages):
        progress(80 + int(15 * (i + 1) / len(stages)), key)
        if isinstance(draft.get(key), dict):
            result[key] = draft[key]
    progress(100, "legal")
    result["_meta"] = {"used_ai": used_ai}
    return result


def apply_generated_sections(proposal, sections):
    content = normalize_content(proposal.content or {})
    touched = []
    for key in SECTION_ORDER:
        if key in sections and isinstance(sections[key], dict):
            merged = dict(content.get(key) or {})
            merged.update(sections[key])
            content[key] = merged
            touched.append(key)
    # Sync utility revenue placeholder from lines if any
    proposal.write({"content": content})
    try:
        proposal.sync_utility_from_lines()
    except Exception:
        _logger.exception("utility sync after generate")
    # Soft repair: ensure utility intro exists
    content = normalize_content(proposal.content or {})
    utility = dict(content.get("utility") or {})
    if not (utility.get("intro") or "").strip():
        utility["intro"] = (
            "Utilidad proyectada del proyecto."
            if (proposal.language or "es") == "es"
            else "Projected project utility."
        )
        content["utility"] = utility
        proposal.write({"content": content})
    return touched
