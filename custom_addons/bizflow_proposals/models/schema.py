# -*- coding: utf-8 -*-
"""Construction proposal content schema (Wise-inspired, construction domain)."""

SECTION_ORDER = [
    "cover",
    "description",
    "before_after",
    "scope",
    "materials",
    "timeline",
    "pricing",
    "utility",
    "legal",
    "design",
    "extras",
]

SECTION_LABELS = {
    "cover": "Cover",
    "description": "Project description",
    "before_after": "Before / After",
    "scope": "Scope of work",
    "materials": "Materials & suppliers",
    "timeline": "Construction timeline",
    "pricing": "Budget",
    "utility": "Project utility",
    "legal": "Terms",
    "design": "PDF design",
    "extras": "Extra pages",
}

DEFAULT_CONTENT = {
    "cover": {
        "title": "",
        "subtitle": "",
        "tagline": "",
        "image": False,
        "client_logo": False,
    },
    "description": {
        "intro": "",
        "location": "",
        "project_type": "",
        "problem": "",
        "solution": "",
        "highlights": [],
    },
    "before_after": {
        "intro": "",
        "before_image": False,
        "after_image": False,
        "after_prompt": "",
        "notes": "",
    },
    "scope": {
        "intro": "",
        "modules": [],
        "deliverables": [],
    },
    "materials": {
        "intro": "",
        "notes": "",
    },
    "timeline": {
        "intro": "",
        "phases": [],
        "tasks": [],
    },
    "pricing": {
        "currency": "USD",
        "intro": "",
        "labor_total": 0.0,
        "materials_total": 0.0,
        "markup_percent": 0.0,
        "contingency_percent": 0.0,
        "notes": "",
        "materials_prompt": "",
        "payments": [],
    },
    "utility": {
        "intro": "",
        "revenue": 0.0,
        "material_cost": 0.0,
        "labor_cost": 0.0,
        "overhead_cost": 0.0,
        "contingency_cost": 0.0,
        "assumptions": [],
        "notes": "",
    },
    "legal": {
        "terms_md": "",
        "responsibilities_md": "",
        "notes": "",
    },
    "design": {
        "css": "",
        "header_html": "",
        "footer_html": "",
    },
    "extras": {
        "pages": [],
    },
}


def normalize_content(raw):
    """Return a full content dict with defaults filled in."""
    data = dict(DEFAULT_CONTENT)
    if not isinstance(raw, dict):
        return data
    for key, default in DEFAULT_CONTENT.items():
        incoming = raw.get(key)
        if isinstance(default, dict) and isinstance(incoming, dict):
            merged = dict(default)
            merged.update(incoming)
            data[key] = merged
        elif incoming is not None:
            data[key] = incoming
        else:
            data[key] = default if not isinstance(default, (list, dict)) else (
                list(default) if isinstance(default, list) else dict(default)
            )
    return data


def empty_content():
    return normalize_content({})
