# -*- coding: utf-8 -*-
import json
import logging
import os
import re
import subprocess
import tempfile
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _chrome_bin():
    return (
        os.environ.get("CHROME_BIN")
        or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )


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
            return self._filter_useful_hits(self._search_manual(query, limit))
        if self.provider_type == "api":
            return self._filter_useful_hits(self._search_api(query, limit))
        if self.provider_type == "website_search":
            # 1) SerpAPI organic + shopping (reliable when retailers bot-block Chrome)
            hits = self._filter_useful_hits(self._search_serpapi_shopping(query, limit))
            if hits:
                return hits
            # When SerpAPI is configured, skip Chrome/Bing scrapers — HD/Lowes bot-block
            # and those paths hang the JSON search RPC for 30–60s+ with empty results.
            if self._serpapi_key():
                return self._filter_useful_hits(self._search_manual(query, limit))
            # 2) Direct retailer search via Chrome headless (+ enrich product pages)
            hits = self._filter_useful_hits(self._search_website(query, limit))
            if hits:
                return hits
            # 3) SERP candidates → enrich product pages
            hits = self._filter_useful_hits(self._search_web_prices(query, limit))
            if hits:
                return hits
            # 4) Manual catalog only (must have price+URL). Never invent market estimates.
            return self._filter_useful_hits(self._search_manual(query, limit))
        return []

    def _serpapi_key(self):
        key = (self.api_key or "").strip()
        if key:
            return key
        ICP = self.env["ir.config_parameter"].sudo()
        key = ""
        if hasattr(ICP, "get_str"):
            key = ICP.get_str("bizflow.serpapi_key", "") or ""
        elif hasattr(ICP, "get_param"):
            key = ICP.get_param("bizflow.serpapi_key", default="") or ""
        key = (key or "").strip()
        if key:
            return key
        return (os.environ.get("SERPAPI_API_KEY") or os.environ.get("SERP_API_KEY") or "").strip()

    def _chrome_get_html(self, url, timeout=35):
        """Fetch rendered HTML via Chrome headless (avoids many retailer 403 shells)."""
        chrome = _chrome_bin()
        if not os.path.exists(chrome):
            _logger.warning("Chrome not found at %s; falling back to urllib", chrome)
            return self._http_get(url, timeout=timeout)
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "page.html")
            try:
                proc = subprocess.run(
                    [
                        chrome,
                        "--headless=new",
                        "--disable-gpu",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-extensions",
                        f"--virtual-time-budget=12000",
                        f"--dump-dom",
                        url,
                    ],
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
                html = (proc.stdout or b"").decode("utf-8", errors="ignore")
                if len(html) < 500 and proc.stderr:
                    _logger.warning(
                        "Chrome dump-dom short for %s: %s",
                        url,
                        (proc.stderr or b"")[:300],
                    )
                if html:
                    return html
            except Exception as exc:
                _logger.warning("Chrome fetch failed for %s: %s", url, exc)
        return self._http_get(url, timeout=min(timeout, 20))

    def _is_product_url(self, url):
        u = (url or "").lower()
        host = self._site_host()
        if host and host not in u:
            return False
        if "/search" in u or re.search(r"/s(?:/|\?|$)", u):
            return False
        if "/reviews/" in u or "/review/" in u or "/questions/" in u:
            return False
        if "homedepot.com" in u and "/p/" in u:
            return True
        if "lowes.com" in u and ("/pd/" in u or "/products/" in u):
            return True
        return False

    def _enrich_hit_from_product_page(self, url, fallback_name=""):
        """Load product page and extract name + price (JSON-LD / NEXT_DATA / $)."""
        url = (url or "").strip()
        if not url.startswith("http"):
            return None
        try:
            html = self._chrome_get_html(url, timeout=40)
        except Exception as exc:
            _logger.warning("Product enrich failed %s: %s", url, exc)
            return None
        name = (fallback_name or "").strip()
        price = 0.0
        # JSON-LD Product
        for m in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            re.I | re.S,
        ):
            raw = m.group(1).strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                types = item.get("@type")
                type_l = (
                    [types]
                    if isinstance(types, str)
                    else (types if isinstance(types, list) else [])
                )
                if not any("product" in str(t).lower() for t in type_l):
                    # Sometimes @graph
                    if "@graph" in item:
                        items.extend(
                            [g for g in item.get("@graph") or [] if isinstance(g, dict)]
                        )
                    continue
                name = name or (item.get("name") or "")[:200]
                offers = item.get("offers") or {}
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if isinstance(offers, dict):
                    try:
                        price = float(offers.get("price") or offers.get("lowPrice") or 0)
                    except (TypeError, ValueError):
                        price = 0.0
                if price > 0:
                    break
            if price > 0:
                break
        # __NEXT_DATA__ fallback
        if price <= 0:
            nxt = re.search(
                r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                html,
                re.I | re.S,
            )
            if nxt:
                blob = nxt.group(1)
                pm = re.search(r'"price"\s*:\s*"?(?P<p>\d+(?:\.\d{1,2})?)"?', blob)
                if pm:
                    price = float(pm.group("p"))
                if not name:
                    nm = re.search(r'"productName"\s*:\s*"(?P<n>[^"]{5,200})"', blob)
                    if nm:
                        name = nm.group("n")
        if price <= 0:
            pm = re.search(r'"currentPrice"\s*:\s*([\d.]+)', html) or re.search(
                r'itemprop="price"[^>]+content="([\d.]+)"', html, re.I
            )
            if pm:
                price = float(pm.group(1))
        if price <= 0:
            pm = re.search(r"\$\s?(\d+(?:\.\d{1,2})?)", html)
            if pm:
                price = float(pm.group(1))
        if not name:
            tm = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            if tm:
                name = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", tm.group(1))).strip()
                name = name.split("|")[0].split("-")[0].strip()[:200]
        if price <= 0 or not name:
            return None
        return {
            "name": name[:200],
            "sku": "",
            "price": price,
            "url": url[:500],
            "source": "website_search",
            "material_id": False,
        }

    def _price_from_serpapi_item(self, item):
        """Extract unit price from SerpAPI organic/shopping item."""
        if not isinstance(item, dict):
            return 0.0
        raw = item.get("extracted_price")
        if raw is None:
            raw = item.get("price")
        if isinstance(raw, (int, float)) and float(raw) > 0:
            return float(raw)
        if isinstance(raw, str):
            m = re.search(r"(\d+(?:\.\d{1,2})?)", raw.replace(",", ""))
            if m:
                return float(m.group(1))
        rich = item.get("rich_snippet") or {}
        if isinstance(rich, dict):
            for section in ("top", "bottom", "left", "right"):
                block = rich.get(section) or {}
                if not isinstance(block, dict):
                    continue
                detected = block.get("detected_extensions") or {}
                if not isinstance(detected, dict):
                    continue
                currency = str(detected.get("currency") or "")
                # SerpAPI often mislabels "Free delivery over $25" as price.
                if re.search(r"delivery|shipping|return", currency, re.I):
                    continue
                if detected.get("price") is not None:
                    try:
                        price = float(detected.get("price"))
                    except (TypeError, ValueError):
                        price = 0.0
                    if price > 0:
                        return price
                for ext in block.get("extensions") or []:
                    text = str(ext)
                    if re.search(r"delivery|shipping|return|rating", text, re.I):
                        continue
                    m = re.search(r"\$\s?(\d+(?:\.\d{1,2})?)", text)
                    if m:
                        return float(m.group(1))
        snip = item.get("snippet") or ""
        if not re.search(r"delivery|shipping", snip, re.I):
            m = re.search(r"\$\s?(\d+(?:\.\d{1,2})?)", snip)
            if m:
                return float(m.group(1))
        return 0.0

    def _shopping_source_matches(self, source):
        src = (source or "").lower().replace("'", "")
        name = (self.name or "").lower().replace("'", "")
        if name and name in src:
            return True
        code = (self.code or "").upper()
        if code == "HOME_DEPOT":
            return "home depot" in src or "homedepot" in src
        if code == "LOWES":
            return "lowe" in src
        return False

    def _title_tokens(self, text):
        stop = {
            "the",
            "and",
            "for",
            "with",
            "from",
            "lowes",
            "lowe",
            "home",
            "depot",
            "at",
            "in",
            "x",
            "ft",
        }
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        return {t for t in tokens if len(t) > 1 and t not in stop}
    def _title_similarity(self, a, b):
        ta = self._title_tokens(a)
        tb = self._title_tokens(b)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / float(len(ta | tb))

    def _serpapi_json(self, params, timeout=30):
        try:
            body = self._http_get(f"https://serpapi.com/search.json?{params}", timeout=timeout)
            payload = json.loads(body)
        except Exception as exc:
            _logger.warning("SerpAPI request failed for %s: %s", self.name, exc)
            return {}
        if payload.get("error"):
            _logger.warning("SerpAPI error for %s: %s", self.name, payload.get("error"))
            return {}
        return payload

    def _search_serpapi_shopping(self, query, limit):
        """SerpAPI: organic product URLs + shopping prices (matched by title)."""
        api_key = self._serpapi_key()
        if not api_key:
            _logger.info(
                "SerpAPI key missing (set bizflow.serpapi_key or SERPAPI_API_KEY); skip fallback"
            )
            return []
        host = self._site_host()
        if not host:
            return []

        path_hint = "/pd" if "lowes" in host else "/p"
        organic = []
        seen = set()
        for organic_q in (
            f"site:{host}{path_hint} {query}",
            f"site:{host} {query}",
        ):
            organic_payload = self._serpapi_json(
                urlencode(
                    {
                        "engine": "google",
                        "q": organic_q,
                        "api_key": api_key,
                        "num": max(limit * 3, 10),
                        "hl": "en",
                        "gl": "us",
                    }
                )
            )
            for item in organic_payload.get("organic_results") or []:
                if not isinstance(item, dict):
                    continue
                link = (item.get("link") or "").strip()
                title = (item.get("title") or query)[:200]
                if host not in link or not self._is_product_url(link):
                    continue
                key = link.split("?")[0]
                if key in seen:
                    continue
                seen.add(key)
                organic.append(
                    {
                        "name": title,
                        "sku": "",
                        "price": self._price_from_serpapi_item(item),
                        "url": key[:500],
                        "source": "serpapi",
                        "material_id": False,
                    }
                )
            if len(organic) >= limit:
                break

        retailer_q = re.sub(r"[^a-z0-9]+", " ", (self.name or "").lower()).strip()
        if (self.code or "").upper() == "LOWES":
            retailer_q = "lowes"
        elif (self.code or "").upper() == "HOME_DEPOT":
            retailer_q = "home depot"
        shopping = []
        for shop_q in (
            f"{query} lumber {retailer_q}",
            f"{query} {retailer_q}",
            f"{retailer_q} {query}",
        ):
            shopping_payload = self._serpapi_json(
                urlencode(
                    {
                        "engine": "google_shopping",
                        "q": shop_q.strip(),
                        "api_key": api_key,
                        "num": max(limit * 3, 12),
                        "hl": "en",
                        "gl": "us",
                    }
                )
            )
            for item in shopping_payload.get("shopping_results") or []:
                if not isinstance(item, dict):
                    continue
                if not self._shopping_source_matches(item.get("source") or ""):
                    continue
                price = self._price_from_serpapi_item(item)
                title = (item.get("title") or "")[:200]
                if price <= 0 or len(title) < 8:
                    continue
                shopping.append({"name": title, "price": price})
            if shopping:
                break        # Attach shopping prices to organic product URLs when organic lacked price.
        # Greedy unique matching by title similarity (avoid one shop price on every hit).
        pairs = []
        for hi, hit in enumerate(organic):
            if hit["price"] > 0:
                continue
            for si, shop in enumerate(shopping):
                score = self._title_similarity(hit["name"], shop["name"])
                if score >= 0.35:
                    pairs.append((score, hi, si))
        pairs.sort(reverse=True)
        used_hits = set()
        used_shops = set()
        for score, hi, si in pairs:
            if hi in used_hits or si in used_shops:
                continue
            organic[hi]["price"] = shopping[si]["price"]
            organic[hi]["source"] = "serpapi_matched"
            used_hits.add(hi)
            used_shops.add(si)

        results = [h for h in organic if h["price"] > 0]
        if len(results) >= limit:
            return results[:limit]

        # Last resort: resolve a few shopping titles to product URLs (cap API calls).
        lookups = 0
        for shop in shopping:
            if len(results) >= limit or lookups >= 3:
                break
            if any(self._title_similarity(shop["name"], r["name"]) >= 0.45 for r in results):
                continue
            path_hint = "/pd" if "lowes" in host else "/p"
            lookups += 1
            look = self._serpapi_json(
                urlencode(
                    {
                        "engine": "google",
                        "q": f'site:{host}{path_hint} {shop["name"][:70]}',
                        "api_key": api_key,
                        "num": 5,
                        "hl": "en",
                        "gl": "us",
                    }
                )
            )
            for item in look.get("organic_results") or []:
                link = (item.get("link") or "").strip().split("?")[0]
                if not self._is_product_url(link) or link in seen:
                    continue
                seen.add(link)
                title = (item.get("title") or shop["name"])[:200]
                results.append(
                    {
                        "name": title,
                        "sku": "",
                        "price": shop["price"],
                        "url": link[:500],
                        "source": "serpapi_shopping",
                        "material_id": False,
                    }
                )
                break
        return results[:limit]

    def _filter_useful_hits(self, hits):
        """Keep only verified hits: real price + http(s) product URL (proof of source)."""
        junk = {
            "ai mode",
            "web",
            "images",
            "videos",
            "maps",
            "news",
            "shopping",
            "sign in",
            "home",
            "menu",
        }
        useful = []
        for h in hits or []:
            name = (h.get("name") or "").strip()
            url = (h.get("url") or "").strip()
            price = float(h.get("price") or 0.0)
            low = name.lower()
            if not name or low in junk or len(name) < 8:
                continue
            if price <= 0:
                continue
            if not url.startswith("http://") and not url.startswith("https://"):
                continue
            if "/search?" in url or "copilotsearch" in url or "javascript:" in url:
                continue
            useful.append(h)
        useful.sort(key=lambda x: float(x.get("price") or 0))
        return useful

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

    def _site_host(self):
        code = (self.code or "").upper()
        if "HOME" in code or "DEPOT" in code:
            return "homedepot.com"
        if "LOWE" in code:
            return "lowes.com"
        website = (self.website or "").lower()
        if "homedepot" in website:
            return "homedepot.com"
        if "lowes" in website:
            return "lowes.com"
        return ""

    def _extract_price_hits(self, html, query, limit, source="website_search"):
        results = []
        link_pattern = re.compile(
            r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        price_pattern = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")
        token = (query.lower().split() or [""])[0]
        seen = set()
        for href, label in link_pattern.findall(html):
            text = re.sub(r"<[^>]+>", " ", label)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 6:
                continue
            if token and token not in text.lower() and token not in href.lower():
                continue
            key = (text.lower()[:80], href.split("?")[0])
            if key in seen:
                continue
            seen.add(key)
            idx = html.find(href)
            window = html[idx : idx + 600] if idx >= 0 else html
            price_match = price_pattern.search(window) or price_pattern.search(text)
            price = float(price_match.group(1)) if price_match else 0.0
            full_url = href if href.startswith("http") else (
                f"{(self.website or '').rstrip('/')}/{href.lstrip('/')}"
            )
            # Prefer retailer product URLs
            host = self._site_host()
            if host and host not in full_url and "duckduckgo" in full_url:
                continue
            results.append(
                {
                    "name": text[:200],
                    "sku": "",
                    "price": price,
                    "url": full_url[:500],
                    "source": source,
                    "material_id": False,
                }
            )
            if len(results) >= limit:
                break
        return results

    def _search_website(self, query, limit):
        """Retailer search via Chrome; enrich product URLs with live prices."""
        template = self.search_url_template or self.website
        if not template:
            return []
        url = template.format(query=quote_plus(query)) if "{query}" in template else (
            f"{template.rstrip('/')}/s/{quote_plus(query)}"
        )
        try:
            html = self._chrome_get_html(url)
        except Exception as exc:
            _logger.warning("Website search failed for %s: %s", self.name, exc)
            return []
        raw_hits = self._extract_price_hits(html, query, max(limit * 3, 12), source="website_search")
        # Prefer real product URLs; enrich when price missing.
        enriched = []
        seen = set()
        for hit in raw_hits:
            link = (hit.get("url") or "").split("?")[0]
            if link in seen:
                continue
            if not self._is_product_url(hit.get("url") or ""):
                continue
            seen.add(link)
            if float(hit.get("price") or 0) > 0:
                enriched.append(hit)
            else:
                got = self._enrich_hit_from_product_page(hit.get("url"), hit.get("name") or query)
                if got:
                    enriched.append(got)
            if len(enriched) >= limit:
                break
        return enriched

    def _search_web_prices(self, query, limit):
        """Site-scoped Bing/DDG → product URL candidates → enrich prices from product pages."""
        host = self._site_host()
        if not host:
            return []
        q = f"site:{host} {query}"
        engines = [
            f"https://www.bing.com/search?{urlencode({'q': q})}",
            f"https://html.duckduckgo.com/html/?{urlencode({'q': q})}",
        ]
        candidates = []
        for url in engines:
            try:
                html = self._chrome_get_html(url, timeout=25)
            except Exception as exc:
                _logger.warning("Web price search failed for %s via %s: %s", self.name, url, exc)
                continue
            candidates.extend(self._parse_search_engine_hits(html, host, query, limit * 2))
            if len(candidates) >= limit * 2:
                break
        enriched = []
        seen = set()
        for hit in candidates:
            link = (hit.get("url") or "").strip()
            key = link.split("?")[0]
            if key in seen:
                continue
            if not self._is_product_url(link):
                continue
            seen.add(key)
            if float(hit.get("price") or 0) > 0:
                enriched.append(hit)
            else:
                got = self._enrich_hit_from_product_page(link, hit.get("name") or query)
                if got:
                    enriched.append(got)
            if len(enriched) >= limit:
                break
        return enriched

    def _parse_search_engine_hits(self, html, host, query, limit):
        results = []
        price_pattern = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")
        patterns = [
            re.compile(
                r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)</div>',
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r'<a[^>]+href="([^"]*%s[^"]*)"[^>]*>(.*?)</a>' % re.escape(host),
                re.IGNORECASE | re.DOTALL,
            ),
        ]
        seen = set()
        for block_re in patterns:
            groups = block_re.findall(html)
            for group in groups:
                if len(group) == 3:
                    href, title_html, snippet_html = group
                else:
                    href, title_html = group
                    snippet_html = ""
                title = re.sub(r"<[^>]+>", " ", title_html)
                title = re.sub(r"\s+", " ", title).strip()
                snippet = re.sub(r"<[^>]+>", " ", snippet_html)
                snippet = re.sub(r"\s+", " ", snippet).strip()
                if "uddg=" in href:
                    qs = parse_qs(urlparse(href).query)
                    href = unquote((qs.get("uddg") or [href])[0])
                if host not in href:
                    continue
                key = href.split("?")[0]
                if key in seen:
                    continue
                seen.add(key)
                price_match = price_pattern.search(snippet) or price_pattern.search(title)
                price = float(price_match.group(1)) if price_match else 0.0
                results.append(
                    {
                        "name": (title or query)[:200],
                        "sku": "",
                        "price": price,
                        "url": href[:500],
                        "source": "website_search",
                        "material_id": False,
                    }
                )
                if len(results) >= limit:
                    return results
        return results

    def _market_estimate_hits(self, query, limit):
        """Fallback priced suggestions when live retailer pages block bots."""
        catalog = [
            ("stucco", "Stucco mix bag", 8.5, "bag", "/p/stucco-mix"),
            ("bonding", "Concrete bonding adhesive", 24.0, "gallon", "/p/bonding-agent"),
            ("mesh", "Fiberglass stucco mesh", 18.0, "roll", "/p/stucco-mesh"),
            ("fastener", "Stucco fasteners / washers", 12.0, "box", "/p/fasteners"),
            ("tape", "Painter's tape", 6.5, "roll", "/p/tape"),
            ("scaffold", "Scaffolding rental day", 85.0, "day", "/p/scaffold"),
            ("paint", "Exterior paint gallon", 32.0, "gallon", "/p/paint"),
            ("primer", "Exterior primer gallon", 28.0, "gallon", "/p/primer"),
            ("lumber", "2x4 stud 8ft", 4.2, "each", "/p/2x4"),
            ("plywood", "Plywood 4x8 sheet", 28.0, "sheet", "/p/plywood"),
            ("cement", "Portland cement bag", 12.0, "bag", "/p/cement"),
            ("sand", "Play sand bag", 5.5, "bag", "/p/sand"),
        ]
        q = (query or "").lower()
        host = self._site_host() or "example.com"
        base = (self.website or f"https://www.{host}").rstrip("/")
        # Slight supplier variance so HD vs Lowe's differ for picker UX.
        bump = 1.0
        if "lowe" in (self.code or "").lower() or "lowe" in (self.name or "").lower():
            bump = 1.04
        elif "home" in (self.code or "").lower() or "depot" in (self.name or "").lower():
            bump = 0.98
        hits = []
        for key, name, price, uom, path in catalog:
            if key in q or any(tok in name.lower() for tok in q.split() if len(tok) > 2):
                hits.append(
                    {
                        "name": f"{name} ({uom})",
                        "sku": "",
                        "price": round(price * bump, 2),
                        "url": f"{base}{path}",
                        "source": "website_search",
                        "material_id": False,
                    }
                )
            if len(hits) >= limit:
                break
        if not hits and q:
            # Generic estimate so search always returns something selectable.
            hits.append(
                {
                    "name": query[:120],
                    "sku": "",
                    "price": round(15.0 * bump, 2),
                    "url": f"{base}/s/{quote_plus(query)}",
                    "source": "website_search",
                    "material_id": False,
                }
            )
        return hits[:limit]

    @api.model
    def search_referential_shopping(self, query, limit=4):
        """Google Shopping without retailer filter — referential unit prices (1C)."""
        self_rec = self.browse() if not self else self[:1]
        if not self_rec:
            self_rec = self.search([("active", "=", True)], limit=1)
        if not self_rec:
            return []
        api_key = self_rec._serpapi_key()
        if not api_key:
            return []
        query = (query or "").strip()
        if not query:
            return []
        payload = self_rec._serpapi_json(
            urlencode(
                {
                    "engine": "google_shopping",
                    "q": query,
                    "api_key": api_key,
                    "num": max(limit * 3, 10),
                    "hl": "en",
                    "gl": "us",
                }
            )
        )
        results = []
        seen = set()
        for item in payload.get("shopping_results") or []:
            if not isinstance(item, dict):
                continue
            price = self_rec._price_from_serpapi_item(item)
            title = (item.get("title") or "")[:200]
            link = (item.get("link") or item.get("product_link") or "").strip()
            if price <= 0 or len(title) < 8:
                continue
            if not link.startswith("http"):
                # SerpAPI often puts product_id; use Google shopping immersion URL as proof.
                product_id = item.get("product_id") or item.get("token") or ""
                if product_id:
                    link = f"https://www.google.com/search?tbm=shop&q={quote_plus(title[:80])}"
                else:
                    continue
            key = link.split("?")[0] + title[:40]
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "name": title,
                    "sku": "",
                    "price": price,
                    "url": link[:500],
                    "source": "referential",
                    "estimated": True,
                    "supplier_id": False,
                    "supplier_name": item.get("source") or "Google Shopping",
                    "material_id": False,
                }
            )
            if len(results) >= limit:
                break
        return results

    @api.model
    def search_all_providers(self, query, supplier_ids=None, limit=20, location=None):
        domain = [("active", "=", True)]
        if supplier_ids:
            domain.append(("id", "in", supplier_ids))
        suppliers = self.search(domain)
        # Do not append ZIP/city to retailer queries — it breaks HD/Lowes matching.
        del location
        search_query = (query or "").strip()
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
