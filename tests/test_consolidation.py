"""Identical-composition consolidation: detection, merge, and trace evidence."""
import unittest
from backend.agents.solution_design_agent import consolidate_converged_strategies
from backend.schemas.solution import CandidateSolution


class ConsolidationTests(unittest.TestCase):
    def solution(self, strategy, package='P1', addons=None, coverage='COVERED', price=80):
        return CandidateSolution(strategy=dict(strategy_id=strategy, decision_policy='REQUIREMENT_FIT',
            intent=f'Calling intent {strategy}',
            focus_priorities=[1], intended_tradeoffs=[]), package_id=package, addon_ids=addons or [],
            total_price_sar=price, preferred_budget_sar=100, budget_difference_sar=price - 100,
            review=dict(needs_coverage=[dict(need='Calls', need_priority=1, requested_level='Medium',
                coverage=coverage, evidence='Calling allowance in selected package')],
                budget_fit='WITHIN' if price <= 100 else 'ABOVE', budget_summary='Observed pricing',
                trade_off_summary='Observed trade-off'))

    def test_no_convergence_is_a_pure_pass_through(self):
        solutions = [self.solution('S1', 'P1'), self.solution('S2', 'P2')]
        traces = []
        result = consolidate_converged_strategies(solutions, stage_traces=traces)
        self.assertEqual(result, solutions)
        self.assertEqual([s.strategy.strategy_id for s in result], ['S1', 'S2'])
        self.assertTrue(all(s.also_converged_strategies == [] for s in result))
        self.assertEqual(traces, [])

    def test_agreeing_duplicate_merges_keeping_plan_order_representative(self):
        solutions = [self.solution('S1', 'P1'), self.solution('S2', 'P1')]
        traces = []
        result = consolidate_converged_strategies(solutions, stage_traces=traces)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].strategy.strategy_id, 'S1')
        self.assertEqual([s.strategy_id for s in result[0].also_converged_strategies], ['S2'])
        # The representative's own review/pricing are untouched, not merged/altered.
        self.assertEqual(result[0].review.needs_coverage[0].coverage, 'COVERED')
        self.assertEqual(len(traces), 1)
        details = traces[0].details
        self.assertEqual(traces[0].stage, 'CONSOLIDATE')
        self.assertEqual(details['kept_strategy_id'], 'S1')
        self.assertEqual(details['converged_strategy_ids'], ['S1', 'S2'])
        self.assertEqual(details['package_id'], 'P1')
        self.assertTrue(details['needs_coverage_agreement'])
        self.assertEqual(set(details['reviews']), {'S1', 'S2'})

    def test_disagreeing_reviews_still_merge_without_arbitration(self):
        solutions = [self.solution('S1', 'P1', coverage='COVERED'),
                     self.solution('S2', 'P1', coverage='NOT_COVERED')]
        traces = []
        result = consolidate_converged_strategies(solutions, stage_traces=traces)
        # No arbitration: the first-in-order representative's own (unchanged) review is kept as-is.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].strategy.strategy_id, 'S1')
        self.assertEqual(result[0].review.needs_coverage[0].coverage, 'COVERED')
        details = traces[0].details
        self.assertFalse(details['needs_coverage_agreement'])
        # Full evidence for both sides of the disagreement is preserved, not discarded.
        self.assertEqual(details['reviews']['S1']['needs_coverage'][0]['coverage'], 'COVERED')
        self.assertEqual(details['reviews']['S2']['needs_coverage'][0]['coverage'], 'NOT_COVERED')

    def test_partial_convergence_preserves_order_and_only_traces_the_merged_group(self):
        solutions = [self.solution('S1', 'P1'), self.solution('S2', 'P2'), self.solution('S3', 'P1')]
        traces = []
        result = consolidate_converged_strategies(solutions, stage_traces=traces)
        self.assertEqual([s.strategy.strategy_id for s in result], ['S1', 'S2'])
        self.assertEqual([s.strategy_id for s in result[0].also_converged_strategies], ['S3'])
        self.assertEqual(result[1].also_converged_strategies, [])
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].details['converged_strategy_ids'], ['S1', 'S3'])

    def test_addon_order_does_not_defeat_identity(self):
        solutions = [self.solution('S1', 'P1', addons=['A1', 'A2']),
                     self.solution('S2', 'P1', addons=['A2', 'A1'])]
        result = consolidate_converged_strategies(solutions)
        self.assertEqual(len(result), 1)
        self.assertEqual([s.strategy_id for s in result[0].also_converged_strategies], ['S2'])

    def test_stage_traces_none_is_safe(self):
        solutions = [self.solution('S1', 'P1'), self.solution('S2', 'P1')]
        result = consolidate_converged_strategies(solutions)
        self.assertEqual(len(result), 1)
        self.assertEqual([s.strategy_id for s in result[0].also_converged_strategies], ['S2'])


if __name__ == '__main__':
    unittest.main()
