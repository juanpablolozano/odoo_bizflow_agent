# -*- coding: utf-8 -*-
import json
import logging
import re
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BizflowSupplier(models.Model):
    _name = "bizflow.supplier"
    _description = "Material Supplier"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    code = fields.Char(help="Short code, e.g. HOME_DEPOT, LOWES")
    provider_type = fields.Selection(
        [
            ("manual", "Manual catalog"),
            ("api", "HTTP API"),
            ("website_search", "Website search"),
        ],
        required=True,
        default="manual",
    )
    website = fields.Char()
    search_url_template = fields.Char(
        help="Use {query} placeholder. Example: https://www.homedepot.com/s/{query}",
    )
    api_base_url = fields.Char()
    api_key = fields.Char()
    api_headers_json = fields.Text(
        string="API headers (JSON)",
        help='Optional headers as JSON object, e.g. {"Authorization": "Bearer ..."}',
    )
    api_search_path = fields.Char(
        default="/search",
        help="Path appended to API base URL for search",
    )
    result_mapping_json = fields.Text(
        string="Result mapping (JSON)",
        help='Map response fields: {"items":"products","name":"title","sku":"id","price":"price","url":"link"}',
    )
    notes = fields.Text()
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    material_ids = fields.One2many("bizflow.material", "supplier_id", string="Materials")

    def action_search_materials(self, query, limit=20):
        self.ensure_one()
        query = (query or "").strip()
        if not query:
            return []
        if self.provider_type == "manual":
            return self._search_manual(query, limit)
        if self.provider_type == "api":
            return self._search_api(query, limit)
        if self.provider_type == "website_search":
            return self._search_website(query, limit)
        return []

    def _search_manual(self, query, limit):
        Materials = self.env["bizflow.material"]
        # Own catalog also includes materials without an assigned supplier.
        supplier_domain = [("supplier_id", "=", self.id)]
        if self.code == "OWN":
            supplier_domain = ["|", ("supplier_id", "=", self.id), ("supplier_id", "=", False)]
        domain = supplier_domain + [
            "|",
            ("name", "ilike", query),
            ("sku", "ilike", query),
        ]
        rows = Materials.search(domain, limit=limit)
        return [
            {
                "name": m.name,
                "sku": m.sku or "",
                "price": m.list_price,
                "url": m.product_url or "",
                "source": "manual",
                "material_id": m.id,
            }
            for m in rows
        ]

    def _parse_mapping(self):
        raw = self.result_mapping_json or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _parse_headers(self):
        raw = self.api_headers_json or "{}"
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _http_get(self, url, headers=None, timeout=15):
        # Browser-like UA reduces 403s from retail sites (HD/Lowe's).
        req_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if headers:
            req_headers.update(headers)
        request = Request(url, headers=req_headers)
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="ignore")

    def _search_api(self, query, limit):
        if not self.api_base_url:
            raise UserError(self.env._("Supplier %s has no API base URL.", self.name))
        path = self.api_search_path or "/search"
        base = self.api_base_url.rstrip("/")
        params = urlencode({"q": query, "query": query, "limit": limit})
        url = f"{base}{path}?{params}"
        headers = self._parse_headers()
        if self.api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            body = self._http_get(url, headers=headers)
            payload = json.loads(body)
        except Exception as exc:
            _logger.warning("Supplier API search failed for %s: %s", self.name, exc)
            raise UserError(
                self.env._("Could not search supplier %s via API: %s", self.name, exc)
            ) from exc
        mapping = self._parse_mapping()
        items_key = mapping.get("items", "items")
        items = payload.get(items_key, []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            items = []
        results = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "name": item.get(mapping.get("name", "name")) or item.get("title") or "",
                    "sku": str(item.get(mapping.get("sku", "sku")) or item.get("id") or ""),
                    "price": float(item.get(mapping.get("price", "price")) or 0.0),
                    "url": item.get(mapping.get("url", "url")) or item.get("link") or "",
                    "source": "api",
                    "material_id": False,
                }
            )
        return results

    def _search_website(self, query, limit):
        """Best-effort website search: fetch search page and extract product-like links/prices."""
        template = self.search_url_template or self.website
        if not template:
            raise UserError(
                self.env._("Supplier %s needs a search URL template or website.", self.name)
            )
        url = template.format(query=quote_plus(query)) if "{query}" in template else (
            f"{template.rstrip('/')}/s/{quote_plus(query)}"
        )
        try:
            html = self._http_get(url)
        except Exception as exc:
            _logger.warning("Website search failed for %s: %s", self.name, exc)
            # Fallback to local catalog so the editor keeps working
            return self._search_manual(query, limit)

        results = []
        # Generic extraction: product-looking anchors + nearby price patterns
        link_pattern = re.compile(
            r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        price_pattern = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")
        seen = set()
        for href, label in link_pattern.findall(html):
            text = re.sub(r"<[^>]+>", " ", label)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 8 or query.lower().split()[0] not in text.lower():
                continue
            key = (text.lower(), href)
            if key in seen:
                continue
            seen.add(key)
            price_match = price_pattern.search(html[html.find(href) : html.find(href) + 400])
            price = float(price_match.group(1)) if price_match else 0.0
            full_url = href if href.startswith("http") else (
                f"{(self.website or '').rstrip('/')}/{href.lstrip('/')}"
            )
            results.append(
                {
                    "name": text[:200],
                    "sku": "",
                    "price": price,
                    "url": full_url,
                    "source": "website_search",
                    "material_id": False,
                }
            )
            if len(results) >= limit:
                break
        if not results:
            return self._search_manual(query, limit)
        return results

    @api.model
    def search_all_providers(self, query, supplier_ids=None, limit=20, location=None):
        domain = [("active", "=", True)]
        if supplier_ids:
            domain.append(("id", "in", supplier_ids))
        suppliers = self.search(domain)
        # Soft location hint: append ZIP/city when caller did not already.
        search_query = (query or "").strip()
        loc = (location or "").strip()
        if loc and loc.lower() not in search_query.lower():
            zip_m = re.search(r"\b(\d{5})(?:-\d{4})?\b", loc)
            hint = zip_m.group(1) if zip_m else loc.split(",")[0].strip()
            if hint and hint.lower() not in search_query.lower():
                search_query = f"{search_query} {hint}".strip()
        combined = []
        for supplier in suppliers:
            try:
                hits = supplier.action_search_materials(search_query, limit=limit)
            except UserError:
                continue
            for hit in hits:
                hit["supplier_id"] = supplier.id
                hit["supplier_name"] = supplier.name
                combined.append(hit)
        return combined[: limit * max(len(suppliers), 1)]
