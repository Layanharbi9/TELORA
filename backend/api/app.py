"""FastAPI adapter for the existing SQLite-backed TELORA workflow.

The browser's customer picker creates a demo cookie for local prototyping. The
cookie is an ownership selector, not authentication; deploy behind a real
identity provider before exposing customer data publicly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from backend.database.db import SearchDatabase
from backend.schemas.requirements import NEEDS, NeedLevel, NeedSelection, Requirements
from backend.services.admin_service import AdminService
from backend.services.current_plan_service import CurrentPlanService
from backend.services.history_service import HistoryService
from backend.services.executor import WEB_EXECUTOR_ID
from backend.services.request_service import BUILD_STATES, InvalidLifecycleTransition, RequestService
from backend.services.tracing_service import environment_name, traced_action
from backend.tools.apply_tool import apply_selected_solution
from backend.workflow.graph import build_workflow_graph


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "frontend" / "web"
SESSION_COOKIE = "telora_demo_customer"
NEED_ALIASES = {"Multi-device / Extra SIM": "Multiple Devices"}
logger = logging.getLogger(__name__)

try:  # The agents read OPENAI_API_KEY etc. from the environment; a project .env fills it for the web server.
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:  # python-dotenv is optional; exported shell variables still work.
    pass

app = FastAPI(title="TELORA API", version="1.0.0")

MISSING_KEY_MESSAGE = ("TELORA's agents are not configured on the server: OPENAI_API_KEY is not set. "
                       "Add it to the .env file at the project root and restart the server.")
GENERIC_FAILURE = "TELORA couldn't complete your solution this time. Please try again."


def _llm_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _failure_message(error: BaseException) -> str:
    """Customer-safe reason for a failed build; never echoes raw provider messages."""
    name = type(error).__name__
    if name == "OpenAIError" and not _llm_configured():
        return MISSING_KEY_MESSAGE
    return {
        "AuthenticationError": "TELORA's agents could not sign in to OpenAI. Check OPENAI_API_KEY and restart the server.",
        "PermissionDeniedError": "The configured OpenAI key cannot use the selected models. Check OPENAI_MODEL, JUDGE_MODEL and DESIGN_MODEL.",
        "NotFoundError": "A configured OpenAI model was not found. Check OPENAI_MODEL, JUDGE_MODEL and DESIGN_MODEL.",
        "RateLimitError": "TELORA's agents hit the OpenAI rate or quota limit. Wait a moment and try again.",
        "APIConnectionError": "TELORA couldn't complete your solution because of a connection issue. Check your connection and try again.",
        "APITimeoutError": "TELORA's agents timed out while building your solution. Please try again.",
    }.get(name, GENERIC_FAILURE)


class NeedInput(BaseModel):
    need: str
    priority: int = Field(ge=1, le=3)
    level: NeedLevel


class BuildInput(BaseModel):
    needs: list[NeedInput] = Field(min_length=3, max_length=3)
    # Matches the budget slider in the web form (50 to 900 SAR).
    preferred_budget_sar: float = Field(gt=0, le=900, allow_inf_nan=False)

    @model_validator(mode="after")
    def all_priorities_once(self):
        if {item.priority for item in self.needs} != {1, 2, 3}:
            raise ValueError("Exactly priorities 1, 2, and 3 are required")
        normalized_needs = {NEED_ALIASES.get(item.need, item.need) for item in self.needs}
        if len(normalized_needs) != 3:
            raise ValueError("Choose three different needs")
        unknown = [item.need for item in self.needs if NEED_ALIASES.get(item.need, item.need) not in NEEDS]
        if unknown:
            raise ValueError(f"Unknown needs: {unknown}")
        return self


class DemoSessionInput(BaseModel):
    customer_id: str = Field(min_length=1)


class ApplyInput(BaseModel):
    solution_id: str = Field(min_length=1)


def _database() -> SearchDatabase:
    return SearchDatabase()


def _customer_id(request: Request) -> str:
    customer_id = request.cookies.get(SESSION_COOKIE)
    if not customer_id:
        raise HTTPException(status_code=401, detail="Choose a customer to start the demo session")
    return customer_id


def _customer(database: SearchDatabase, customer_id: str) -> dict:
    with closing(database._connect()) as connection:
        row = connection.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Unknown or expired customer session")
    return dict(row)


def _plan_response(plan: dict | None) -> dict | None:
    if plan is None:
        return None
    details = plan.get("package_details") or {}
    base = {
        "package_id": plan.get("package_id"),
        "package_name": plan.get("package_name") or "No package",
        "monthly_price_sar": plan.get("package_monthly_price_sar"),
        "data_gb": plan.get("package_data_gb"),
        "voice_minutes": plan.get("package_voice_minutes"),
        "sms": plan.get("package_sms"),
        "social_data_gb": plan.get("package_social_data_gb"),
        "streaming_benefit": details.get("streaming_benefit"),
        "gaming_benefit": details.get("gaming_benefit"),
        "roaming_data_gb": plan.get("package_roaming_data_gb"),
        "international_minutes": plan.get("package_international_minutes"),
        "multi_sim": plan.get("package_multi_sim"),
        "data_sharing": plan.get("package_data_sharing"),
        "included_benefits": plan.get("package_included_benefits", []),
    }
    addons = plan.get("addon_details", [])
    updated_by = None
    if plan.get("updated_at"):
        # Find the successful TELORA order that most recently set this plan.
        database = _database()
        with closing(database._connect()) as connection:
            row = connection.execute("""SELECT request_id, applied_at, previous_package_names_json,
                previous_addon_names_json, previous_total_price FROM orders
                WHERE customer_id=? AND status='ACTIVE' ORDER BY julianday(applied_at) DESC LIMIT 1""",
                                     (plan.get("customer_id"),)).fetchone()
        if row:
            previous_names = _json(row["previous_package_names_json"], [])
            updated_by = {"request_id": row["request_id"], "applied_at": row["applied_at"],
                          "previous_plan": {"name": previous_names[0] if previous_names else None,
                                            "addons": _json(row["previous_addon_names_json"], []),
                                            "total_price": row["previous_total_price"]}}
    return {"base_package": base, "active_addons": addons,
            "total_monthly_price": plan.get("total_price"), "updated_by": updated_by,
            "updated_at": plan.get("updated_at")}


def _plan_summary(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    names = snapshot.get("package_names") or []
    return {"name": names[0] if names else "—",
            "package_ids": snapshot.get("package_ids", []),
            "addon_names": snapshot.get("addon_names", []),
            "total_price": snapshot.get("total_price")}


# Customer-facing status comes from the persisted workflow (status, stage), the
# lifecycle source of truth; business_status is order/reporting bookkeeping.
CUSTOMER_STATUS = {
    ("PROCESSING", "SEARCHING"): "processing",
    ("PROCESSING", "DESIGNING"): "processing",
    ("PROCESSING", "VALIDATING"): "processing",
    ("PROCESSING", "PREPARING_SOLUTIONS"): "processing",
    ("PROCESSING", "APPLYING"): "applying",
    ("WAITING_FOR_USER", "SELECTION"): "awaiting_selection",
    ("COMPLETED", "COMPLETED"): "completed",
    ("CANCELLED", "CANCELLED"): "cancelled",
    ("NEEDS_ADJUSTMENT", "REQUIREMENTS"): "needs_adjustment",
    ("ERROR", "ERROR"): "failed",
}


def _customer_status(row: dict) -> str:
    status = CUSTOMER_STATUS.get((row.get("status"), row.get("stage")))
    if status is None:
        # Never present an unknown/inconsistent pair as Processing: nothing is working on it.
        logger.warning("Request %s has an unexpected lifecycle state %s/%s",
                       row.get("request_id"), row.get("status"), row.get("stage"))
        return "failed"
    return status


def _request_summary(row: dict) -> dict:
    checkpoint = _database().get_checkpoint(row["request_id"])
    final = checkpoint.final_solutions if checkpoint else None
    solutions = final.get("solutions", []) if isinstance(final, dict) else []
    business_status = row.get("business_status")
    status = _customer_status(row)
    requirements = row.get("requirements") if isinstance(row.get("requirements"), dict) else {}
    previous = row.get("previous_plan")
    new = row.get("new_plan")
    return {"request_id": row["request_id"], "created_at": row["created_at"],
            "updated_at": row.get("updated_at"), "status": status,
            "business_status": business_status, "solutions_count": len(solutions),
            # The customer's own needs/budget, so View solutions can restore their puzzle.
            "needs": _admin_needs(requirements), "budget": requirements.get("preferred_budget_sar"),
            "previous_plan": _plan_summary(previous), "new_plan": _plan_summary(new),
            "order_id": row.get("order_id"), "error_message": row.get("error_message")}


def _recommendation_available(final) -> bool:
    # Outputs persisted before recommendation_available existed always carried a recommendation.
    return bool(final.get("recommendation_available", True)) if isinstance(final, dict) else True


def _frontend_solutions(request_service: RequestService, solutions: list[dict]) -> list[dict]:
    db = request_service.database
    result = []
    for rank, item in enumerate(solutions, 1):
        package = item.get("package", {})
        capabilities = package.get("capabilities", {})
        addons = []
        with closing(db._connect()) as connection:
            for addon in item.get("addons", []):
                row = connection.execute("SELECT category FROM addons WHERE addon_id=?",
                                         (addon.get("addon_id"),)).fetchone()
                addons.append({"addon_id": addon["addon_id"], "name": addon["name"],
                               "price": addon["price_sar"], "category": row[0] if row else None})
        coverage = item.get("needs_coverage", [])
        covered, partial = [], []
        for entry in coverage:
            need = entry.get("need")
            if need == "Multiple Devices":
                need = "Multi-device / Extra SIM"
            row = {"need": need, "detail": entry.get("detail") or entry.get("evidence") or ""}
            (covered if entry.get("coverage") == "COVERED" else partial).append(row)
        tradeoff = item.get("trade_off", "")
        result.append({
            "solution_id": item["solution_id"], "rank": rank, "recommended": item.get("recommended", False),
            "recommendation_reason": item.get("recommendation_reason"),
            "base_package": {"package_id": package["package_id"], "name": package["name"],
                             "price": package["price_sar"], "capabilities": capabilities},
            "addons": addons, "total_monthly_price": item["total_price_sar"],
            "needs_covered": covered, "needs_not_fully_covered": partial,
            "why": item.get("recommendation_reason") or tradeoff,
            "strengths": [f"{entry['need']}: {entry['detail']}" for entry in covered],
            "trade_offs": [tradeoff] if tradeoff else [], "budget_status": item.get("budget_status"),
        })
    return result


def _run_build(request_id: str, customer_id: str, requirements: Requirements) -> None:
    service = RequestService(_database())

    def workflow_action():
        service.start_customer_request(customer_id, requirements, request_id=request_id,
                                       executor_id=WEB_EXECUTOR_ID)
        build_workflow_graph(request_service=service).invoke({"request_id": request_id})
        request_row = service.get_request(request_id)
        return {"request_id": request_id, "status": request_row.status.value if request_row else "MISSING"}

    try:
        traced_action("telora.workflow", workflow_action, request_id=request_id,
                      customer_id=customer_id, workflow_id=request_id,
                      metadata={"agent_system": "TELORA", "workflow_version": "1", "environment": environment_name()},
                      tags=("workflow", "frontend"))
    except Exception as error:
        logger.exception("TELORA web workflow failed for request %s", request_id)
        _settle_build(service, request_id, _failure_message(error))
    except BaseException:
        # Interrupted while the process is still alive (e.g. SystemExit in this thread).
        logger.exception("TELORA web workflow was interrupted for request %s", request_id)
        _settle_build(service, request_id, GENERIC_FAILURE)
        raise
    else:
        _settle_build(service, request_id, None)


def _settle_build(service: RequestService, request_id: str, message: str | None) -> None:
    """Completion guard: once the graph has returned or raised, Search/Design is over.

    A row still PROCESSING in a build stage is failed through the existing
    technical-failure path; waiting/terminal/APPLYING rows are never touched.
    `message` is the customer-safe reason for a raised failure.
    """
    try:
        row = service.get_request(request_id)
        if row is not None and (row.status, row.stage) in BUILD_STATES:
            service.mark_error(request_id, allowed_from=BUILD_STATES)
            message = message or GENERIC_FAILURE
        if message:
            with closing(service.database._connect()) as connection, connection:
                connection.execute("UPDATE requests SET error_message=? WHERE request_id=? AND status='ERROR'",
                                   (message, request_id))
    except Exception:
        logger.exception("Could not persist failure for request %s", request_id)


@app.on_event("startup")
def initialize_database() -> None:
    CurrentPlanService(_database()).initialize_missing_plans()
    reconcile_interrupted_builds()


def reconcile_interrupted_builds() -> list[str]:
    """Fail builds left PROCESSING by an earlier (dead) web boot or legacy rows.

    Web builds run in-process, so after a (re)start none of them can still be
    running. Relies on a single web worker (see backend.services.executor).
    """
    try:
        reconciled = RequestService(_database()).reconcile_interrupted_requests(WEB_EXECUTOR_ID)
    except Exception:
        logger.exception("Startup reconciliation of interrupted requests failed")
        return []
    if reconciled:
        logger.warning("Marked %d interrupted request(s) as failed: %s", len(reconciled), ", ".join(reconciled))
    return reconciled


@app.get("/api/demo/customers")
def list_demo_customers():
    return [{"customer_id": row["customer_id"], "name": row["name"]}
            for row in _database().get_customers()]


@app.post("/api/demo/session")
def start_demo_session(payload: DemoSessionInput, response: Response):
    customer = _customer(_database(), payload.customer_id)
    response.set_cookie(SESSION_COOKIE, customer["customer_id"], httponly=True,
                        samesite="lax", secure=False, max_age=60 * 60 * 8, path="/")
    return {"customer_id": customer["customer_id"]}


@app.post("/api/session/end")
def end_session(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    customer = _customer(_database(), _customer_id(request))
    return {"customer_id": customer["customer_id"], "name": customer["name"], "city": customer["city"]}


@app.get("/api/me/plan")
def my_plan(request: Request):
    customer_id = _customer_id(request)
    plan = HistoryService(_database()).get_current_plan(customer_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Current plan not found")
    return _plan_response(plan)


@app.get("/api/me/requests")
def my_requests(request: Request):
    customer_id = _customer_id(request)
    return [_request_summary(row) for row in HistoryService(_database()).get_customer_requests(customer_id)]


@app.get("/api/health")
def health():
    return {"ok": True, "agents_configured": _llm_configured()}


@app.post("/api/build", status_code=202)
def build(payload: BuildInput, request: Request, background_tasks: BackgroundTasks):
    customer_id = _customer_id(request)
    customer = _customer(_database(), customer_id)
    if not _llm_configured():
        raise HTTPException(status_code=503, detail=MISSING_KEY_MESSAGE)
    ordered = sorted(payload.needs, key=lambda need: need.priority)
    try:
        requirements = Requirements(
            NeedSelection(NEED_ALIASES.get(ordered[0].need, ordered[0].need), ordered[0].level),
            NeedSelection(NEED_ALIASES.get(ordered[1].need, ordered[1].need), ordered[1].level),
            NeedSelection(NEED_ALIASES.get(ordered[2].need, ordered[2].need), ordered[2].level),
            payload.preferred_budget_sar,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    request_id = f"REQ-{uuid4().hex.upper()}"
    background_tasks.add_task(_run_build, request_id, customer["customer_id"], requirements)
    return {"request_id": request_id}


def _error_message(request_id: str) -> str | None:
    with closing(_database()._connect()) as connection:
        row = connection.execute("SELECT error_message FROM requests WHERE request_id=?", (request_id,)).fetchone()
    return row[0] if row else None


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


@app.get("/api/build/{request_id}/events")
async def build_events(request_id: str, request: Request):
    customer_id = _customer_id(request)
    service = RequestService(_database())
    row = service.get_request(request_id)
    if row is not None and row.customer_id != customer_id:
        raise HTTPException(status_code=404, detail="Request not found")

    async def events():
        last_stage = None
        started_at = asyncio.get_running_loop().time()
        last_heartbeat = started_at
        while True:
            if await request.is_disconnected():
                return
            item = await asyncio.to_thread(service.get_request, request_id)
            if item is None:
                if asyncio.get_running_loop().time() - started_at > 15:
                    yield _sse({"request_id": request_id, "stage": "understanding", "status": "failed",
                                "message": "TELORA could not start this request. Please try again."})
                    return
                await asyncio.sleep(0.2)
                continue
            checkpoint = await asyncio.to_thread(service.get_checkpoint, request_id)
            stage = item.stage.value
            # CHECKING is progress metadata inside the persisted SEARCHING
            # stage (set by Search at its real FINDING -> CHECKING boundary),
            # not a lifecycle stage. `progress` is what the stream has shown;
            # an earlier-stage event implicitly completes prior UI stages, so a
            # (re)connect straight into CHECKING or DESIGNING replays correctly.
            progress = stage
            if stage == "SEARCHING" and await asyncio.to_thread(
                    service.database.get_search_phase, request_id) == "CHECKING":
                progress = "CHECKING"
            if progress == "SEARCHING" and last_stage != "SEARCHING":
                yield _sse({"request_id": request_id, "stage": "understanding", "status": "done"})
                yield _sse({"request_id": request_id, "stage": "finding", "status": "active"})
            elif progress == "CHECKING" and last_stage != "CHECKING":
                yield _sse({"request_id": request_id, "stage": "finding", "status": "done"})
                yield _sse({"request_id": request_id, "stage": "checking", "status": "active"})
            elif progress == "DESIGNING" and last_stage != "DESIGNING":
                yield _sse({"request_id": request_id, "stage": "checking", "status": "done"})
                yield _sse({"request_id": request_id, "stage": "building", "status": "active"})
            if progress != last_stage:
                last_stage = progress
            if item.status.value == "WAITING_FOR_USER" and stage == "SELECTION":
                final = checkpoint.final_solutions if checkpoint and isinstance(checkpoint.final_solutions, dict) else {}
                solutions = final.get("solutions", [])
                output = _frontend_solutions(service, solutions)
                yield _sse({"request_id": request_id, "stage": "finding", "status": "done"})
                yield _sse({"request_id": request_id, "stage": "checking", "status": "done"})
                yield _sse({"request_id": request_id, "stage": "building", "status": "done"})
                yield _sse({"request_id": request_id, "stage": "done", "status": "done",
                            "data": {"solutions": output,
                                     "recommendation_available": _recommendation_available(final)}})
                return
            if item.status.value == "NEEDS_ADJUSTMENT":
                yield _sse({"request_id": request_id, "stage": "done", "status": "done",
                            "data": {"solutions": []}})
                return
            if item.status.value in {"COMPLETED", "CANCELLED"}:
                # Terminal without selectable solutions: end the stream instead of polling forever.
                yield _sse({"request_id": request_id, "stage": "done", "status": item.status.value.lower()})
                return
            if item.status.value == "ERROR":
                # _run_build stores the reason just after the workflow marks ERROR; give it a moment to land.
                message = None
                for _ in range(10):
                    message = await asyncio.to_thread(_error_message, request_id)
                    if message:
                        break
                    await asyncio.sleep(0.2)
                # Attribute the failed stage from the persisted checkpoint, not
                # connection-local last_stage: mark_error() always sets stage
                # to ERROR, so last_stage can never equal DESIGNING at this
                # point, and a fresh/reconnected connection never observed the
                # DESIGNING transition at all. candidate_pool is only ever
                # persisted once Search succeeds, so its presence is an
                # authoritative, connection-independent signal.
                failed_stage = "building" if checkpoint and checkpoint.candidate_pool is not None else "finding"
                yield _sse({"request_id": request_id, "stage": failed_stage,
                            "status": "failed", "message": message or GENERIC_FAILURE})
                return
            now = asyncio.get_running_loop().time()
            if now - last_heartbeat >= 15:
                yield ": keep-alive\n\n"
                last_heartbeat = now
            await asyncio.sleep(0.35)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/requests/{request_id}/apply")
def apply(request_id: str, payload: ApplyInput, request: Request):
    customer_id = _customer_id(request)
    service = RequestService(_database())
    saved = service.get_request(request_id)
    if saved is None or saved.customer_id != customer_id:
        raise HTTPException(status_code=404, detail="Request not found")
    checkpoint = service.get_checkpoint(request_id)
    solutions = checkpoint.final_solutions.get("solutions", []) if checkpoint and isinstance(checkpoint.final_solutions, dict) else []
    selected = next((item for item in solutions if item.get("solution_id") == payload.solution_id), None)
    if selected is None:
        raise HTTPException(status_code=422, detail="Selected solution is not part of this request")
    try:
        def select_and_apply():
            service.select_solution(request_id, selected)
            return apply_selected_solution(
                customer_id, request_id, payload.solution_id,
                package_ids=[selected["package"]["package_id"]],
                addon_ids=[addon["addon_id"] for addon in selected.get("addons", [])],
                database=service.database, require_saved_selection=True,
            )

        root_run_id = service.database.get_workflow_trace_root(request_id)
        result = traced_action("customer.selection_apply", select_and_apply,
                               request_id=request_id, customer_id=customer_id,
                               workflow_id=request_id, tags=("workflow", "selection", "apply"),
                               parent_run_id=root_run_id)
    except (ValueError, KeyError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if result.get("status") != "ACTIVE":
        raise HTTPException(status_code=409, detail=result.get("error_message", "Application failed"),
                            headers={"X-TELORA-Result": "failed"})
    return {"success": True, "order_id": result.get("order_id"),
            "request_id": request_id, "status": result["status"],
            "plan": _plan_response(HistoryService(service.database).get_current_plan(customer_id))}


@app.post("/api/requests/{request_id}/cancel")
def cancel(request_id: str, request: Request):
    """Customer declines every generated solution; the current plan is untouched."""
    customer_id = _customer_id(request)
    service = RequestService(_database())
    saved = service.get_request(request_id)
    if saved is None or saved.customer_id != customer_id:
        raise HTTPException(status_code=404, detail="Request not found")
    try:
        changed = service.cancel_request(request_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Request not found") from error
    except InvalidLifecycleTransition as error:
        raise HTTPException(status_code=409, detail="This request can no longer be cancelled.") from error
    return {"success": True, "request_id": request_id, "status": "cancelled", "changed": changed}


# ---------- admin console (Demo Mode: unauthenticated, local prototype only) ----------
# Every admin view is read-only and computed from SQLite by AdminService; the
# LangSmith runtime view (latency, tokens, cost) is the only external read.


def _json(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _admin_needs(requirements: dict) -> list[dict]:
    needs = []
    for priority, key in enumerate(("primary_need", "secondary_need", "third_need"), 1):
        item = requirements.get(key)
        if item:
            name = item.get("need")
            needs.append({"priority": priority, "need": "Multi-device / Extra SIM" if name == "Multiple Devices" else name,
                          "level": item.get("level")})
    return needs


@app.get("/api/admin/overview")
def admin_overview():
    return AdminService(_database()).overview()


@app.get("/api/admin/requests")
def admin_requests():
    return AdminService(_database()).requests()


@app.get("/api/admin/requests/{request_id}")
def admin_request_journey(request_id: str):
    journey = AdminService(_database()).journey(request_id)
    if journey is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return journey


@app.get("/api/admin/customers")
def admin_customers():
    return AdminService(_database()).customers()


@app.get("/api/admin/customers/{customer_id}")
def admin_customer(customer_id: str):
    customer = AdminService(_database()).customer(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@app.get("/api/admin/agents")
def admin_agents():
    return AdminService(_database()).agents()


@app.get("/api/admin/catalog")
def admin_catalog():
    return AdminService(_database()).catalog()


@app.get("/api/admin/system")
def admin_system(days: int = 7, refresh: bool = False):
    from backend.services.monitoring_service import system_overview
    return system_overview(days, refresh=refresh)


@app.get("/", include_in_schema=False)
def index():
    page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    # Version every script and stylesheet by mtime so browsers never run a stale copy after an edit.
    for asset in [*(WEB_ROOT / "js").glob("*.js"), *(WEB_ROOT / "css").glob("*.css")]:
        relative = asset.relative_to(WEB_ROOT).as_posix()
        version = asset.stat().st_mtime_ns
        page = page.replace(f'src="{relative}"', f'src="{relative}?v={version}"')
        page = page.replace(f'href="{relative}"', f'href="{relative}?v={version}"')
    return HTMLResponse(page, headers={"Cache-Control": "no-store, max-age=0"})


app.mount("/css", StaticFiles(directory=WEB_ROOT / "css"), name="css")
app.mount("/js", StaticFiles(directory=WEB_ROOT / "js"), name="js")
