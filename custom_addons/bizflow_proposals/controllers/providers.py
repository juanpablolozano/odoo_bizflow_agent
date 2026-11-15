# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request


class BizflowProviderController(http.Controller):

    @http.route("/bizflow/suppliers", type="http", auth="user", methods=["GET"], csrf=False)
    def list_suppliers(self, **kwargs):
        suppliers = request.env["bizflow.supplier"].search([("active", "=", True)])
        return request.make_json_response(
            {
                "items": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "code": s.code,
                        "provider_type": s.provider_type,
                        "website": s.website or "",
                    }
                    for s in suppliers
                ]
            }
        )

    @http.route("/bizflow/materials/search", type="jsonrpc", auth="user", methods=["POST"])
    def search_materials(self, **payload):
        query = payload.get("query") or ""
        supplier_ids = payload.get("supplier_ids") or []
        limit = int(payload.get("limit") or 20)
        results = request.env["bizflow.supplier"].search_all_providers(
            query,
            supplier_ids=supplier_ids or None,
            limit=limit,
        )
        return {"items": results}

    @http.route("/bizflow/materials/import", type="jsonrpc", auth="user", methods=["POST"])
    def import_material(self, **payload):
        Material = request.env["bizflow.material"]
        supplier_id = payload.get("supplier_id") or False
        if not supplier_id:
            own = request.env["bizflow.supplier"].search([("code", "=", "OWN")], limit=1)
            supplier_id = own.id if own else False
        vals = {
            "name": payload.get("name") or request.env._("Material"),
            "sku": payload.get("sku") or "",
            "supplier_id": supplier_id,
            "list_price": float(payload.get("price") or 0.0),
            "cost_price": float(payload.get("cost_price") or payload.get("price") or 0.0),
            "product_url": payload.get("url") or "",
            "last_fetched_price": float(payload.get("price") or 0.0),
            "uom_name": payload.get("uom_name") or "unit",
            "category": payload.get("category") or "other",
        }
        material = Material.create(vals)
        return {
            "id": material.id,
            "name": material.name,
            "sku": material.sku,
            "price": material.get_unit_price(),
            "supplier_id": material.supplier_id.id,
        }
