"""Full pipeline evidence through PLAN -> DESIGN -> QUALITY CHECK -> CONSOLIDATE ->
GUARDRAILS -> COMPARE -> RECOMMENDATION, without live calls."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from backend.agents import solution_design_agent as a
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import CandidatePool, PackageCandidate
from backend.schemas.solution import (
    SolutionDesignPlan, DesignProposal,
    SolutionComparison, SolutionRecommendation,
)


class QualityCheckTests(unittest.TestCase):
    def setUp(self):
        self.req = Requirements(NeedSelection('Calls','Medium'),None,NeedSelection('Data','Low'),100)
        self.pool = CandidatePool(search_status='SUCCESS', addon_candidates=[], package_candidates=[
            PackageCandidate(package_id=k,name=k,price_sar=p,matched_needs=[],match_reasons=[],
                             capabilities={'voice_minutes':v,'data_gb':5})
            for k,p,v in [('P1',110,500),('P2',80,200)]])
        self.strategies = [dict(strategy_id=k,decision_policy=policy,intent='Protect calls '+k,
                                focus_priorities=[1],intended_tradeoffs=[])
                           for k,policy in (('S1','REQUIREMENT_FIT'),('S2','CORE_FOCUS'))]
        self.calls=[]
        self.distinct=False
        self.fail_ids=set()
        self.design_fail_ids=set()
        self.design_call_count=0
        self.comparison_fails=False
        self.recommendation_fails=False

    def model(self):
        model=Mock()
        def structured(schema, **kwargs):
            runnable=Mock()
            def invoke(messages):
                data=json.loads(messages[1].content)
                self.calls.append((schema,data))
                if schema is SolutionDesignPlan:
                    return dict(strategies=self.strategies)
                if schema is SolutionComparison:
                    if self.comparison_fails:
                        return {}
                    return dict(needs_coverage_summary='Both solutions cover Calls and Data.',
                        priority_fit_summary='Calls is protected in both solutions.',
                        budget_fit_summary='placeholder text the caller must not trust',
                        key_tradeoff='S1 costs 80 SAR; S2 costs 110 SAR and has more calling minutes.')
                if schema is SolutionRecommendation:
                    if self.recommendation_fails:
                        return dict(recommended_solution_id='not-selectable', why_recommended='Invalid pick')
                    return dict(recommended_solution_id=data['selectable_solution_ids'][0],
                                why_recommended='Best available fit')
                raise AssertionError('Unexpected stage '+schema.__name__)
            runnable.invoke.side_effect=invoke
            return runnable
        model.with_structured_output.side_effect=structured

        # DESIGN's context carries no strategy_id (decision_policy is the sole
        # policy signal): calls are correlated to strategies by order -- S1
        # designs first, S2 second. Neither fixture needs a calculate_solution
        # call, so each strategy finalizes in a single native tool-calling turn.
        def design_turn(messages):
            data = json.loads(messages[1].content)
            self.calls.append((DesignProposal, data))
            strategy_id = self.strategies[self.design_call_count]['strategy_id']
            if strategy_id in self.design_fail_ids:
                self.design_call_count += 1
                raise RuntimeError(f'design failed for {strategy_id}')
            package = 'P2' if self.distinct and self.design_call_count==0 else 'P1'
            self.design_call_count += 1
            args = dict(policy_rationale='Chosen package meets the calling target.',
                       package_id=package, addon_ids=[])
            return SimpleNamespace(tool_calls=[{'name': 'DesignProposal', 'args': args,
                                                'id': f'call-{self.design_call_count}'}])
        model.bind_tools.return_value.invoke.side_effect = design_turn
        return model

    def patched_review(self):
        """Simulate a deterministic Quality Check integrity failure for
        strategy_ids in self.fail_ids (Quality Check has no LLM call to fail
        any more, so failure is now injected at the review_solution boundary
        itself, exactly like any other deterministic integrity failure)."""
        real = a.review_solution

        def side_effect(requirements, candidate_pool, design, calculation, llm=None, stage_traces=None):
            if design.strategy.strategy_id in self.fail_ids:
                raise ValueError(f'Simulated deterministic integrity failure for {design.strategy.strategy_id}')
            return real(requirements, candidate_pool, design, calculation, llm=llm, stage_traces=stage_traces)

        return patch.object(a, 'review_solution', side_effect=side_effect)

    def test_pipeline_consolidates_and_recommends_when_valid(self):
        result=a.run_solution_design(self.req,self.pool,self.model())
        # S1 and S2 both design to package P1 (distinct=False): identical composition consolidates to one.
        self.assertEqual([s.strategy.strategy_id for s in result.solutions],['S1'])
        solution = result.solutions[0]
        self.assertEqual([s.strategy_id for s in solution.also_converged_strategies], ['S2'])
        self.assertEqual(solution.review.budget_fit,'ABOVE')
        self.assertEqual(solution.total_price_sar,110)
        self.assertEqual(solution.budget_difference_sar,10)
        self.assertEqual([(n.need,n.need_priority) for n in solution.review.needs_coverage],
                         [('Calls',1),('Data',3)])
        self.assertTrue(all(item.coverage == 'COVERED' for item in solution.review.needs_coverage))
        self.assertEqual(result.outcome, 'RECOMMENDED')
        self.assertEqual([s.stage for s in result.stage_traces],
                         ['PROPOSAL','GROUND','DESIGN_FINAL','PROPOSAL','GROUND','DESIGN_FINAL',
                          'QUALITY_CHECK','QUALITY_CHECK','CONSOLIDATE','GUARDRAILS','COMPARE',
                          'RECOMMENDATION','RECOMMENDATION'])
        # 110 SAR is within the 100 * 1.15 = 115 SAR hard limit: it is the sole valid candidate.
        consolidate_trace = next(s for s in result.stage_traces if s.stage == 'CONSOLIDATE')
        self.assertEqual(consolidate_trace.details['kept_strategy_id'], 'S1')
        self.assertEqual(consolidate_trace.details['converged_strategy_ids'], ['S1', 'S2'])
        self.assertTrue(consolidate_trace.details['needs_coverage_agreement'])
        self.assertTrue(all(r.overall == 'PASS' for r in result.guardrails))
        self.assertIsNotNone(result.comparison)
        self.assertIn('S1', result.comparison.key_tradeoff)
        self.assertIsNotNone(result.recommendation)
        self.assertEqual(result.recommendation.recommended_solution_id, 'S1')
        # No SolutionReview call: Quality Check is fully deterministic now.
        self.assertEqual([schema for schema,_ in self.calls],[
            SolutionDesignPlan,DesignProposal,DesignProposal,SolutionRecommendation])

    def test_successful_design_strategy_exports_its_design_trace_stages(self):
        result = a.run_solution_design(self.req, self.pool, self.model())
        design_stages = [s for s in result.stage_traces if s.stage in ('PROPOSAL', 'GROUND', 'DESIGN_FINAL')]
        self.assertEqual([s.stage for s in design_stages],
                         ['PROPOSAL', 'GROUND', 'DESIGN_FINAL', 'PROPOSAL', 'GROUND', 'DESIGN_FINAL'])
        final_stages = [s for s in design_stages if s.stage == 'DESIGN_FINAL']
        self.assertEqual(final_stages[0].details['strategy']['strategy_id'], 'S1')
        self.assertEqual(final_stages[0].details['package_id'], 'P1')
        self.assertEqual(final_stages[1].details['strategy']['strategy_id'], 'S2')

    def test_multiple_successful_strategies_preserve_execution_order_in_trace(self):
        # DESIGN traces must appear in plan/execution order, ahead of the
        # later QUALITY_CHECK stages -- never appended after unrelated stages.
        result = a.run_solution_design(self.req, self.pool, self.model())
        ordered_stages = [s.stage for s in result.stage_traces
                         if s.stage in ('PROPOSAL', 'GROUND', 'DESIGN_FINAL', 'QUALITY_CHECK')]
        self.assertEqual(ordered_stages,
                         ['PROPOSAL', 'GROUND', 'DESIGN_FINAL', 'PROPOSAL', 'GROUND', 'DESIGN_FINAL',
                          'QUALITY_CHECK', 'QUALITY_CHECK'])

    def test_failed_design_strategy_still_exports_its_failure_trace(self):
        self.design_fail_ids = {'S2'}
        with self.assertLogs('backend.services.llm_retry', level='ERROR'):
            result = a.run_solution_design(self.req, self.pool, self.model())
        self.assertEqual([s.strategy.strategy_id for s in result.solutions], ['S1'])
        design_stages = [s for s in result.stage_traces
                         if s.stage in ('PROPOSAL', 'GROUND', 'DESIGN_FINAL', 'DESIGN')]
        self.assertEqual([s.stage for s in design_stages], ['PROPOSAL', 'GROUND', 'DESIGN_FINAL', 'DESIGN'])
        failure_stage = design_stages[-1]
        self.assertEqual(failure_stage.status, 'ERROR')
        self.assertEqual(failure_stage.details['error_type'], 'RuntimeError')

    def test_mixed_success_failure_success_design_outcome_has_no_duplication(self):
        self.strategies = self.strategies + [dict(strategy_id='S3', decision_policy='CAPABILITY_HEADROOM',
                                                  intent='Protect calls S3', focus_priorities=[1], intended_tradeoffs=[])]
        self.design_fail_ids = {'S2'}
        with self.assertLogs('backend.services.llm_retry', level='ERROR'):
            result = a.run_solution_design(self.req, self.pool, self.model())
        design_stage_events = [(s.stage, s.status) for s in result.stage_traces
                               if s.stage in ('PROPOSAL', 'GROUND', 'DESIGN_FINAL', 'DESIGN')]
        self.assertEqual(design_stage_events, [
            ('PROPOSAL', 'SUCCESS'), ('GROUND', 'SUCCESS'), ('DESIGN_FINAL', 'SUCCESS'),  # S1
            ('DESIGN', 'ERROR'),                                                          # S2 (failed)
            ('PROPOSAL', 'SUCCESS'), ('GROUND', 'SUCCESS'), ('DESIGN_FINAL', 'SUCCESS'),  # S3
        ])
        # Exactly one DESIGN trace group per planned strategy -- no duplication.
        self.assertEqual(len(result.designs.results), 2)
        self.assertEqual([f.strategy_id for f in result.designs.failures], ['S2'])
        # Per-strategy DESIGN failure isolation, composition/count decisions,
        # and final outcome are unaffected by the tracing fix.
        self.assertEqual(result.outcome, 'RECOMMENDED')

    def recommendation_context(self):
        return next(data for schema, data in self.calls if schema is SolutionRecommendation)

    def test_normal_late_stage_path_is_unchanged_and_reports_success(self):
        self.distinct = True
        result = a.run_solution_design(self.req, self.pool, self.model())
        self.assertEqual((result.comparison_status, result.recommendation_status), ('SUCCESS', 'SUCCESS'))
        self.assertIsNotNone(result.comparison)
        self.assertEqual(result.recommendation.recommended_solution_id, 'S1')
        self.assertFalse(any(s.status in ('RECOVERED', 'UNAVAILABLE') for s in result.stage_traces))
        final = a.assemble_final_solutions(self.pool, result)
        self.assertTrue(final.recommendation_available)
        self.assertEqual([s.solution_id for s in final.solutions if s.recommended], ['S1'])

    def test_recommended_solution_gets_a_rationale_even_when_all_solutions_are_fully_covered(self):
        # Regression shape: both solutions fully cover every need (Calls and
        # Data are both COVERED for P1 and P2) and differ only in price -- the
        # recommended one must still receive a "Recommended Because" rationale,
        # not an empty one, and it must never leak an internal strategy ID.
        self.distinct = True
        result = a.run_solution_design(self.req, self.pool, self.model())
        final = a.assemble_final_solutions(self.pool, result)
        self.assertTrue(all(item.coverage == 'COVERED'
                            for solution in final.solutions for item in solution.needs_coverage))
        recommended = next(s for s in final.solutions if s.recommended)
        self.assertEqual(recommended.recommendation_reason, 'Best available fit')
        for strategy_id in ('S1', 'S2'):
            self.assertNotIn(strategy_id, recommended.recommendation_reason)
        non_recommended = next(s for s in final.solutions if not s.recommended)
        self.assertIsNone(non_recommended.recommendation_reason)

    def test_recommendation_receives_only_selectable_ids_after_convergence(self):
        # S1 and S2 converge on P1; S2 survives only inside also_converged_strategies.
        a.run_solution_design(self.req, self.pool, self.model())
        context = self.recommendation_context()
        self.assertEqual(context['selectable_solution_ids'], ['S1'])
        self.assertNotIn('S2', json.dumps(context))

    def test_compare_llm_failure_keeps_solutions_and_recommendation_continues(self):
        self.distinct = True
        self.comparison_fails = True
        with self.assertLogs('backend.services.llm_retry', level='ERROR'):
            result = a.run_solution_design(self.req, self.pool, self.model())
        self.assertEqual(result.outcome, 'RECOMMENDED')
        self.assertIsNone(result.comparison)
        self.assertEqual(result.comparison_status, 'FAILED')
        self.assertEqual(result.recommendation_status, 'SUCCESS')
        self.assertIsNone(self.recommendation_context()['comparison'])
        compare_stages = [(s.status, s.details.get('recovery')) for s in result.stage_traces if s.stage == 'COMPARE']
        self.assertEqual(compare_stages, [('ERROR', None), ('RECOVERED', 'CONTINUE_WITHOUT_COMPARISON')])
        final = a.assemble_final_solutions(self.pool, result)
        self.assertEqual([s.solution_id for s in final.solutions], ['S1', 'S2'])
        self.assertTrue(final.recommendation_available)

    def test_recommendation_llm_failure_keeps_every_solution_without_recommending(self):
        self.distinct = True
        self.recommendation_fails = True
        with self.assertLogs('backend.services.llm_retry', level='ERROR'):
            result = a.run_solution_design(self.req, self.pool, self.model())
        self.assertEqual(result.outcome, 'RECOMMENDED')
        self.assertIsNone(result.recommendation)
        self.assertEqual((result.comparison_status, result.recommendation_status), ('SUCCESS', 'UNAVAILABLE'))
        recommendation_stages = [s for s in result.stage_traces if s.stage == 'RECOMMENDATION']
        self.assertEqual([s.status for s in recommendation_stages], ['STARTED', 'ERROR', 'UNAVAILABLE'])
        self.assertEqual(recommendation_stages[1].details['rejected_solution_ids'],
                         ['not-selectable', 'not-selectable'])
        self.assertEqual(recommendation_stages[2].details['recovery'], 'SOLUTIONS_WITHOUT_RECOMMENDATION')
        final = a.assemble_final_solutions(self.pool, result)
        self.assertEqual([s.solution_id for s in final.solutions], ['S1', 'S2'])
        self.assertFalse(final.recommendation_available)
        self.assertFalse(any(s.recommended for s in final.solutions))

    def test_both_late_stages_failing_still_returns_valid_solutions(self):
        self.distinct = True
        self.comparison_fails = True
        self.recommendation_fails = True
        with self.assertLogs('backend.services.llm_retry', level='ERROR'):
            result = a.run_solution_design(self.req, self.pool, self.model())
        self.assertEqual((result.comparison_status, result.recommendation_status), ('FAILED', 'UNAVAILABLE'))
        self.assertIsNone(result.comparison)
        self.assertIsNone(result.recommendation)
        final = a.assemble_final_solutions(self.pool, result)
        self.assertEqual(len(final.solutions), 2)
        self.assertFalse(final.recommendation_available)
        self.assertFalse(any(s.recommended for s in final.solutions))

    def test_single_solution_recommendation_failure_keeps_the_solution(self):
        self.strategies = self.strategies[:1]
        self.recommendation_fails = True
        with self.assertLogs('backend.services.llm_retry', level='ERROR'):
            result = a.run_solution_design(self.req, self.pool, self.model())
        self.assertEqual((result.comparison_status, result.recommendation_status),
                         ('DETERMINISTIC_SINGLE', 'UNAVAILABLE'))
        self.assertNotIn(SolutionComparison, [schema for schema, _ in self.calls])
        final = a.assemble_final_solutions(self.pool, result)
        self.assertEqual([s.solution_id for s in final.solutions], ['S1'])
        self.assertFalse(final.solutions[0].recommended)
        self.assertFalse(final.recommendation_available)

    def test_deterministic_late_stage_errors_are_not_recovered(self):
        self.distinct = True
        for target in ('compare_solutions', 'recommend_solution'):
            with self.subTest(target=target):
                self.calls, self.design_call_count = [], 0
                with patch.object(a, target, side_effect=ValueError('data integrity')), \
                        self.assertRaises(ValueError) as caught:
                    a.run_solution_design(self.req, self.pool, self.model())
                self.assertEqual(str(caught.exception), 'data integrity')

    def test_final_evidence_used_by_review_and_comparison(self):
        self.distinct=True
        result=a.run_solution_design(self.req,self.pool,self.model())
        compared=next(data for schema,data in self.calls if schema is SolutionComparison)
        # Deterministic review used the actual grounded calculation/composition,
        # not stale or wrong data: P2's real price and capabilities show up.
        self.assertEqual(result.solutions[0].total_price_sar, 80)
        self.assertEqual(compared['solutions'][0]['package']['capabilities']['voice_minutes'],200)
        self.assertEqual(compared['solutions'][0]['package']['package_id'],'P2')
        self.assertEqual(result.solutions[0].package_id,'P2')
        self.assertEqual(result.solutions[0].review.budget_fit,'WITHIN')
        self.assertIn('P2', result.solutions[0].review.needs_coverage[0].evidence)
        self.assertEqual(result.solutions[1].package_id,'P1')
        self.assertIn(SolutionComparison,[schema for schema,_ in self.calls])
        self.assertTrue(result.comparison.key_tradeoff)

    def test_single_solution_no_comparison_llm(self):
        self.strategies=self.strategies[:1]
        result=a.run_solution_design(self.req,self.pool,self.model())
        self.assertEqual(len(result.solutions), 1)
        self.assertEqual(result.outcome, 'RECOMMENDED')
        self.assertIn('S1', result.comparison.key_tradeoff)
        self.assertNotIn(SolutionComparison,[schema for schema,_ in self.calls])

    def test_guardrails_receives_final_calculation_evidence(self):
        self.distinct = True
        with patch.object(a, 'check_solution_guardrails', wraps=a.check_solution_guardrails) as guard:
            result = a.run_solution_design(self.req, self.pool, self.model())
        self.assertEqual(guard.call_count, 1)
        self.assertIs(guard.call_args.args[0], self.req)
        self.assertIs(guard.call_args.args[1], self.pool)
        self.assertIs(guard.call_args.args[2], result.solutions)
        evidence = guard.call_args.args[3]
        self.assertEqual(list(evidence), ['S1', 'S2'])
        self.assertEqual(evidence['S1'].package.package_id, 'P2')
        self.assertEqual(evidence['S1'].total_price_sar, 80)
        self.assertEqual(evidence['S2'].package.package_id, 'P1')
        self.assertTrue(all(len(d.trace.tool_traces) == 1 for d in result.designs.results))
        self.assertTrue(all(r.overall == 'PASS' for r in result.guardrails))

    def test_guardrail_failure_yields_no_valid_recommendation(self):
        self.pool.package_candidates[0].price_sar = 140
        with patch.object(a, 'compare_solutions') as compare, patch.object(a, 'recommend_solution') as recommend:
            result = a.run_solution_design(self.req, self.pool, self.model())
        # S1 and S2 both design to package P1 (distinct=False): identical composition consolidates to one.
        self.assertEqual(len(result.solutions), 1)
        self.assertTrue(all(r.overall == 'FAIL' for r in result.guardrails))
        self.assertTrue(all(r.checks['budget_hard_limit'].status == 'FAIL' for r in result.guardrails))
        self.assertEqual(result.outcome, 'NO_VALID_RECOMMENDATION')
        self.assertIsNone(result.comparison)
        self.assertIsNone(result.recommendation)
        # Guardrails runs before COMPARE/RECOMMENDATION: neither stage is reached when nothing is valid.
        self.assertEqual([s.stage for s in result.stage_traces[-2:]], ['CONSOLIDATE', 'GUARDRAILS'])
        compare.assert_not_called()
        recommend.assert_not_called()

    def test_quality_does_not_redesign_calculate_or_mutate_inputs(self):
        model=self.model()
        plan=a.create_solution_design_plan(self.req,self.pool,model)
        designs=a.design_strategies(self.req,self.pool,plan,model)
        before=[r.design.model_dump() for r in designs.results]
        with patch.object(a,'calculate_solution',side_effect=AssertionError('No recalculation')), \
             patch.object(a,'design_strategy',side_effect=AssertionError('No redesign')):
            a.quality_check(self.req,self.pool,designs,model)
        self.assertEqual(before,[r.design.model_dump() for r in designs.results])

    def test_one_strategy_fails_integrity_check_pipeline_continues_with_survivor(self):
        # Case A: strategy_1 passes review, strategy_2 fails Quality Check's
        # deterministic integrity check -- the request must still succeed
        # with the survivor instead of aborting the whole Solution Design workflow.
        self.fail_ids={'S2'}
        with self.patched_review():
            result=a.run_solution_design(self.req,self.pool,self.model())
        self.assertEqual([s.strategy.strategy_id for s in result.solutions],['S1'])
        self.assertEqual(result.outcome,'RECOMMENDED')
        self.assertTrue(all(r.overall=='PASS' for r in result.guardrails))
        self.assertIsNotNone(result.comparison)
        self.assertIsNotNone(result.recommendation)
        self.assertEqual(result.recommendation.recommended_solution_id,'S1')
        final_output=a.assemble_final_solutions(self.pool,result)
        self.assertEqual([s.solution_id for s in final_output.solutions],['S1'])
        # A single survivor short-circuits COMPARE's LLM call deterministically.
        self.assertNotIn(SolutionComparison,[schema for schema,_ in self.calls])
        # The dropped strategy's integrity-failure evidence is preserved in the trace timeline.
        error_trace=next(s for s in result.stage_traces if s.stage=='QUALITY_CHECK' and s.status=='ERROR')
        self.assertEqual(error_trace.details['error_type'],'ValueError')
        self.assertNotIn('llm_retry', error_trace.details)
        dropped_trace=next(s for s in result.stage_traces if s.stage=='QUALITY_CHECK' and s.status=='DROPPED')
        self.assertEqual(dropped_trace.details['strategy_id'],'S2')

    def test_two_of_three_strategies_fail_integrity_check_pipeline_succeeds_with_survivor(self):
        # Case B: multiple strategies fail Quality Check's deterministic
        # integrity check but one valid solution survives.
        self.strategies=self.strategies+[dict(strategy_id='S3',decision_policy='CAPABILITY_HEADROOM',
                                              intent='Protect calls S3',focus_priorities=[1],intended_tradeoffs=[])]
        self.fail_ids={'S2','S3'}
        with self.patched_review():
            result=a.run_solution_design(self.req,self.pool,self.model())
        self.assertEqual([s.strategy.strategy_id for s in result.solutions],['S1'])
        self.assertEqual(result.outcome,'RECOMMENDED')
        self.assertIsNotNone(result.recommendation)
        self.assertEqual(result.recommendation.recommended_solution_id,'S1')
        dropped=sorted(s.details['strategy_id'] for s in result.stage_traces
                       if s.stage=='QUALITY_CHECK' and s.status=='DROPPED')
        self.assertEqual(dropped,['S2','S3'])

    def test_all_strategies_fail_integrity_check_is_terminal_request_failure(self):
        # Case C: every strategy fails Quality Check's deterministic integrity
        # check -- the request becomes terminal FAILED.
        self.fail_ids={'S1','S2'}
        with self.patched_review():
            with self.assertRaises(ValueError) as caught:
                a.run_solution_design(self.req,self.pool,self.model())
        error=caught.exception
        self.assertEqual({f.strategy_id for f in error.strategy_failures},{'S1','S2'})
        self.assertTrue(all(f.trace[-1].status=='DROPPED' for f in error.strategy_failures))
        self.assertNotIn(SolutionComparison,[schema for schema,_ in self.calls])
        self.assertNotIn(SolutionRecommendation,[schema for schema,_ in self.calls])


if __name__=='__main__':
    unittest.main()
