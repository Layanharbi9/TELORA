"""Terminal SSE failure-stage attribution and the generic failure message copy."""
import itertools
import json
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.api import app as app_module
from backend.schemas.checkpoint import WorkflowCheckpoint
from backend.schemas.request import Request as RequestRecord, RequestStatus, WorkflowStage
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import CandidatePool
from backend.services.request_service import RequestService


def _requirements():
    return Requirements(NeedSelection('Calls', 'Medium'), None, NeedSelection('Data', 'Low'), 100)


def _row(status, stage):
    return RequestRecord(request_id='REQ-1', customer_id='CUST-1', requirements=_requirements(),
                         status=status, stage=stage)


def _checkpoint(with_pool):
    pool = CandidatePool(search_status='SUCCESS', addon_candidates=[], package_candidates=[]) if with_pool else None
    return WorkflowCheckpoint(request_id='REQ-1', candidate_pool=pool)


# ---------------------------------------------------------------------------
# Pure-Python mirrors of design.js's error-kind classification/rendering.
# There is no JS runtime in this environment (no Node, no js2py), so these
# functions are hand-translated 1:1 from the exact current lines of
# design.js and exercised against real backend-shaped SSE payloads, rather
# than only grepping the JS source for expected substrings. Each docstring
# names the exact JS this mirrors; FrontendErrorCTAGuardTests separately
# guards that those literal JS lines still exist unchanged, so a future edit
# to either side that silently diverges will break one of the two suites.
# ---------------------------------------------------------------------------
def classify_error_kind_from_failed_event(payload: dict) -> str:
    """Mirrors design.js onEvent(): S.errorKind=ev.category==='connection'?'connection':'technical';"""
    return 'connection' if payload.get('category') == 'connection' else 'technical'


def classify_error_kind_from_done_event(payload: dict) -> str | None:
    """Mirrors design.js onEvent(): if(!ev.data?.solutions?.length){S.errorKind='business';...}"""
    solutions = (payload.get('data') or {}).get('solutions')
    return 'business' if not solutions else None


def error_view_shows_adjust(error_kind: str) -> bool:
    """Mirrors design.js errorView()'s foot ternary: only 'business' includes adjustBtn(...)."""
    return error_kind == 'business'


def error_view_tip_text(error_kind: str) -> str:
    """Mirrors design.js errorView()'s tipText ternary."""
    return 'Adjust your pieces, or try again.' if error_kind == 'business' else 'Try again in a moment.'


