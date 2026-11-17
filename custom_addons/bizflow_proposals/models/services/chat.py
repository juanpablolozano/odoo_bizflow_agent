# -*- coding: utf-8 -*-
import json
import logging

from odoo.addons.bizflow_proposals.models.schema import SECTION_LABELS, SECTION_ORDER, normalize_content
from odoo.addons.bizflow_proposals.models.services import llm

_logger = logging.getLogger(__name__)


def _system(language, only_section=None):
    lang = "Spanish" if language == "es" else "English"
    base = (
        f"You edit construction proposals with the sales team in {lang}. "
        "Return JSON with: reply (1-3 short non-technical sentences), "
        "sections (object of complete section objects you change; omit unchanged), "
        "hide_pages (list of section keys to hide), show_pages (list to show again). "
        f"Allowed section keys: {', '.join(SECTION_ORDER)}. "
        "Keep measurements from the brief when relevant. Never expose JSON/field names to the client in reply."
    )
    if only_section:
        base += (
            f" This request is only for section '{only_section}'. "
            "Change only that section; leave others out of 'sections'."
        )
    return base


def history_messages(proposal, thread=""):
    Message = proposal.env["bizflow.proposal.message"]
    rows = Message.search(
        [("proposal_id", "=", proposal.id), ("thread", "=", thread or "")],
        order="id asc",
    )
    # Drop everything before the latest reset marker
    reset_idx = -1
    for i, row in enumerate(rows):
        if row.role == "reset":
            reset_idx = i
    if reset_idx >= 0:
        rows = rows[reset_idx + 1 :]
    return [{"role": r.role, "text": r.text} for r in rows if r.role in ("user", "assistant")]


def edit_proposal(proposal, instruction, thread="", focus="", only=False):
    """Apply a chat instruction. Returns reply, sections dict, visibility dict."""
    model = llm.resolve_copy_model(proposal.env, proposal)
    content = normalize_content(proposal.content or {})
    only_section = focus if only and focus in SECTION_ORDER else None
    hist = history_messages(proposal, thread=thread)

    if not (model and model.api_key):
        # Local heuristic edits without API
        return _local_edit(proposal, instruction, content, only_section)

    messages = [
        {"role": "system", "content": _system(proposal.language, only_section)},
        {
            "role": "user",
            "content": (
                f"BRIEF:\n{proposal.brief or proposal.description or ''}\n\n"
                f"WORK NOTES:\n{proposal.work_brief or ''}\n\n"
                f"CURRENT PROPOSAL JSON:\n{json.dumps(content, ensure_ascii=False)}"
            ),
        },
    ]
    for h in hist[-12:]:
        messages.append({"role": h["role"], "content": h["text"]})
    user_msg = instruction
    if focus:
        label = SECTION_LABELS.get(focus, focus)
        user_msg = f"[Looking at section: {label} / {focus}]\n{instruction}"
    messages.append({"role": "user", "content": user_msg})

    try:
        raw = llm.chat_completion(model, messages, response_json=True, timeout=120)
        data = llm.parse_json_object(raw)
    except Exception as exc:
        _logger.exception("chat edit failed")
        return str(exc), {}, {"hide": [], "show": []}, True

    reply = data.get("reply") or (
        "Listo." if (proposal.language or "es") == "es" else "Done."
    )
    sections = data.get("sections") or {}
    if not isinstance(sections, dict):
        sections = {}
    if only_section:
        sections = {k: v for k, v in sections.items() if k == only_section and isinstance(v, dict)}
    else:
        sections = {k: v for k, v in sections.items() if k in SECTION_ORDER and isinstance(v, dict)}
    visibility = {
        "hide": [k for k in (data.get("hide_pages") or []) if k in SECTION_ORDER],
        "show": [k for k in (data.get("show_pages") or []) if k in SECTION_ORDER],
    }
    return reply, sections, visibility, False


def _local_edit(proposal, instruction, content, only_section):
    """Simple offline edit: append instruction note to intro of target section."""
    lang_es = (proposal.language or "es") == "es"
    key = only_section or "description"
    section = dict(content.get(key) or {})
    note = instruction.strip()
    field = "intro" if "intro" in section or key != "cover" else "tagline"
    if key == "cover":
        section["tagline"] = (section.get("tagline") or "")
        if note:
            section["subtitle"] = note[:120]
    else:
        prev = section.get("intro") or section.get("notes") or ""
        section["intro"] = (prev + ("\n\n" if prev else "") + note).strip()
    reply = (
        lang_es
        and f"Actualicé “{SECTION_LABELS.get(key, key)}” con tu pedido."
        or f"Updated “{SECTION_LABELS.get(key, key)}” with your request."
    )
    return reply, {key: section}, {"hide": [], "show": []}, False


def apply_chat_result(proposal, sections, visibility):
    content = normalize_content(proposal.content or {})
    touched = []
    for key, data in (sections or {}).items():
        if key not in SECTION_ORDER or not isinstance(data, dict):
            continue
        merged = dict(content.get(key) or {})
        merged.update(data)
        content[key] = merged
        touched.append(key)
    hidden = set(proposal.hidden_sections or [])
    for key in visibility.get("hide") or []:
        hidden.add(key)
    for key in visibility.get("show") or []:
        hidden.discard(key)
    vals = {"content": content, "hidden_sections": list(hidden)}
    proposal.write(vals)
    return touched
