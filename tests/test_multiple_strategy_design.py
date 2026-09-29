"""Sequential multi-strategy execution through the real single-strategy path."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from backend.agents import solution_design_agent as agent
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import CandidatePool, PackageCandidate
from backend.schemas.solution import SolutionDesignPlan, SolutionStrategy


class MultipleStrategyTests(unittest.TestCase):
    def setUp(self):
        self.requirements = Requirements(NeedSelection('Calls', 'Medium'), None, None, 100)
        self.pool = CandidatePool(search_status='SUCCESS', addon_candidates=[], package_candidates=[
            PackageCandidate(package_id=f'P{i}', name=f'Package {i}', price_sar=80+i,
                             matched_needs=[], match_reasons=[], capabilities={'voice_minutes': 100*i})
            for i in (1, 2, 3)])
        self.plan = SolutionDesignPlan(strategies=[SolutionStrategy(strategy_id=f'S{i}',
            decision_policy='REQUIREMENT_FIT', intent=f'Explore calling option {i}',
            focus_priorities=[1], intended_tradeoffs=[]) for i in (3, 1, 2)])
        self.contexts = []
        self.call_order = []

    def fake(self, order, failure=None):
        # DESIGN's context no longer carries strategy_id (decision_policy is
        # the sole policy signal), so calls are correlated to strategies by
        # call order -- design_strategies() runs strategies sequentially, and
        # this fixture's strategies finalize in a single turn each (no
        # calculate_solution calls needed).
        model = Mock()
        remaining = list(order)

        def design_turn(messages):
            context = json.loads(messages[1].content)
            self.contexts.append(context)
            strategy_id = remaining.pop(0)
            self.call_order.append(strategy_id)
            if failure is not None and strategy_id == 'S1':
                raise failure
            package = 'P' + strategy_id[1:]
            args = dict(policy_rationale='Chosen package meets the calling target.',
                       package_id=package, addon_ids=[])
            return SimpleNamespace(tool_calls=[{'name': 'DesignProposal', 'args': args, 'id': f'call-{strategy_id}'}])
        model.bind_tools.return_value.invoke.side_effect = design_turn
        return model

    def test_one_and_three_preserve_order_independence_and_evidence(self):
        for count in (1, 3):
            with self.subTest(count=count):
                plan = SolutionDesignPlan(strategies=self.plan.strategies[:count])
                before = self.pool.model_dump()
                model = self.fake(order=[s.strategy_id for s in plan.strategies])
                with patch.object(agent, 'design_strategy', wraps=agent.design_strategy) as single:
                    result = agent.design_strategies(self.requirements, self.pool, plan, model)
                self.assertEqual(single.call_count, count)
                for call, strategy, item in zip(single.call_args_list, plan.strategies, result.results):
                    self.assertIs(call.args[0], self.requirements)
                    self.assertIs(call.args[1], self.pool)
                    self.assertEqual(call.args[2], strategy)
                    self.assertEqual(item.design.strategy, strategy)
                    self.assertEqual(item.design.package_id, 'P' + strategy.strategy_id[1:])
                    self.assertEqual([s.stage for s in item.trace.stages],
                                     ['PROPOSAL', 'GROUND', 'DESIGN_FINAL'])
                    self.assertEqual(item.trace.tool_traces[0].output['package']['package_id'], item.design.package_id)
                self.assertEqual(set(vars(result)), {'results', 'failures'})
                self.assertEqual(result.failures, [])
                self.assertEqual(self.pool.model_dump(), before)
                if count == 3:
                    result.results[0].trace.stages.clear()
                    result.results[0].design.strategy.intent = 'Mutation isolated to result'
                    self.assertEqual(len(result.results[1].trace.stages), 3)
                    self.assertEqual(plan.strategies[0].intent, 'Explore calling option 3')
        for context in self.contexts:
            self.assertNotIn('previous_steps', context)
            self.assertNotIn('solutions', context)
            self.assertNotIn('strategy', context)
            self.assertNotIn('intent', context)
            self.assertNotIn('intended_tradeoffs', context)

    def test_first_strategy_receives_no_prior_compositions(self):
        model = self.fake(order=[s.strategy_id for s in self.plan.strategies])
        agent.design_strategies(self.requirements, self.pool, self.plan, model)
        self.assertNotIn('prior_compositions', self.contexts[0])

    def test_later_strategies_receive_only_prior_successful_compositions_in_order(self):
        # Plan order is S3, S1, S2; each finalizes to package P<n> with no add-ons.
        model = self.fake(order=[s.strategy_id for s in self.plan.strategies])
        agent.design_strategies(self.requirements, self.pool, self.plan, model)
        self.assertNotIn('prior_compositions', self.contexts[0])
        self.assertEqual(self.contexts[1]['prior_compositions'],
                         [{'package_id': 'P3', 'addon_ids': []}])
        self.assertEqual(self.contexts[2]['prior_compositions'],
                         [{'package_id': 'P3', 'addon_ids': []}, {'package_id': 'P1', 'addon_ids': []}])

    def test_failed_strategy_composition_never_appears_as_prior_context(self):
        # Plan order is S3, S1, S2; S1 fails, so S2 must only see S3's composition.
        error = RuntimeError('model failure')
        with self.assertLogs('backend.services.llm_retry', level='ERROR'):
            agent.design_strategies(self.requirements, self.pool, self.plan,
                self.fake(order=[s.strategy_id for s in self.plan.strategies], failure=error))
        self.assertNotIn('prior_compositions', self.contexts[0])  # S3 (first strategy)
        self.assertEqual(self.contexts[1]['prior_compositions'],  # S1 (fails, but still sees S3)
                         [{'package_id': 'P3', 'addon_ids': []}])
        self.assertEqual(self.contexts[2]['prior_compositions'],  # S2: S1's failed attempt is absent
                         [{'package_id': 'P3', 'addon_ids': []}])

    def test_prior_composition_context_does_not_block_legitimate_convergence(self):
        # Every strategy proposes the identical composition regardless of
        # prior_compositions content -- design_strategies must not reject or
        # alter this; convergence stays a valid, unforced outcome.
        model = Mock()

        def design_turn(messages):
            context = json.loads(messages[1].content)
            self.contexts.append(context)
            args = dict(policy_rationale='This policy also converges on P1.',
                       package_id='P1', addon_ids=[])
            return SimpleNamespace(tool_calls=[{'name': 'DesignProposal', 'args': args, 'id': 'call'}])
        model.bind_tools.return_value.invoke.side_effect = design_turn

        result = agent.design_strategies(self.requirements, self.pool, self.plan, model)
        self.assertEqual(result.failures, [])
        self.assertEqual([item.design.package_id for item in result.results], ['P1', 'P1', 'P1'])
        self.assertEqual(self.contexts[1]['prior_compositions'], [{'package_id': 'P1', 'addon_ids': []}])
        self.assertEqual(self.contexts[2]['prior_compositions'],
                         [{'package_id': 'P1', 'addon_ids': []}, {'package_id': 'P1', 'addon_ids': []}])

    def test_partial_failure_preserves_successful_designs_and_records_failure(self):
        # Plan order is S3, S1, S2; only S1 fails, so S3 and S2 must both still run and succeed.
        error = RuntimeError('model failure')
        with self.assertLogs('backend.services.llm_retry', level='ERROR'):
            result = agent.design_strategies(self.requirements, self.pool, self.plan,
                self.fake(order=[s.strategy_id for s in self.plan.strategies], failure=error))
        self.assertEqual([item.design.strategy.strategy_id for item in result.results], ['S3', 'S2'])
        self.assertEqual(len(result.failures), 1)
        failure = result.failures[0]
        self.assertEqual(failure.strategy_id, 'S1')
        self.assertEqual(failure.error_type, 'RuntimeError')
        self.assertEqual(failure.reason, 'model failure')
        self.assertTrue(failure.trace.stages)
        self.assertEqual(failure.trace.stages[-1].status, 'ERROR')
        self.assertEqual(self.call_order, ['S3', 'S1', 'S2'])

    def test_all_strategies_failing_raises_terminal_error_with_evidence(self):
        error = RuntimeError('design failed')
        with patch.object(agent, 'design_strategy', side_effect=error) as single:
            with self.assertRaises(ValueError) as caught:
                agent.design_strategies(self.requirements, self.pool, self.plan,
                    self.fake(order=[s.strategy_id for s in self.plan.strategies]))
        self.assertIsNot(caught.exception, error)
        self.assertEqual(single.call_count, 3)
        failures = caught.exception.strategy_failures
        self.assertEqual([failure.strategy_id for failure in failures], ['S3', 'S1', 'S2'])
        self.assertTrue(all(failure.error_type == 'RuntimeError' for failure in failures))
        self.assertTrue(all(failure.reason == 'design failed' for failure in failures))

    def test_invalid_plan_fails_before_execution(self):
        self.plan.strategies.clear()
        with patch.object(agent, 'design_strategy') as single, self.assertRaises(ValueError):
            agent.design_strategies(self.requirements, self.pool, self.plan)
        single.assert_not_called()


if __name__ == '__main__':
    unittest.main()
