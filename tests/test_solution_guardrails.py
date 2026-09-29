"""Deterministic guardrails, without model or persistence calls."""
import unittest

from backend.guardrails.solution_guardrails import check_solution_guardrails
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import CandidatePool, PackageCandidate, AddonCandidate
from backend.schemas.solution import CandidateSolution, SolutionRecommendation
from backend.tools.calculation_tool import calculate_solution
from tests.test_solution_schemas import output


class SolutionGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.req = Requirements(NeedSelection('Data', 'High'), None, None, 320)
        self.pool = CandidatePool(search_status='SUCCESS', package_candidates=[
            PackageCandidate(package_id='P1', name='Base', price_sar=180,
                             capabilities={'data_gb': 30}, matched_needs=[], match_reasons=[])],
            addon_candidates=[AddonCandidate(addon_id='A1', name='Extra', price_sar=10,
                                            capabilities={'data_gb': 5}, matched_needs=[], match_reasons=[])])
        self.solutions = [CandidateSolution.model_validate(item) for item in output(2)["solutions"]]
        self.evidence = {}
        self.set_price(180)
        self.recommendation = SolutionRecommendation(recommended_solution_id='S0', why_recommended='Data fit')

    def set_price(self, price):
        self.pool.package_candidates[0].price_sar = price
        for solution in self.solutions:
            calculation = calculate_solution(self.pool, solution.package_id, solution.addon_ids, 320)
            self.evidence[solution.strategy.strategy_id] = calculation
            solution.total_price_sar = float(calculation.total_price_sar)
            solution.preferred_budget_sar = 320
            solution.budget_difference_sar = float(calculation.budget_difference_sar)
            solution.review.budget_fit = 'ABOVE' if price > 320 else 'WITHIN'

    def check(self):
        self.traces = []
        return check_solution_guardrails(self.req, self.pool, self.solutions, self.evidence,
                                         stage_traces=self.traces)

    def test_valid_all_checks_pass_and_trace_serializes(self):
        results = self.check()
        self.assertEqual([r.strategy_id for r in results], ['S0', 'S1'])
        self.assertTrue(all(r.overall == 'PASS' for r in results))
        self.assertEqual(len(results[0].checks), 4)
        self.assertEqual(results[0].hard_budget_limit_sar, 368)
        self.assertEqual(self.traces[0].details, results[0].model_dump(mode='json'))

    def test_unknown_package_and_addon(self):
        for field, value in (('package_id', 'UNKNOWN'), ('addon_ids', ['UNKNOWN'])):
            with self.subTest(field=field):
                self.setUp()
                setattr(self.solutions[0], field, value)
                result = self.check()[0]
                self.assertEqual(result.overall, 'FAIL')
                self.assertEqual(result.checks['catalog_validity'].status, 'FAIL')
                self.assertIsNotNone(result.checks['catalog_validity'].reason)

    def test_invalid_compositions_return_fail_not_exception(self):
        for field, value in (('package_id', ''), ('package_id', ['P1','P2']),
                             ('addon_ids', ['A1'] * 3), ('addon_ids', ['A1','A1'])):
            with self.subTest(value=value):
                self.setUp()
                setattr(self.solutions[0], field, value)
                result = self.check()[0]
                self.assertEqual(result.checks['composition_validity'].status, 'FAIL')
                self.assertEqual(len(result.checks), 4)

    def test_incorrect_total_fails_but_other_results_preserved(self):
        self.solutions[0].total_price_sar = 1
        result = self.check()[0]
        self.assertEqual(result.overall, 'FAIL')
        self.assertEqual(result.checks['price_integrity'].status, 'FAIL')
        self.assertEqual(sum(c.status == 'PASS' for c in result.checks.values()), 3)

    def test_budget_boundary_and_overage(self):
        for price, expected in ((368, 'PASS'), (368.01, 'FAIL')):
            with self.subTest(price=price):
                self.set_price(price)
                result = self.check()[0]
                self.assertEqual(result.checks['budget_hard_limit'].status, expected)
                self.assertEqual(result.checks['price_integrity'].status, 'PASS')

    def test_catalog_evidence_mismatch_and_missing_evidence(self):
        self.evidence['S0'].package.capabilities['data_gb'] = 999
        del self.evidence['S1']
        results = self.check()
        self.assertTrue(all(r.checks['catalog_validity'].status == 'FAIL' for r in results))

    def test_catalog_component_price_mismatch(self):
        self.evidence['S0'].package.price_sar = 1
        self.assertEqual(self.check()[0].checks['catalog_validity'].status, 'FAIL')

    def test_no_mutation_even_when_recommended_solution_fails(self):
        self.set_price(400)
        before = ([s.model_dump_json() for s in self.solutions], self.recommendation.model_dump_json(),
                  self.pool.model_dump_json(), {k:v.model_dump_json() for k,v in self.evidence.items()})
        results = self.check()
        self.assertEqual(results[0].strategy_id, self.recommendation.recommended_solution_id)
        self.assertEqual(results[0].overall, 'FAIL')
        self.assertEqual(before, ([s.model_dump_json() for s in self.solutions], self.recommendation.model_dump_json(),
                         self.pool.model_dump_json(), {k:v.model_dump_json() for k,v in self.evidence.items()}))

    def test_hard_limit_uses_original_budget_not_tampered_solution_budget(self):
        self.set_price(400)
        self.solutions[0].preferred_budget_sar = 1000
        self.assertEqual(self.check()[0].checks['budget_hard_limit'].status, 'FAIL')


if __name__ == '__main__':
    unittest.main()
