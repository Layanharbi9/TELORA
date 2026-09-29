"""Calculation-only tests: canonical lookup and exact financial arithmetic."""
import json
import unittest
from decimal import Decimal, localcontext

from backend.schemas.search import CandidatePool, PackageCandidate, AddonCandidate
from backend.schemas.solution import CalculationResult
from backend.tools.calculation_tool import calculate_solution


class CalculationToolTests(unittest.TestCase):
    def setUp(self):
        self.pool = CandidatePool(search_status="SUCCESS", package_candidates=[PackageCandidate(
            package_id="P1", name="Base", price_sar=0.1, matched_needs=[], match_reasons=[],
            capabilities={"data_gb": 10, "streaming_benefit": "Included"})], addon_candidates=[
                AddonCandidate(addon_id=key, name=key, price_sar=price, matched_needs=[], match_reasons=[],
                    capabilities={"data_gb": "Unlimited", "category": "Data"})
                for key, price in (("A1", 0.2), ("A2", 0.3), ("A3", 1))])

    def calculate(self, addons=None, budget=1):
        return calculate_solution(self.pool, "P1", [] if addons is None else addons, budget)

    def test_canonical_resolution_order_and_capabilities_remain_separate(self):
        before = self.pool.model_dump()
        result = self.calculate(["A2", "A1"])
        self.assertEqual(result.package, self.pool.package_candidates[0])
        self.assertEqual([item.addon_id for item in result.addons], ["A2", "A1"])
        self.assertEqual(result.package.capabilities["data_gb"], 10)
        self.assertEqual(result.addons[0].capabilities["data_gb"], "Unlimited")
        result.package.capabilities["data_gb"] = 99
        result.addons[0].capabilities["category"] = "changed"
        self.assertEqual(self.pool.model_dump(), before)

    def test_zero_one_two_addons_totals_and_budget_difference(self):
        for ids, total in (([], "0.1"), (["A1"], "0.3"), (["A1", "A2"], "0.6")):
            with self.subTest(ids=ids):
                result = self.calculate(ids)
                self.assertEqual(result.total_price_sar, Decimal(total))
                self.assertEqual(result.budget_difference_sar, Decimal(total) - 1)
        self.assertEqual(self.calculate(["A1"], 0.3).budget_difference_sar, 0)
        self.assertEqual(self.calculate(["A1"], 0.1).budget_difference_sar, Decimal("0.2"))

    def test_unknown_and_invalid_ids(self):
        for package, addons in (("missing", []), ("P1", ["missing"]), (" ", []),
                                ("P1", [""]), ("P1", [None]), ("P1", "A1")):
            with self.subTest(package=package, addons=addons), self.assertRaises(ValueError):
                calculate_solution(self.pool, package, addons, 1)

    def test_duplicate_addons_and_maximum(self):
        for ids in (["A1", "A1"], ["A1", "A2", "A3"]):
            with self.assertRaises(ValueError):
                self.calculate(ids)

    def test_ambiguous_catalog_ids_including_unselected_records(self):
        for field in ("package_candidates", "addon_candidates"):
            with self.subTest(field=field):
                pool = self.pool.model_copy(deep=True)
                records = getattr(pool, field)
                records.append(records[-1].model_copy(deep=True))
                with self.assertRaisesRegex(ValueError, "Ambiguous duplicate"):
                    calculate_solution(pool, "P1", [], 1)

    def test_invalid_selected_prices(self):
        for field in ("package_candidates", "addon_candidates"):
            for price in (-1, float("nan"), float("inf"), float("-inf"), "invalid", None, True):
                with self.subTest(field=field, price=price):
                    pool = self.pool.model_copy(deep=True)
                    # Existing candidate models allow assignment without validation.
                    getattr(pool, field)[0].price_sar = price
                    with self.assertRaises(ValueError):
                        calculate_solution(pool, "P1", ["A1"], 1)
        self.pool.package_candidates[0].price_sar = 0
        self.assertEqual(self.calculate().total_price_sar, 0)

    def test_invalid_budget(self):
        for budget in (0, -1, float("nan"), float("inf"), "invalid", None, True):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                self.calculate(budget=budget)

    def test_decimal_arithmetic_independent_of_ambient_precision(self):
        self.pool.package_candidates[0].price_sar = 1e20
        with localcontext() as context:
            context.prec = 2
            result = self.calculate(["A1"], budget=Decimal("100000000000000000000.1"))
        self.assertEqual(result.total_price_sar, Decimal("100000000000000000000.2"))
        self.assertEqual(result.budget_difference_sar, Decimal("0.1"))

    def test_json_result_roundtrip_preserves_decimal_values(self):
        result = self.calculate(["A1"])
        payload = json.loads(json.dumps(result.model_dump(mode="json")))
        self.assertEqual(payload["total_price_sar"], "0.3")
        self.assertEqual(CalculationResult.model_validate(payload), result)


if __name__ == "__main__":
    unittest.main()
