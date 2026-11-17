# -*- coding: utf-8 -*-
"""Helpers for Odoo 20 Binary / BinaryValueAttachment values."""
import base64


def binary_to_bytes(value):
    """Return raw bytes from a Binary field value (str/bytes/BinaryValue*)."""
    if not value:
        return b""
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        try:
            return base64.b64decode(raw, validate=True)
        except Exception:
            return raw
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("data:") and "," in text:
            text = text.split(",", 1)[1]
        return base64.b64decode(text)
    return b""


def binary_to_b64(value):
    """Return ascii base64 string suitable for JSON APIs / Binary writes."""
    if not value:
        return ""
    content = getattr(value, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return base64.b64encode(bytes(content)).decode("ascii")
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("data:") and "," in text:
            return text.split(",", 1)[1]
        return text
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        try:
            base64.b64decode(raw, validate=True)
            return raw.decode("ascii")
        except Exception:
            return base64.b64encode(raw).decode("ascii")
    return ""