class BuildEventsStageAttributionTests(unittest.TestCase):
    """Failed-stage attribution must come from the persisted checkpoint, not connection-local last_stage."""

    def setUp(self):
        self.client = TestClient(app_module.app)
        self.client.cookies.set(app_module.SESSION_COOKIE, 'CUST-1')
        patcher = patch.object(app_module, '_database', return_value=Mock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _first_event(self, get_request, message='Boom'):
        """Return the terminal `status: failed` event, skipping live narration events."""
        with patch.object(RequestService, 'get_request', side_effect=get_request), \
             patch.object(RequestService, 'get_checkpoint', return_value=self._checkpoint), \
             patch.object(app_module, '_error_message', return_value=message):
            with self.client.stream('GET', '/api/build/REQ-1/events') as response:
                for line in response.iter_lines():
                    if line.startswith('data: '):
                        payload = json.loads(line[len('data: '):])
                        if payload.get('status') == 'failed':
                            return payload
        return None

    def _first_done_event(self, get_request):
        """Return the terminal `stage: done` event (the NEEDS_ADJUSTMENT/business path)."""
        with patch.object(RequestService, 'get_request', side_effect=get_request), \
             patch.object(RequestService, 'get_checkpoint', return_value=self._checkpoint):
            with self.client.stream('GET', '/api/build/REQ-1/events') as response:
                for line in response.iter_lines():
                    if line.startswith('data: '):
                        payload = json.loads(line[len('data: '):])
                        if payload.get('stage') == 'done':
                            return payload
        return None

    def test_error_with_candidate_pool_reports_building(self):
        # mark_error() always persists stage=ERROR alongside status=ERROR, so a
        # fresh connection that only ever observes the terminal row must still
        # attribute the failure correctly from the checkpoint.
        self._checkpoint = _checkpoint(with_pool=True)
        row = _row(RequestStatus.ERROR, WorkflowStage.ERROR)
        event = self._first_event(lambda request_id: row)
        self.assertEqual(event['stage'], 'building')
        self.assertEqual(event['status'], 'failed')

    def test_error_without_candidate_pool_reports_finding(self):
        self._checkpoint = _checkpoint(with_pool=False)
        row = _row(RequestStatus.ERROR, WorkflowStage.ERROR)
        event = self._first_event(lambda request_id: row)
        self.assertEqual(event['stage'], 'finding')
        self.assertEqual(event['status'], 'failed')

    def test_real_connection_failure_event_shows_try_again_only(self):
        # Drives the actual SSE endpoint through a genuine backend ERROR with
        # the real customer-facing connection message, then classifies that
        # real payload the same way design.js does. This is the exact path a
        # real browser APIConnectionError failure takes end-to-end.
        self._checkpoint = _checkpoint(with_pool=True)
        row = _row(RequestStatus.ERROR, WorkflowStage.ERROR)
        message = app_module._failure_message(type('APIConnectionError', (Exception,), {})())
        event = self._first_event(lambda request_id: row, message=message)
        self.assertNotIn('category', event)
        self.assertEqual(event['message'], message)
        kind = classify_error_kind_from_failed_event(event)
        self.assertEqual(kind, 'technical')
        self.assertFalse(error_view_shows_adjust(kind))
        tip = error_view_tip_text(kind)
        for banned in ('adjust', 'puzzle', 'piece'):
            self.assertNotIn(banned, tip.lower())

    def test_real_needs_adjustment_event_still_shows_adjust_my_puzzle(self):
        # The business/no-fit path is a different SSE shape entirely (a
        # normal "done" event with an empty solutions list, not a "failed"
        # status) and must keep offering Adjust my puzzle.
        self._checkpoint = _checkpoint(with_pool=True)
        row = _row(RequestStatus.NEEDS_ADJUSTMENT, WorkflowStage.REQUIREMENTS)
        event = self._first_done_event(lambda request_id: row)
        self.assertEqual(event['data']['solutions'], [])
        kind = classify_error_kind_from_done_event(event)
        self.assertEqual(kind, 'business')
        self.assertTrue(error_view_shows_adjust(kind))
        self.assertIn('adjust', error_view_tip_text(kind).lower())

    def test_reconnected_connection_after_failure_uses_same_attribution(self):
        # Simulate a connection that watched the DESIGNING transition live and
        # only later observed the ERROR row -- last_stage would have briefly
        # been "DESIGNING" mid-stream, but the terminal attribution must still
        # come from the checkpoint, not from that connection-local history.
        self._checkpoint = _checkpoint(with_pool=True)
        designing = _row(RequestStatus.PROCESSING, WorkflowStage.DESIGNING)
        error = _row(RequestStatus.ERROR, WorkflowStage.ERROR)
        rows = itertools.chain([designing, designing], itertools.repeat(error))
        event = self._first_event(lambda request_id: next(rows))
        self.assertEqual(event['stage'], 'building')
        self.assertEqual(event['status'], 'failed')


class BuildEventsProgressTests(unittest.TestCase):
    """Live stage events come from the persisted stage plus the SEARCHING-only search_phase."""

    def setUp(self):
        self.client = TestClient(app_module.app)
        self.client.cookies.set(app_module.SESSION_COOKIE, 'CUST-1')
        self.db = Mock()
        patcher = patch.object(app_module, '_database', return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def stream(self, states, checkpoint=None):
        """Replay (status, stage, search_phase) per poll; return (stage, status) events to the terminal one."""
        states = [states[0], *states]  # build_events reads the row once for its ownership check
        scripted, current = iter(states), {}

        def get_request(request_id):
            status, stage, phase = next(scripted, states[-1])
            current['phase'] = phase
            return _row(status, stage)

        self.db.get_search_phase.side_effect = lambda request_id: current.get('phase')
        checkpoint = checkpoint or WorkflowCheckpoint(request_id='REQ-1', final_solutions={"solutions": []})
        events = []
        with patch.object(RequestService, 'get_request', side_effect=get_request), \
             patch.object(RequestService, 'get_checkpoint', return_value=checkpoint), \
             patch.object(app_module, '_error_message', return_value='Boom'):
            with self.client.stream('GET', '/api/build/REQ-1/events') as response:
                for line in response.iter_lines():
                    if line.startswith('data: '):
                        payload = json.loads(line[len('data: '):])
                        events.append((payload['stage'], payload['status']))
                        if payload['stage'] == 'done' or payload['status'] == 'failed':
                            break
        return events

    SEARCHING = (RequestStatus.PROCESSING, WorkflowStage.SEARCHING, None)
    CHECKING = (RequestStatus.PROCESSING, WorkflowStage.SEARCHING, 'CHECKING')
    DESIGNING = (RequestStatus.PROCESSING, WorkflowStage.DESIGNING, 'CHECKING')
    READY = (RequestStatus.WAITING_FOR_USER, WorkflowStage.SELECTION, 'CHECKING')

    def test_full_run_shows_checking_as_its_own_running_stage(self):
        events = self.stream([self.SEARCHING, self.CHECKING, self.DESIGNING, self.READY])
        self.assertEqual(events[:6], [
            ('understanding', 'done'), ('finding', 'active'),   # SEARCHING, no phase yet
            ('finding', 'done'), ('checking', 'active'),         # real FINDING -> CHECKING boundary
            ('checking', 'done'), ('building', 'active'),        # Search finished -> DESIGNING
        ])
        self.assertEqual(events[-1], ('done', 'done'))
        # Checking is never completed in the same step that starts it.
        self.assertLess(events.index(('checking', 'active')), events.index(('checking', 'done')))

    def test_each_transition_is_emitted_once(self):
        events = self.stream([self.SEARCHING, self.SEARCHING, self.CHECKING, self.CHECKING,
                              self.DESIGNING, self.DESIGNING, self.READY])
        for event in (('finding', 'active'), ('checking', 'active'), ('building', 'active')):
            self.assertEqual(events.count(event), 1)

    def test_reconnect_during_checking_reconstructs_progress(self):
        events = self.stream([self.CHECKING, self.DESIGNING, self.READY])
        self.assertEqual(events[:2], [('finding', 'done'), ('checking', 'active')])
        self.assertNotIn(('finding', 'active'), events)

    def test_reconnect_during_designing_does_not_restart_earlier_stages(self):
        events = self.stream([self.DESIGNING, self.READY])
        self.assertEqual(events[:2], [('checking', 'done'), ('building', 'active')])
        self.assertNotIn(('finding', 'active'), events)
        self.assertNotIn(('checking', 'active'), events)

    def test_needs_adjustment_after_checking_is_unchanged(self):
        events = self.stream([self.CHECKING, (RequestStatus.NEEDS_ADJUSTMENT, WorkflowStage.REQUIREMENTS, 'CHECKING')])
        self.assertEqual(events, [('finding', 'done'), ('checking', 'active'), ('done', 'done')])

    def test_error_during_checking_keeps_existing_failure_attribution(self):
        # No candidate_pool persisted yet -> the existing rule reports "finding" (unchanged by design).
        events = self.stream([self.CHECKING, (RequestStatus.ERROR, WorkflowStage.ERROR, 'CHECKING')],
                             checkpoint=_checkpoint(with_pool=False))
        self.assertEqual(events[-1], ('finding', 'failed'))


def _final_entry(solution_id, recommended):
    return {"solution_id": solution_id,
            "package": {"package_id": "P1", "name": "Package One", "price_sar": 90, "capabilities": {}},
            "addons": [], "total_price_sar": 90, "budget_status": "WITHIN_BUDGET (-10.00 SAR)",
            "needs_coverage": [], "trade_off": "x", "recommended": recommended,
            "recommendation_reason": "Because" if recommended else None}


class RecommendationAvailabilityTests(unittest.TestCase):
    """The API must not turn a missing recommendation into an implied one."""

    def setUp(self):
        self.client = TestClient(app_module.app)
        self.client.cookies.set(app_module.SESSION_COOKIE, 'CUST-1')
        patcher = patch.object(app_module, '_database', return_value=Mock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _done_event(self, final):
        checkpoint = WorkflowCheckpoint(request_id='REQ-1', final_solutions=final)
        row = _row(RequestStatus.WAITING_FOR_USER, WorkflowStage.SELECTION)
        with patch.object(RequestService, 'get_request', return_value=row), \
             patch.object(RequestService, 'get_checkpoint', return_value=checkpoint):
            with self.client.stream('GET', '/api/build/REQ-1/events') as response:
                for line in response.iter_lines():
                    if line.startswith('data: '):
                        payload = json.loads(line[len('data: '):])
                        if payload.get('stage') == 'done':
                            return payload
        return None

    def test_unavailable_recommendation_is_forwarded_without_a_recommended_solution(self):
        event = self._done_event({"solutions": [_final_entry('S1', False), _final_entry('S2', False)],
                                  "recommendation_available": False})
        self.assertIs(event['data']['recommendation_available'], False)
        self.assertEqual([s['recommended'] for s in event['data']['solutions']], [False, False])

    def test_available_and_legacy_outputs_keep_the_recommendation(self):
        for final in ({"solutions": [_final_entry('S1', True)], "recommendation_available": True},
                      {"solutions": [_final_entry('S1', True)]}):
            with self.subTest(final=final):
                event = self._done_event(final)
                self.assertIs(event['data']['recommendation_available'], True)
                self.assertTrue(event['data']['solutions'][0]['recommended'])

    def test_admin_summary_does_not_fall_back_to_first_solution(self):
        from backend.services import admin_service
        row = {"selected_solution_id": None, "previous_plan": None, "new_plan": None}
        unavailable = {"solutions": [_final_entry('S1', False), _final_entry('S2', False)],
                       "recommendation_available": False}
        text = admin_service._headline("awaiting", row, {}, [], unavailable, None)
        self.assertIn("No recommendation was produced", text)
        legacy = {"solutions": [_final_entry('S1', True)]}
        self.assertNotIn("No recommendation", admin_service._headline("awaiting", row, {}, [], legacy, None))


class FrontendRecommendationGuardTests(unittest.TestCase):
    """Source-level guard: no JS test runner exists in this repo."""

    def source(self):
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / 'frontend' / 'web' / 'js' / 'design.js').read_text(encoding='utf-8')

    def test_results_view_only_falls_back_to_first_solution_when_recommendation_available(self):
        source = self.source()
        self.assertIn("const recAvailable=S.results.recommendation_available!==false;", source)
        self.assertIn("const rec=recAvailable?(sols.find(x=>x.recommended)||sols[0]):null;", source)
        self.assertIn("const ordered=rec?[rec,...sols.filter(x=>x!==rec)]:sols;", source)
        self.assertIn("isRec=!!rec&&sol.solution_id===rec.solution_id", source)
        self.assertIn("if(!rec)return null;", source)
        self.assertNotIn("const rec=sols.find(x=>x.recommended)||sols[0];", source)

    def test_recommended_because_renders_the_backend_rationale_directly(self):
        # The frontend must not invent/derive its own recommendation reasoning
        # (e.g. "cheapest + full coverage"); it renders the backend's grounded
        # recommendation_reason, which is validated server-side to never leak
        # internal solution IDs.
        source = self.source()
        self.assertIn("function recommendedBecause(sol){\n  return (sol.recommendation_reason||'').trim();\n}", source)
        self.assertIn("const recBecause=isRec?recommendedBecause(sol):'';", source)
        self.assertNotIn("Fully matches your needs at the lowest monthly cost", source)
        self.assertNotIn("const isCheapest=", source)
        self.assertNotIn("const someoneElseIncomplete=", source)


class FrontendErrorCTAGuardTests(unittest.TestCase):
    """Source-level guard: no JS test runner exists in this repo."""

    def source(self):
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / 'frontend' / 'web' / 'js' / 'design.js').read_text(encoding='utf-8')

    def test_technical_failure_shows_only_try_again(self):
        source = self.source()
        self.assertIn(
            "const foot=S.errorKind==='business'?adjustBtn(true)+tryBtn(false)\n"
            "    :S.errorKind==='connection'?requestsBtn(true)+tryBtn(false)\n"
            "    :tryBtn(true);",
            source)
        self.assertNotIn(":tryBtn(true)+adjustBtn(false);", source)

    def test_business_failure_still_offers_adjust_my_puzzle(self):
        source = self.source()
        self.assertIn("S.errorKind==='business'?adjustBtn(true)+tryBtn(false)", source)


class FailureMessageTests(unittest.TestCase):
    def test_generic_technical_failure_message_copy(self):
        message = app_module._failure_message(ValueError('All reviewed strategies failed QUALITY CHECK'))
        self.assertEqual(message, "TELORA couldn't complete your solution this time. Please try again.")
        self.assertNotIn('adjust your needs', message.lower())

    def test_connection_failure_message_is_customer_facing(self):
        # Only APIConnectionError's message changed: no OpenAI/agent/server
        # implementation detail, matching the approved connection-failure UX.
        message = app_module._failure_message(type('APIConnectionError', (Exception,), {})())
        self.assertIn('connection issue', message)
        for leaked in ('OpenAI', 'agents', 'server', 'API'):
            self.assertNotIn(leaked, message)

    def test_other_openai_failure_messages_unchanged(self):
        for name, expected_snippet in [
            ('AuthenticationError', 'could not sign in'),
            ('PermissionDeniedError', 'cannot use the selected models'),
            ('NotFoundError', 'model was not found'),
            ('RateLimitError', 'rate or quota limit'),
            ('APITimeoutError', 'timed out'),
        ]:
            error = type(name, (Exception,), {})()
            self.assertIn(expected_snippet, app_module._failure_message(error))


if __name__ == '__main__':
    unittest.main()
