#!/usr/bin/env python3
"""Local unit checks for Analyze materials qty-only / repair BOM helpers (no Odoo DB)."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
PRICING_PATH = ROOT / "custom_addons/bizflow_proposals/models/services/pricing.py"


def _load_pricing():
    odoo = types.ModuleType("odoo")
    exc = types.ModuleType("odoo.exceptions")

    class UserError(Exception):
        pass

    exc.UserError = UserError
    sys.modules["odoo"] = odoo
    sys.modules["odoo.exceptions"] = exc

    pkg = types.ModuleType("bf_pricing")
    services = types.ModuleType("bf_pricing.services")
    llm = types.ModuleType("bf_pricing.services.llm")
    plan_render = types.ModuleType("bf_pricing.services.plan_render")
    sys.modules["bf_pricing"] = pkg
    sys.modules["bf_pricing.services"] = services
    sys.modules["bf_pricing.services.llm"] = llm
    sys.modules["bf_pricing.services.plan_render"] = plan_render
    pkg.services = services
    services.llm = llm
    services.plan_render = plan_render

    spec = importlib.util.spec_from_file_location(
        "bf_pricing.services.pricing", PRICING_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


pricing = _load_pricing()


class FakeProposal:
    def __init__(self, brief="", description="", work_brief="", project_type="", content=None):
        self.brief = brief
        self.description = description
        self.work_brief = work_brief
        self.project_type = project_type
        self.content = content or {}


class PricingHelpersTest(unittest.TestCase):
    def test_area_hint_picks_largest_sq_ft(self):
        p = FakeProposal(
            brief=(
                "El área a trabajar es de **250 pies cuadrados (sq ft)**.\n"
                "Volver a aplicar stucco en los **350 sq ft**."
            )
        )
        self.assertEqual(pricing._area_hint(p), 350.0)

    def test_stucco_mix_not_bonding_agent(self):
        self.assertTrue(
            pricing._is_stucco_mix_item(
                {"name": "Stucco Mix", "query": "stucco mix bag", "category": "concrete"}
            )
        )
        self.assertFalse(
            pricing._is_stucco_mix_item(
                {
                    "name": "Concrete Bonding Agent",
                    "query": "concrete bonding agent for stucco",
                    "category": "concrete",
                }
            )
        )

    def test_sanitize_bumps_stucco_bags_not_bonding(self):
        bags = pricing._sanitize_material_quantity(
            {"name": "Stucco Mix", "query": "stucco mix", "quantity": 12, "uom": "bag"},
            350.0,
        )
        self.assertGreaterEqual(bags, 19.0)
        bond = pricing._sanitize_material_quantity(
            {
                "name": "Concrete Bonding Agent",
                "query": "bonding agent for stucco",
                "quantity": 2,
                "uom": "bucket",
            },
            350.0,
        )
        self.assertEqual(bond, 2.0)

    def test_repair_bom_seed_fills_thin_takeoff(self):
        p = FakeProposal(
            brief="Reparar chimenea con stucco 350 sq ft",
            project_type="Chimney",
        )
        materials = pricing._ensure_repair_bom_minimum(
            p,
            [{"name": "Stucco Mix", "query": "stucco mix", "quantity": 12, "uom": "bag"}],
            lang_es=False,
        )
        names = " ".join(m["name"].lower() for m in materials)
        self.assertIn("bonding", names)
        self.assertIn("mesh", names)
        self.assertIn("bead", names)
        bond = next(m for m in materials if "bonding" in m["name"].lower())
        self.assertEqual(bond["uom"], "gallon")
        self.assertLessEqual(float(bond["quantity"]), 5.0)

    def test_price_candidate_qty_only_is_addable(self):
        env = MagicMock()
        suppliers = MagicMock()
        suppliers.__bool__.return_value = False
        cand = pricing._price_candidate(
            env,
            suppliers,
            {
                "name": "Stucco Mix",
                "query": "stucco mix bag",
                "quantity": 12,
                "uom": "bag",
                "category": "concrete",
            },
            set(),
            0,
            area_hint=350.0,
        )
        self.assertTrue(cand["options"])
        self.assertTrue(cand["unverified"])
        self.assertEqual(cand["options"][0]["source"], "qty")
        self.assertGreaterEqual(float(cand["quantity"]), 19.0)

    def test_apply_accepts_qty_only_candidate(self):
        """Smoke: apply_pricing_selection creates material lines without URL/price."""
        Line = MagicMock()
        created = []

        def _create(vals):
            created.extend(vals if isinstance(vals, list) else [vals])
            return MagicMock()

        Line.create.side_effect = _create
        env = MagicMock()
        env.__getitem__.side_effect = lambda k: Line if k == "bizflow.proposal.line" else MagicMock()
        env._ = lambda s: s

        proposal = MagicMock()
        proposal.ensure_one = MagicMock()
        proposal.env = env
        proposal.language = "en"
        proposal.line_ids = []
        proposal.content = {"pricing": {}, "legal": {}}
        proposal.invalidate_recordset = MagicMock()
        proposal.sync_utility_from_lines = MagicMock()
        proposal.to_editor_payload.return_value = {"ok": True}
        proposal.write = MagicMock()

        candidates = [
            {
                "name": "Stucco Mix",
                "quantity": 19.25,
                "uom": "bag",
                "options": [
                    {
                        "key": "qty-0",
                        "name": "Stucco Mix",
                        "price": 0,
                        "url": "",
                        "source": "qty",
                        "estimated": True,
                        "recommended": True,
                    }
                ],
                "selected_key": "qty-0",
                "unverified": True,
            }
        ]
        result = pricing.apply_pricing_selection(
            proposal, candidates=candidates, labor_items=[], exclusions=[]
        )
        self.assertTrue(result.get("ok"))
        mat_lines = [c for c in created if c.get("line_type") == "material"]
        self.assertEqual(len(mat_lines), 1)
        self.assertEqual(mat_lines[0]["name"], "Stucco Mix")
        self.assertEqual(mat_lines[0]["notes"], "qty-only")
        self.assertEqual(mat_lines[0]["price_unit"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
