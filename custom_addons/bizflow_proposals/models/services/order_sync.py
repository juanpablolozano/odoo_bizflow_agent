# -*- coding: utf-8 -*-
"""Sync bizflow.proposal lines onto the linked sale.order quotation."""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


def _generic_products(env):
    Product = env["product.product"].sudo()
    material = Product.search([("default_code", "=", "BIZFLOW_MATERIAL")], limit=1)
    labor = Product.search([("default_code", "=", "BIZFLOW_LABOR")], limit=1)
    if not material:
        material = Product.create(
            {
                "name": "Construction materials",
                "default_code": "BIZFLOW_MATERIAL",
                "type": "service",
                "list_price": 0.0,
                "sale_ok": True,
                "purchase_ok": False,
                "invoice_policy": "order",
            }
        )
    if not labor:
        labor = Product.create(
            {
                "name": "Construction labor",
                "default_code": "BIZFLOW_LABOR",
                "type": "service",
                "list_price": 0.0,
                "sale_ok": True,
                "purchase_ok": False,
                "invoice_policy": "order",
            }
        )
    return material, labor


def sync_proposal_to_sale_order(proposal):
    """Replace BizFlow-synced order lines with current proposal lines."""
    proposal.ensure_one()
    order = proposal.order_id
    if not order:
        return False
    if order.state not in ("draft", "sent"):
        _logger.info(
            "skip order sync for %s: order %s state=%s",
            proposal.code,
            order.name,
            order.state,
        )
        return False

    material_product, labor_product = _generic_products(proposal.env)
    stale = order.order_line.filtered(lambda l: l.bizflow_synced)
    if stale:
        stale.unlink()

    sequence = 10
    commands = []
    for line in proposal.line_ids.sorted(key=lambda l: (l.sequence, l.id)):
        product = labor_product if line.line_type == "labor" else material_product
        # Customer-facing description: item name only (no generic product, uom, or notes).
        name = (line.name or "").strip() or product.name
        commands.append(
            (
                0,
                0,
                {
                    "product_id": product.id,
                    "name": name,
                    "product_uom_qty": float(line.quantity or 1.0),
                    "price_unit": float(line.price_unit or 0.0),
                    "sequence": sequence,
                    "bizflow_synced": True,
                    "bizflow_proposal_line_id": line.id,
                },
            )
        )
        sequence += 10

    if commands:
        order.write({"order_line": commands})
    _logger.info(
        "synced %s proposal lines → sale.order %s",
        len(commands),
        order.name,
    )
    return True
