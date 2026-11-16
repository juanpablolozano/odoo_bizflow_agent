# -*- coding: utf-8 -*-
import json
import logging
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def resolve_copy_model(env, proposal=None):
    Ai = env["bizflow.ai.model"]
    model = proposal.copy_model_id if proposal else False
    if not model:
        model = Ai.search([("purpose", "=", "proposal_copy"), ("active", "=", True)], limit=1)
    return model


def _error_body(exc):
    if isinstance(exc, HTTPError):
        try:
            return exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            return str(exc)
    return str(exc)


def chat_completion(model, messages, temperature=0.4, response_json=False, timeout=120):
    """Call OpenAI-compatible chat completions. Returns assistant text."""
    if not model:
        raise UserError("No AI model configured.")
    if not model.api_key:
        raise UserError(
            "Configure an API key on the AI model (Configuration → AI models)."
        )
    base = (model.api_base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"

    def _do_request(use_json_format):
        body = {
            "model": model.model_name or "gpt-4o-mini",
            "messages": messages,
            "temperature": temperature,
        }
        if use_json_format:
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
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        payload = _do_request(response_json)
    except HTTPError as exc:
        detail = _error_body(exc)
        _logger.exception("LLM chat completion failed: %s", detail)
        if response_json and exc.code == 400:
            try:
                payload = _do_request(False)
            except Exception as retry_exc:
                raise UserError(
                    f"AI request failed: HTTP Error {exc.code}: {detail or retry_exc}"
                ) from retry_exc
        else:
            raise UserError(f"AI request failed: HTTP Error {exc.code}: {detail}") from exc
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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Recover common LLM issues: prose wrapper, truncated trailing comma.
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            snippet = text[start : end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                cleaned = re.sub(r",\s*([}\]])", r"\1", snippet)
                return json.loads(cleaned)
        raise
