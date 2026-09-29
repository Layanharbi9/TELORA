"""Cross-solution comparison contracts over already-valid (guardrail-passing) solutions."""
import json
import unittest
from copy import deepcopy
from unittest.mock import Mock
from backend.agents.solution_design_agent import compare_solutions
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import CandidatePool, PackageCandidate, AddonCandidate
from backend.schemas.solution import CandidateSolution, SolutionComparison


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.requirements = Requirements(NeedSelection('Calls', 'Medium'), None, None, 100)
        self.pool = CandidatePool(search_status='SUCCESS', package_candidates=[PackageCandidate(
            package_id=key, name=key, price_sar=price, matched_needs=[], match_reasons=[],
            capabilities={'voice_minutes': minutes}) for key, price, minutes in [('P1', 80, 200), ('P2', 110, 500)]],
            addon_candidates=[AddonCandidate(addon_id=key, name=key, price_sar=0, matched_needs=[], match_reasons=[])
                              for key in ('A1', 'A2')])

    def solution(self, strategy, package='P1', addons=None):
        price = 80 if package == 'P1' else 110
        return CandidateSolution(strategy=dict(strategy_id=strategy, decision_policy='REQUIREMENT_FIT',
            intent=f'Calling intent {strategy}',
            focus_priorities=[1], intended_tradeoffs=[]), package_id=package, addon_ids=addons or [],
            total_price_sar=price, preferred_budget_sar=100, budget_difference_sar=price-100,
            review=dict(needs_coverage=[dict(need='Calls', need_priority=1, requested_level='Medium',
                coverage='COVERED', evidence='Calling allowance in selected package')],
                budget_fit='WITHIN' if price <= 100 else 'ABOVE', budget_summary='Observed pricing',
                trade_off_summary='Observed trade-off'))

    def fake(self, *outputs):
        model = Mock()
        model.with_structured_output.return_value.invoke.side_effect = outputs
        return model

    def comparison(self, key_tradeoff='No meaningful difference.'):
        return dict(needs_coverage_summary='Both solutions cover Calls.',
                    priority_fit_summary='Primary need protected in both solutions.',
                    budget_fit_summary='placeholder text the caller must not trust',
                    key_tradeoff=key_tradeoff)

    def test_single_is_deterministic(self):
        model = Mock()
        traces = []
        result = compare_solutions(self.requirements, self.pool, [self.solution('S1')], model, stage_traces=traces)
        self.assertIsInstance(result, SolutionComparison)
        self.assertIn('S1', result.key_tradeoff)
        self.assertIn('S1', result.needs_coverage_summary)
        self.assertIn('80.0 SAR', result.budget_fit_summary)
        model.with_structured_output.assert_not_called()
        self.assertEqual(traces[0].details['llm_retry']['attempts'], 0)

    def test_budget_fit_summary_is_always_deterministic_not_llm_authored(self):
        solutions = [self.solution('S1'), self.solution('S2', 'P2')]
        output = self.comparison()
        model = self.fake(output)
        traces = []
        result = compare_solutions(self.requirements, self.pool, solutions, model, stage_traces=traces)
        # Python overwrites budget_fit_summary regardless of what the model returned.
        self.assertNotEqual(result.budget_fit_summary, output['budget_fit_summary'])
        self.assertIn('S1: 80.0 SAR total, 20.0 SAR under the 100.0 SAR preferred budget (WITHIN).', result.budget_fit_summary)
        self.assertIn('S2: 110.0 SAR total, 10.0 SAR over the 100.0 SAR preferred budget (ABOVE).', result.budget_fit_summary)

    def test_grounded_context_no_input_changes(self):
        solutions = [self.solution('S1'), self.solution('S2', 'P2')]
        before = deepcopy(solutions)
        output = self.comparison('S2 offers 500 minutes versus S1 with 200, at 110 SAR versus 80 SAR.')
        model = self.fake(output)
        traces = []
        result = compare_solutions(self.requirements, self.pool, solutions, model, stage_traces=traces)
        self.assertEqual(result.key_tradeoff, output['key_tradeoff'])
        self.assertEqual(set(result.model_dump()),
                         {'needs_coverage_summary', 'priority_fit_summary', 'budget_fit_summary', 'key_tradeoff'})
        self.assertEqual(solutions, before)
        context = json.loads(model.with_structured_output.return_value.invoke.call_args.args[0][1].content)
        self.assertEqual(context['solution_ids'], ['S1', 'S2'])
        self.assertEqual([s['solution_id'] for s in context['solutions']], ['S1', 'S2'])
        self.assertEqual(context['solutions'][1]['package']['capabilities']['voice_minutes'], 500)
        self.assertEqual(context['solutions'][1]['needs_coverage'][0]['evidence'], 'Calling allowance in selected package')
        self.assertEqual(context['objective_facts'], {'solution_count': 2})
        self.assertEqual(traces[0].details['comparison'], result.model_dump())
        self.assertEqual(traces[0].details['llm_retry']['retry_count'], 0)

    def test_converged_strategy_ids_are_not_presented_as_separate_solutions(self):
        merged = self.solution('S1').model_copy(update={'also_converged_strategies': [self.solution('S9').strategy]})
        model = self.fake(self.comparison())
        compare_solutions(self.requirements, self.pool, [merged, self.solution('S2', 'P2')], model)
        payload = model.with_structured_output.return_value.invoke.call_args.args[0][1].content
        self.assertNotIn('S9', payload)
        self.assertNotIn('also_converged_strategies', payload)
        self.assertEqual(json.loads(payload)['solution_ids'], ['S1', 'S2'])

    def test_invalid_output_and_failure_evidence(self):
        model = self.fake({**self.comparison(), 'winner': 'S1'}, {})
        traces = []
        with self.assertLogs('backend.services.llm_retry', level='ERROR'), self.assertRaises(ValueError) as caught:
            compare_solutions(self.requirements, self.pool, [self.solution('S1'), self.solution('S2')], model, stage_traces=traces)
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)
        self.assertIs(caught.exception.comparison_trace, traces[0])
        self.assertEqual(traces[0].status, 'ERROR')
        self.assertEqual(traces[0].details['llm_retry']['attempts'], 2)

    def test_unknown_composition_and_requirement_mismatch_fail_before_llm(self):
        for field, value in (('package_id', 'invented'), ('preferred_budget_sar', 999)):
            solution = self.solution('S1')
            setattr(solution, field, value)
            model = Mock()
            with self.assertRaises(ValueError):
                compare_solutions(self.requirements, self.pool, [solution], model)
            model.with_structured_output.assert_not_called()


if __name__ == '__main__':
    unittest.main()
