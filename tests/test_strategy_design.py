"""Integrated one-strategy DESIGN tests with observation-driven fake models."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from backend.agents import solution_design_agent as agent
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import CandidatePool, PackageCandidate, AddonCandidate
from backend.schemas.solution import SolutionStrategy


def ai_message(tool_calls):
    return SimpleNamespace(tool_calls=tool_calls)


def proposal_call(args, call_id="call-design"):
    return {"name": "DesignProposal", "args": args, "id": call_id}


class StrategyDesignTests(unittest.TestCase):
    def setUp(self):
        self.requirements = Requirements(NeedSelection('Calls', 'Medium'),
                                         NeedSelection('Roaming', 'Low'), None, 100)
        self.strategy = SolutionStrategy(strategy_id='S1', decision_policy='CORE_FOCUS',
                                        intent='Protect calls with optional roaming',
                                        focus_priorities=[1, 2], intended_tradeoffs=[])
        self.pool = CandidatePool(search_status='SUCCESS', package_candidates=[PackageCandidate(
            package_id=key, name=key, price_sar=price, matched_needs=[], match_reasons=[],
            capabilities={'voice_minutes': 500}) for key, price in [('P1', 110), ('P2', 80)]],
            addon_candidates=[AddonCandidate(addon_id='A1', name='Roaming', price_sar=15,
                matched_needs=[], match_reasons=[], capabilities={'roaming_data_gb': 1})])
        self.proposal = dict(policy_rationale='P1 alone protects calls within budget under this policy.',
                            package_id='P1', addon_ids=[])

    def fake(self, *design_responses):
        # No genuine trade-off in this fixture by default: the model finalizes
        # directly, in one turn, without calling calculate_solution.
        model = Mock()
        turns = [ai_message([proposal_call(response)]) for response in (design_responses or [self.proposal])]
        model.bind_tools.return_value.invoke.side_effect = turns
        return model

    def run_design(self, model):
        return agent.design_strategy(self.requirements, self.pool, self.strategy, llm=model)

    def test_one_proposal_one_calculation_becomes_final_directly(self):
        model = self.fake()
        with patch.object(agent, 'calculate_solution', wraps=agent.calculate_solution) as calculation:
            result = self.run_design(model)
        bound = model.bind_tools.return_value
        self.assertEqual(bound.invoke.call_count, 1)  # one turn: no calculation call was needed
        calculation.assert_called_once_with(self.pool, package_id='P1', addon_ids=[], preferred_budget_sar=100)
        self.assertEqual(result.design.package_id, 'P1')
        self.assertEqual(result.design.total_price_sar, 110)
        self.assertEqual(result.design.budget_difference_sar, 10)
        self.assertEqual([s.stage for s in result.trace.stages], ['PROPOSAL', 'GROUND', 'DESIGN_FINAL'])
        self.assertEqual(len(result.trace.tool_traces), 1)  # no in-loop calculation traces
        observation = result.trace.tool_traces[0].output
        self.assertEqual(observation['package']['capabilities'], {'voice_minutes': 500})
        self.assertEqual(result.trace.stages[-1].details, result.design.model_dump(mode='json'))

    def test_unknown_id_retries_then_fails_before_calculation(self):
        for changes in ({'package_id': 'invented'}, {'addon_ids': ['invented']}):
            with self.subTest(changes=changes):
                bad = {**self.proposal, **changes}
                model = self.fake(bad, bad)
                with patch.object(agent, 'calculate_solution') as calculation, self.assertRaises(ValueError) as caught:
                    self.run_design(model)
                calculation.assert_not_called()
                self.assertEqual(model.bind_tools.return_value.invoke.call_count, 2)
                self.assertEqual(caught.exception.llm_retry['attempts'], 2)
                self.assertEqual(caught.exception.design_trace.stages[-1].status, 'ERROR')

    def test_unknown_id_recovers_on_retry(self):
        for changes in ({'package_id': 'invented'}, {'addon_ids': ['invented']}):
            with self.subTest(changes=changes):
                bad = {**self.proposal, **changes}
                model = self.fake(bad, self.proposal)
                result = self.run_design(model)
                self.assertEqual(model.bind_tools.return_value.invoke.call_count, 2)
                self.assertEqual(result.design.package_id, 'P1')

    def test_technical_retry_only_repairs_proposal(self):
        model = self.fake({}, self.proposal)
        result = self.run_design(model)
        self.assertEqual(model.bind_tools.return_value.invoke.call_count, 2)
        self.assertEqual(len(result.trace.tool_traces), 1)
        self.assertEqual(result.trace.stages[-1].stage, 'DESIGN_FINAL')

    def test_bad_proposal_exhausts_technical_retry_without_calculation(self):
        model = self.fake({}, {})
        with self.assertRaises(ValueError) as caught:
            self.run_design(model)
        self.assertEqual(caught.exception.llm_retry['attempts'], 2)
        self.assertEqual(caught.exception.design_trace.tool_traces, [])

    def test_calculation_failure_never_triggers_new_proposal(self):
        self.pool.package_candidates[0].price_sar = -1
        model = self.fake()
        with self.assertRaises(ValueError) as caught:
            self.run_design(model)
        self.assertEqual(model.bind_tools.return_value.invoke.call_count, 1)
        self.assertEqual(caught.exception.design_trace.stages[-1].status, 'ERROR')

    def test_full_original_strategy_preserved_despite_narrowed_context(self):
        model = self.fake()
        result = self.run_design(model)
        context = json.loads(model.bind_tools.return_value.invoke.call_args.args[0][1].content)
        self.assertNotIn('intent', context)
        self.assertNotIn('intended_tradeoffs', context)
        # The model was never told the strategy's intent/tradeoffs, yet the
        # full original strategy -- including that free text -- is preserved
        # on the traced/final result via Python re-attachment, not the model.
        self.assertEqual(result.design.strategy, self.strategy)
        self.assertEqual(result.design.strategy.intent, 'Protect calls with optional roaming')
        self.assertEqual(result.design.strategy.intended_tradeoffs, [])
        self.assertEqual(result.trace.stages[-1].details['strategy'], self.strategy.model_dump(mode='json'))

    def test_initial_prompt_keeps_cost_and_requirement_fit_guidance(self):
        model = self.fake()
        self.run_design(model)
        messages = model.bind_tools.return_value.invoke.call_args.args[0]
        guidance = ' '.join(messages[0].content.split())
        for text in ('Design for requirement fit, not maximum capability',
                     'Use canonical candidate prices only to browse and compare feasible',
                     "using the calculate_solution tool's authoritative result",
                     'Unchanged customer-relevant capability facts cannot be claimed as a capability improvement'):
            self.assertIn(text, guidance)
        context = json.loads(messages[1].content)
        self.assertEqual(list(context), ['needs', 'preferred_budget_sar', 'decision_policy',
                                         'focus_priorities', 'candidate_pool'])
        self.assertEqual(context['preferred_budget_sar'], self.requirements.preferred_budget_sar)
        self.assertEqual(context['candidate_pool'], self.pool.model_dump(mode='json'))
        self.assertEqual(context['decision_policy'], self.strategy.decision_policy)
        self.assertEqual(context['focus_priorities'], self.strategy.focus_priorities)
        self.assertNotIn('intent', context)
        self.assertNotIn('intended_tradeoffs', context)
        self.assertNotIn('strategy', context)

    def test_initial_prompt_excludes_other_policy_doctrine(self):
        # This fixture's strategy is CORE_FOCUS, so REQUIREMENT_FIT's and
        # CAPABILITY_HEADROOM's doctrine must not reach the model at all.
        model = self.fake()
        self.run_design(model)
        messages = model.bind_tools.return_value.invoke.call_args.args[0]
        guidance = ' '.join(messages[0].content.split())
        for text in (
            'Under REQUIREMENT_FIT, meet the requested Needs and Levels while avoiding',
            'Unnecessary cost means cost that adds no coverage for any requested need',
            'REQUIREMENT_FIT calls for the ordinary default reading',
            'Under REQUIREMENT_FIT specifically,',
            'Under CAPABILITY_HEADROOM,',
            'CAPABILITY_HEADROOM calls for more willingness to accept the additional cost',
        ):
            self.assertNotIn(text, guidance)

    def test_initial_prompt_keeps_core_focus_sacrifice_guidance(self):
        model = self.fake()
        self.run_design(model)
        messages = model.bind_tools.return_value.invoke.call_args.args[0]
        guidance = ' '.join(messages[0].content.split())
        for text in (
            "when serving a lower-priority need would conflict with the customer's preferred budget, "
            "sacrifice that lower-priority need's coverage in this composition to preserve "
            "higher-priority needs and budget fit",
            'a lower-priority need left weakly served or unserved is the intended, correct outcome of '
            'this policy, not a gap to compensate for with an extra package or add-on',
            'CORE_FOCUS declines an above-budget composition whose only benefit is '
            'covering a lower-priority need -- choose the within-budget composition '
            'instead and leave that lower-priority need weakly served or unserved',
            'under CORE_FOCUS this exception is about protecting a higher-priority need, '
            "never about adding a lower-priority need's coverage",
        ):
            self.assertIn(text, guidance)

    def test_initial_prompt_keeps_tool_calling_guidance(self):
        model = self.fake()
        self.run_design(model)
        messages = model.bind_tools.return_value.invoke.call_args.args[0]
        guidance = ' '.join(messages[0].content.split())
        for text in (
            'Call it only for compositions genuinely needed to resolve THIS policy\'s trade-off',
            'this is not a search, enumeration, or optimization step',
            'call it zero times and go straight to your final proposal',
            'You may call it as many times as genuinely needed to resolve this',
            "policy's trade-off, including zero times; there is no target count",
        ):
            self.assertIn(text, guidance)


if __name__ == '__main__':
    unittest.main()
