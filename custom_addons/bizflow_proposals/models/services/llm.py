# -*- coding: utf-8 -*-
import json
import logging
from urllib.request import Request, urlopen

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def resolve_copy_model(env, proposal=None):
    Ai = env["bizflow.ai.model"]
    model = proposal.copy_model_id if proposal else False
    if not model:
        model = Ai.search([("purpose", "=", "proposal_copy"), ("active", "=", True)], limit=1)
    return model


def chat_completion(model, messages, temperature=0.4, response_json=False, timeout=120):
    """Call OpenAI-compatible chat completions. Returns assistant text."""
    if not model:
        raise UserError("No AI model configured for proposal copy.")
    if not model.api_key:
        raise UserError(
            "Configure an API key on the proposal copy AI model (Configuration → AI models)."
        )
    base = (model.api_base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"
    body = {
        "model": model.model_name or "gpt-4o-mini",
        "messages": messages,
        "temperature": temperature,
    }
    if response_json:
        body["response_format"] = {"type": "json_object"}
    extra = model.extra_params or {}
    if isinstance(extra, dict):
        body.update({k: v for k, v in extra.items() if k not in body})
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
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        _logger.exception("LLM chat completion failed")
        raise UserError(f"AI request failed: {exc}") from exc
    try:
        return payload["choices"][0]["message"]["content"] or ""
    except Exception as exc:
        raise UserError(f"Unexpected AI response: {payload}") from exc


def parse_json_object(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    return json.loads(text)
