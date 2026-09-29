"""Single-solution QUALITY CHECK (review_solution): fully deterministic, no LLM call."""
import unittest
from copy import deepcopy
from unittest.mock import Mock
from backend.agents.solution_design_agent import _need_coverage_and_evidence, _trade_off_summary, review_solution
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import AddonCandidate, CandidatePool, PackageCandidate, search_needs_from_requirements
from backend.schemas.solution import DesignedSolution, NeedCoverage, SolutionStrategy, SolutionReview
from backend.tools.calculation_tool import calculate_solution


class SolutionReviewTests(unittest.TestCase):
    def setUp(self):
        self.requirements = Requirements(NeedSelection('Calls', 'Medium'), None, NeedSelection('Data', 'Low'), 100)
        self.pool = CandidatePool(search_status='SUCCESS', addon_candidates=[], package_candidates=[PackageCandidate(
            package_id='P1', name='Base', price_sar=110, matched_needs=[], match_reasons=[],
            capabilities={'voice_minutes': 500, 'data_gb': 5})])
        self.design = DesignedSolution(strategy=SolutionStrategy(strategy_id='S1', decision_policy='REQUIREMENT_FIT',
            intent='Protect calls', focus_priorities=[1], intended_tradeoffs=[]),
            policy_rationale='P1 meets the calling target within budget.', package_id='P1', addon_ids=[])

    def run_review(self, requirements=None, pool=None, design=None, calculation=None, **kwargs):
        requirements = requirements or self.requirements
        pool = pool or self.pool
        design = design or self.design
        calculation = calculation or calculate_solution(pool, design.package_id, design.addon_ids,
                                                          requirements.preferred_budget_sar)
        traces = []
        result = review_solution(requirements, pool, design, calculation, stage_traces=traces, **kwargs)
        return result, traces

    def test_deterministic_coverage_matches_grounded_capabilities(self):
        before = (self.design.model_dump(), self.pool.model_dump())
        result, traces = self.run_review()
        self.assertIsInstance(result, SolutionReview)
        self.assertEqual([(x.need, x.need_priority, x.requested_level.value, x.coverage) for x in result.needs_coverage],
                         [('Calls', 1, 'Medium', 'COVERED'), ('Data', 3, 'Low', 'COVERED')])
        self.assertEqual((self.design.model_dump(), self.pool.model_dump()), before)
        self.assertEqual(traces[0].details['review'], result.model_dump(mode='json'))
        self.assertEqual(traces[0].status, 'SUCCESS')
        self.assertNotIn('llm_retry', traces[0].details)

    def test_no_llm_call_is_made(self):
        # `llm` is accepted only to avoid call-site churn; it must never be used.
        model = Mock()
        self.run_review(llm=model)
        model.with_structured_output.assert_not_called()
        model.invoke.assert_not_called()

    def test_budget_fit_and_summary_are_deterministic(self):
        for price, expected in ((80, 'WITHIN'), (100, 'WITHIN'), (110, 'ABOVE')):
            self.pool.package_candidates[0].price_sar = price
            result, _ = self.run_review()
            self.assertEqual(result.budget_fit, expected)
            self.assertIn(f'Total {price} SAR', result.budget_summary)

    def test_mismatched_observation_rejected_deterministically(self):
        for budget, changed in ((99, False), (100, True)):
            calculation = calculate_solution(self.pool, 'P1', [], budget)
            if changed:
                calculation.package.capabilities['voice_minutes'] = 999
            with self.assertRaises(ValueError):
                review_solution(self.requirements, self.pool, self.design, calculation)

    def test_integrity_failure_records_error_trace_without_llm_retry(self):
        calculation = calculate_solution(self.pool, 'P1', [], 99)  # mismatched budget
        traces = []
        with self.assertRaises(ValueError) as caught:
            review_solution(self.requirements, self.pool, self.design, calculation, stage_traces=traces)
        self.assertEqual(traces[0].status, 'ERROR')
        self.assertNotIn('llm_retry', traces[0].details)
        self.assertIs(caught.exception.review_trace, traces[0])

    def _gaming_scenario(self, package_gaming='None', addon_gaming=None):
        requirements = Requirements(NeedSelection('Calls', 'Medium'), None, NeedSelection('Gaming', 'Low'), 100)
        addon_candidates = []
        addon_ids = []
        if addon_gaming is not None:
            addon_candidates.append(AddonCandidate(addon_id='A1', name='Game Boost', price_sar=10,
                matched_needs=[], match_reasons=[], capabilities={'gaming_benefit': addon_gaming}))
            addon_ids = ['A1']
        pool = CandidatePool(search_status='SUCCESS', addon_candidates=addon_candidates, package_candidates=[
            PackageCandidate(package_id='P1', name='Base', price_sar=110, matched_needs=[], match_reasons=[],
                             capabilities={'voice_minutes': 500, 'gaming_benefit': package_gaming})])
        design = DesignedSolution(strategy=SolutionStrategy(strategy_id='S1', decision_policy='REQUIREMENT_FIT',
            intent='Protect calls', focus_priorities=[1], intended_tradeoffs=[]),
            policy_rationale='Composition addresses calling and gaming.', package_id='P1', addon_ids=addon_ids)
        calculation = calculate_solution(pool, 'P1', addon_ids, requirements.preferred_budget_sar)
        return requirements, pool, design, calculation

    def test_regression_addon_provided_gaming_is_covered(self):
        # The confirmed real bug: an add-on (not the package) provides Gaming.
        # The package alone does NOT support Gaming; only Game Boost does.
        requirements, pool, design, calculation = self._gaming_scenario(
            package_gaming='None', addon_gaming='Gaming Data')
        result, _ = self.run_review(requirements, pool, design, calculation)
        gaming = next(item for item in result.needs_coverage if item.need == 'Gaming')
        self.assertEqual(gaming.coverage, 'COVERED')
        self.assertIn('Game Boost', gaming.evidence)
        self.assertIn('gaming_benefit', gaming.evidence)
        calls = next(item for item in result.needs_coverage if item.need == 'Calls')
        self.assertEqual(calls.coverage, 'COVERED')
        self.assertIn('Every requested need has real catalog capability', result.trade_off_summary)

    def test_coverage_source_package_only(self):
        requirements, pool, design, calculation = self._gaming_scenario(package_gaming='Gaming Data', addon_gaming=None)
        result, _ = self.run_review(requirements, pool, design, calculation)
        gaming = next(item for item in result.needs_coverage if item.need == 'Gaming')
        self.assertEqual(gaming.coverage, 'COVERED')
        self.assertIn('Base (package)', gaming.evidence)

    def test_coverage_source_addon_only(self):
        requirements, pool, design, calculation = self._gaming_scenario(package_gaming='None', addon_gaming='Gaming Data')
        result, _ = self.run_review(requirements, pool, design, calculation)
        gaming = next(item for item in result.needs_coverage if item.need == 'Gaming')
        self.assertEqual(gaming.coverage, 'COVERED')
        self.assertIn('Game Boost (add-on)', gaming.evidence)

    def test_coverage_source_both_package_and_addon(self):
        requirements, pool, design, calculation = self._gaming_scenario(package_gaming='Gaming Benefit', addon_gaming='Gaming Data')
        result, _ = self.run_review(requirements, pool, design, calculation)
        gaming = next(item for item in result.needs_coverage if item.need == 'Gaming')
        self.assertEqual(gaming.coverage, 'COVERED')
        self.assertIn('Base (package)', gaming.evidence)
        self.assertIn('Game Boost (add-on)', gaming.evidence)

    def test_coverage_source_neither(self):
        requirements, pool, design, calculation = self._gaming_scenario(package_gaming='None', addon_gaming=None)
        result, _ = self.run_review(requirements, pool, design, calculation)
        gaming = next(item for item in result.needs_coverage if item.need == 'Gaming')
        self.assertEqual(gaming.coverage, 'NOT_COVERED')
        self.assertIn('No selected component provides Gaming', gaming.evidence)
        self.assertIn('does not provide capability for Gaming', result.trade_off_summary)

    def test_all_need_categories_use_the_existing_capability_rules(self):
        # Verifies _need_coverage_and_evidence agrees with _has_capability for
        # every currently supported need category; no semantics are changed.
        cases = [
            ('Data', {'data_gb': 5}, {'data_gb': 0}, True),
            ('Data', {'data_gb': 0}, {}, False),
            ('Calls', {'voice_minutes': 100}, {}, True),
            ('Calls', {'voice_minutes': 0}, {}, False),
            ('Gaming', {'gaming_benefit': 'Gaming Data'}, {}, True),
            ('Gaming', {'gaming_benefit': 'None'}, {}, False),
            ('Streaming', {'streaming_benefit': 'OTT Benefit'}, {}, True),
            ('Streaming', {'streaming_benefit': 'None'}, {}, False),
            ('Social Media', {'social_data_gb': 'Unlimited'}, {}, True),
            ('Social Media', {'social_data_gb': 0}, {}, False),
            ('Roaming', {'roaming_data_gb': 2}, {}, True),
            ('Roaming', {'roaming_data_gb': 0}, {}, False),
            ('International Calls', {'international_minutes': 100}, {}, True),
            ('International Calls', {'international_minutes': 0}, {}, False),
            ('Multiple Devices', {'multi_sim': 'Yes'}, {}, True),
            ('Multiple Devices', {'multi_sim': 'No'}, {}, False),
            ('Data Sharing', {'data_sharing': 'Yes'}, {}, True),
            ('Data Sharing', {'data_sharing': 'No'}, {}, False),
        ]
        for need_name, package_caps, addon_caps, expect_covered in cases:
            with self.subTest(need=need_name, package_caps=package_caps):
                requirements = Requirements(NeedSelection(need_name, 'Medium'), None, None, 100)
                need = search_needs_from_requirements(requirements)[0]
                package = PackageCandidate(package_id='P1', name='Base', price_sar=100,
                                           matched_needs=[], match_reasons=[], capabilities=package_caps)
                addons = [AddonCandidate(addon_id='A1', name='Addon', price_sar=1,
                                        matched_needs=[], match_reasons=[], capabilities=addon_caps)] if addon_caps else []
                coverage, evidence = _need_coverage_and_evidence(need, package, addons)
                self.assertEqual(coverage == 'COVERED', expect_covered)

    def test_trade_off_summary_lists_covered_and_not_covered_needs_by_priority(self):
        covered = [
            NeedCoverage(need='Data', need_priority=1, requested_level='Medium', coverage='COVERED', evidence='x'),
            NeedCoverage(need='Streaming', need_priority=2, requested_level='High', coverage='COVERED', evidence='x'),
            NeedCoverage(need='Gaming', need_priority=3, requested_level='Low', coverage='NOT_COVERED', evidence='x'),
        ]
        summary = _trade_off_summary(covered)
        self.assertIn('Data (priority 1)', summary)
        self.assertIn('Streaming (priority 2)', summary)
        self.assertIn('Gaming (priority 3)', summary)
        self.assertIn('does not provide capability for', summary)

    def test_trade_off_summary_when_everything_is_covered(self):
        all_covered = [
            NeedCoverage(need='Calls', need_priority=1, requested_level='Medium', coverage='COVERED', evidence='x'),
        ]
        summary = _trade_off_summary(all_covered)
        self.assertIn('Every requested need has real catalog capability', summary)
        self.assertNotIn('does not provide', summary)

    def test_review_reflects_actual_composition_regardless_of_design_rationale(self):
        # DESIGN's rationale claims Gaming isn't needed / no conflict exists,
        # but the composition actually leaves it uncovered. QC's own
        # deterministic trade_off_summary must reflect the real outcome
        # regardless of what the rationale says -- and the composition itself
        # (package_id/addon_ids) must stay unchanged.
        requirements, pool, design, calculation = self._gaming_scenario(package_gaming='None', addon_gaming=None)
        design = design.model_copy(update={
            'policy_rationale': 'Gaming does not require support here and there is no budget conflict.'})
        before = design.model_dump()
        result, _ = self.run_review(requirements, pool, design, calculation)
        self.assertEqual(design.model_dump(), before)
        self.assertEqual(design.package_id, 'P1')
        self.assertEqual(design.addon_ids, [])
        self.assertIn('Calls (priority 1)', result.trade_off_summary)
        self.assertIn('Gaming (priority 3)', result.trade_off_summary)
        self.assertIn('does not provide capability for', result.trade_off_summary)
        self.assertNotIn('does not require support', result.trade_off_summary)
        self.assertNotIn('no budget conflict', result.trade_off_summary)


if __name__ == '__main__':
    unittest.main()
