"""Multi-solution QUALITY CHECK through the existing single-review function.

Quality Check is fully deterministic (no LLM call), so these tests exercise
review_solution's deterministic integrity checks and per-strategy failure
isolation directly, without any mocked model.
"""
import unittest
from copy import deepcopy
from unittest.mock import patch
from backend.agents import solution_design_agent as agent
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import CandidatePool, PackageCandidate
from backend.schemas.solution import CalculatedDesign, SolutionStrategy
from backend.schemas.state import ToolTrace, StageTrace
from backend.tools.calculation_tool import calculate_solution


class MultiReviewTests(unittest.TestCase):
    def setUp(self):
        self.requirements = Requirements(NeedSelection('Calls', 'Medium'), None, None, 100)
        self.pool = CandidatePool(search_status='SUCCESS', addon_candidates=[], package_candidates=[
            PackageCandidate(package_id='P1', name='Base', price_sar=110, matched_needs=[],
                             match_reasons=[], capabilities={'voice_minutes': 500})])
        calculation = calculate_solution(self.pool, 'P1', [], 100)
        results = []
        for key in ('S3', 'S1', 'S2'):
            strategy = SolutionStrategy(strategy_id=key, decision_policy='REQUIREMENT_FIT',
                intent=f'Calling intent {key}', focus_priorities=[1], intended_tradeoffs=[])
            design = CalculatedDesign(strategy=strategy, policy_rationale='P1 meets the calling target within budget.',
                package_id='P1', addon_ids=[],
                total_price_sar=calculation.total_price_sar, preferred_budget_sar=calculation.preferred_budget_sar,
                budget_difference_sar=calculation.budget_difference_sar)
            trace = agent.DesignRuntimeTrace(stages=[StageTrace('DESIGN_FINAL', 'SUCCESS', 0, {})],
                tool_traces=[ToolTrace('calculate_solution', {}, calculation.model_dump(mode='json'), 'SUCCESS', 1)])
            results.append(agent.StrategyDesignResult(design, trace))
        self.designs = agent.MultipleStrategyDesignResult(results)

    def test_order_composition_prices_and_independent_reviews(self):
        for count in (1, 3):
            designs = agent.MultipleStrategyDesignResult(self.designs.results[:count])
            before = deepcopy(designs)
            with patch.object(agent, 'review_solution', wraps=agent.review_solution) as review, \
                 patch.object(agent, 'calculate_solution', side_effect=AssertionError('Do not recalculate')):
                result = agent.quality_check(self.requirements, self.pool, designs)
            self.assertEqual(review.call_count, count)
            self.assertEqual(set(vars(result)), {'solutions', 'review_traces', 'failures'})
            self.assertEqual([s.strategy.strategy_id for s in result.solutions], ['S3', 'S1', 'S2'][:count])
            for original, solution in zip(designs.results, result.solutions):
                self.assertEqual(solution.strategy, original.design.strategy)
                self.assertEqual(solution.package_id, original.design.package_id)
                self.assertEqual(solution.addon_ids, original.design.addon_ids)
                self.assertEqual((solution.total_price_sar, solution.preferred_budget_sar, solution.budget_difference_sar), (110, 100, 10))
                self.assertEqual(solution.review.budget_fit, 'ABOVE')
                key = solution.strategy.strategy_id
                self.assertEqual(solution.review.needs_coverage[0].coverage, 'COVERED')
                self.assertEqual(result.review_traces[key][0].details['strategy_id'], key)
                self.assertEqual(result.review_traces[key][0].details['review'], solution.review.model_dump(mode='json'))
                self.assertNotIn('llm_retry', result.review_traces[key][0].details)
            self.assertEqual(designs, before)
            if count == 3:
                self.assertIsNot(result.solutions[0].review, result.solutions[1].review)

    def test_integrity_failure_drops_only_that_strategy_siblings_still_reviewed(self):
        # S1's (middle strategy's) calculation is corrupted; S3 (earlier) and
        # S2 (later) are siblings and must still be reviewed -- only S1 is dropped.
        designs = deepcopy(self.designs)
        designs.results[1].trace.tool_traces[0].output['total_price_sar'] = '999'
        result = agent.quality_check(self.requirements, self.pool, designs)
        self.assertEqual([s.strategy.strategy_id for s in result.solutions], ['S3', 'S2'])
        self.assertEqual([f.strategy_id for f in result.failures], ['S1'])
        failure = result.failures[0]
        self.assertEqual([s.status for s in failure.trace[-2:]], ['ERROR', 'DROPPED'])
        self.assertEqual(failure.trace[-2].details['error_type'], 'ValueError')
        self.assertNotIn('llm_retry', failure.trace[-2].details)
        self.assertIs(result.review_traces['S1'][-2], failure.trace[-2])
        self.assertEqual(result.review_traces['S1'][-1].details['strategy_id'], 'S1')
        self.assertEqual(result.solutions[-1].strategy.strategy_id, 'S2')

    def test_all_strategies_failing_integrity_check_raises_terminal_error_with_evidence(self):
        designs = deepcopy(self.designs)
        for design_result in designs.results:
            design_result.trace.tool_traces[0].output['total_price_sar'] = '999'
        with self.assertRaises(ValueError) as caught:
            agent.quality_check(self.requirements, self.pool, designs)
        error = caught.exception
        self.assertEqual({f.strategy_id for f in error.strategy_failures}, {'S3', 'S1', 'S2'})
        self.assertTrue(all(f.trace[-1].status == 'DROPPED' for f in error.strategy_failures))

    def test_missing_or_mismatched_calculation_drops_only_that_strategy(self):
        for missing in (True, False):
            designs = deepcopy(self.designs)
            if missing:
                designs.results[0].trace.tool_traces.clear()
            else:
                designs.results[0].trace.tool_traces[0].output['total_price_sar'] = '999'
            result = agent.quality_check(self.requirements, self.pool, designs)
            self.assertEqual([f.strategy_id for f in result.failures], ['S3'])
            self.assertEqual(result.failures[0].trace[-1].status, 'DROPPED')
            self.assertEqual(result.review_traces['S3'][-1].status, 'DROPPED')
            # S3's failure is deterministic; S1 and S2 are unaffected siblings
            # and are still reviewed normally.
            self.assertEqual([s.strategy.strategy_id for s in result.solutions], ['S1', 'S2'])


if __name__ == '__main__':
    unittest.main()
