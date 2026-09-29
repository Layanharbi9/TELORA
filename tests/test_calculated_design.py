"""One-shot design/calculation integration; no live model or database."""
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pydantic import ValidationError
from backend.agents import solution_design_agent as agent
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import CandidatePool, PackageCandidate, AddonCandidate
from backend.schemas.solution import CalculatedDesign, CalculationResult, SolutionStrategy
from backend.tools.calculation_tool import calculate_solution


def ai_message(tool_calls):
    return SimpleNamespace(tool_calls=tool_calls)


def proposal_call(args, call_id="call-design"):
    return {"name": "DesignProposal", "args": args, "id": call_id}


class CalculatedDesignTests(unittest.TestCase):
    def setUp(self):
        self.requirements = Requirements(NeedSelection('Calls', 'Medium'), None, None, 100)
        self.strategy = SolutionStrategy(strategy_id='S1', decision_policy='REQUIREMENT_FIT',
                                        intent='Protect calling targets',
                                        focus_priorities=[1], intended_tradeoffs=[])
        self.pool = CandidatePool(search_status='SUCCESS', package_candidates=[PackageCandidate(
            package_id='P1', name='Base', price_sar=110, matched_needs=[], match_reasons=[],
            capabilities={'voice_minutes': 500})], addon_candidates=[AddonCandidate(
            addon_id='A1', name='Extra', price_sar=15, matched_needs=[], match_reasons=[],
            capabilities={'voice_minutes': 100})])
        self.proposal = dict(policy_rationale='P1 plus A1 meets the requested calling target within budget.',
                            package_id='P1', addon_ids=['A1'])
        self.model = self._build_model()

    def _build_model(self):
        # No genuine trade-off in this fixture: the model finalizes directly,
        # in one turn, without calling calculate_solution.
        model = Mock()
        model.bind_tools.return_value.invoke.return_value = ai_message([proposal_call(self.proposal)])
        return model

    def run_design(self):
        return agent.create_calculated_design(self.requirements, self.pool, self.strategy, llm=self.model)

    def test_selection_calculation_and_trace_with_no_above_budget_redesign(self):
        before = self.pool.model_dump()
        with patch.object(agent, 'create_basic_design', wraps=agent.create_basic_design) as basic, \
             patch.object(agent, 'calculate_solution', wraps=calculate_solution) as calculate:
            result = self.run_design()
            basic.assert_called_once_with(self.requirements, self.pool, self.strategy, llm=self.model)
            # No calculation requested in-loop, so the only calculate_solution call is the final one.
            calculate.assert_called_once_with(self.pool, package_id='P1', addon_ids=['A1'], preferred_budget_sar=100)
        self.assertEqual(self.model.bind_tools.return_value.invoke.call_count, 1)
        self.assertEqual(result.design.total_price_sar, Decimal('125'))
        self.assertEqual(result.design.preferred_budget_sar, Decimal('100'))
        self.assertEqual(result.design.budget_difference_sar, Decimal('25'))
        self.assertEqual(result.design.addon_ids, ['A1'])
        trace = result.calculation_trace
        self.assertEqual(trace.tool_name, 'calculate_solution')
        self.assertEqual(trace.input, dict(package_id='P1', addon_ids=['A1'], preferred_budget_sar=100))
        observation = CalculationResult.model_validate(trace.output)
        self.assertEqual(observation.package, self.pool.package_candidates[0])
        self.assertEqual(observation.addons, self.pool.addon_candidates)
        self.assertEqual(observation.total_price_sar, result.design.total_price_sar)
        self.assertEqual(observation.budget_difference_sar, result.design.budget_difference_sar)
        self.assertEqual(trace.status, 'SUCCESS')
        self.assertGreaterEqual(trace.execution_time_ms, 0)
        json.dumps(trace.to_dict())
        self.assertEqual(CalculatedDesign.model_validate_json(result.design.model_dump_json()), result.design)
        self.assertEqual(set(result.design.model_dump()), {'strategy', 'policy_rationale', 'package_id', 'addon_ids',
            'total_price_sar', 'preferred_budget_sar', 'budget_difference_sar'})
        self.assertEqual(self.pool.model_dump(), before)

    def test_model_pricing_is_rejected_before_calculation(self):
        self.proposal = {**self.proposal, 'total_price_sar': 1, 'budget_difference_sar': -99}
        self.model = self._build_model()
        with patch.object(agent, 'calculate_solution') as calculate, \
             self.assertLogs('backend.services.llm_retry', level='ERROR'), self.assertRaises(ValidationError):
            self.run_design()
        calculate.assert_not_called()

    def test_calculation_failure_does_not_trigger_redesign(self):
        self.pool.package_candidates[0].price_sar = -1
        with self.assertRaises(ValueError):
            self.run_design()
        self.assertEqual(self.model.bind_tools.return_value.invoke.call_count, 1)  # one design turn, then calculation fails


if __name__ == '__main__':
    unittest.main()
