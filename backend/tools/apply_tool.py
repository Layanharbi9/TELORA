"""Deterministic customer-selected solution application.

Activation in this prototype is simulated locally; this module makes no telecom
provider or network calls. Names, benefits, and prices are read from SQLite.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from langchain_core.tools import tool

from backend.database.db import SearchDatabase
from backend.schemas.application import ApplySolutionInput
from backend.services.current_plan_service import CurrentPlanService
from backend.services.tracing_service import record_event, trace_span


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: str | None, fallback):
    return json.loads(value) if value else fallback


def _snapshot(plan) -> dict:
    if plan is None:
        return {"package_ids": [], "package_names": [], "addon_ids": [], "addon_names": [], "total_price": None}
    return {
        "customer_id": plan["customer_id"], "customer_name": plan["customer_name"],
        "package_ids": [plan["package_id"]] if plan["package_id"] else [],
        "package_names": [plan["package_name"]] if plan["package_name"] else [],
        "package_data_gb": plan["package_data_gb"], "package_voice_minutes": plan["package_voice_minutes"],
        "package_sms": plan["package_sms"], "package_monthly_price_sar": plan["package_monthly_price_sar"],
        "package_social_data_gb": plan["package_social_data_gb"],
        "package_roaming_data_gb": plan["package_roaming_data_gb"],
        "package_international_minutes": plan["package_international_minutes"],
        "package_multi_sim": plan["package_multi_sim"], "package_data_sharing": plan["package_data_sharing"],
        "package_contract_months": plan["package_contract_months"],
        "package_included_benefits": _loads(plan["package_included_benefits_json"], []),
        "package_details": _loads(plan["package_details_json"], {}),
        "addon_ids": _loads(plan["addon_ids_json"], []),
        "addon_names": _loads(plan["addon_names_json"], []),
        "addon_details": _loads(plan["addon_details_json"], []),
        "total_price": plan["total_price"], "created_at": plan["created_at"], "updated_at": plan["updated_at"],
    }


def _catalog_composition(connection, package_ids: list[str], addon_ids: list[str]):
    return CurrentPlanService.catalog_composition(connection, package_ids, addon_ids)


def _apply_selected_solution(
    customer_id: str,
    request_id: str,
    selected_solution_id: str,
    *,
    package_ids: list[str],
    addon_ids: list[str],
    database: SearchDatabase | None = None,
    activation: Callable[[], None] | None = None,
    require_saved_selection: bool = False,
) -> dict:
    """Apply the solution selected by the customer; never recommends/selects.

    Optional client component IDs are checked against the persisted final
    solution. The persisted solution and original catalog remain authoritative.
    `activation` is an injectable simulation hook; raising marks the order failed.
    """
    database = database or SearchDatabase()
    order_id = f"ORD-{uuid4().hex.upper()}"
    timestamp = _now()
    simulation = True
    with closing(database._connect()) as connection:
        request = connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if request is None:
            raise ValueError(f"Unknown request: {request_id}")
        record_event("apply.validation.request", inputs={"request_id": request_id},
                     outputs={"exists": True}, metadata={"request_id": request_id,
                     "customer_id": customer_id, "tool": "apply-tool", "stage": "REQUEST_VALIDATION"},
                     tags=("apply-tool", "validation"))
        if request["customer_id"] != customer_id:
            raise ValueError("Request does not belong to this customer")
        record_event("apply.validation.ownership", inputs={"request_id": request_id,
                     "customer_id": customer_id}, outputs={"valid": True},
                     metadata={"request_id": request_id, "customer_id": customer_id,
                               "tool": "apply-tool", "stage": "OWNERSHIP_VALIDATION"},
                     tags=("apply-tool", "validation"))
        prior_order = connection.execute("SELECT * FROM orders WHERE request_id=?", (request_id,)).fetchone()
        if prior_order is not None:
            if prior_order["status"] in ("PENDING", "PROCESSING"):
                return {"success": False, "request_id": request_id, "order_id": prior_order["order_id"],
                        "customer_id": customer_id, "selected_solution_id": prior_order["selected_solution_id"],
                        "status": prior_order["status"], "error_message": "Order is still being processed",
                        "simulation": True}
            prior = dict(prior_order)
            return {
                "success": prior["status"] == "ACTIVE", "request_id": request_id,
                "order_id": prior["order_id"], "customer_id": customer_id,
                "selected_solution_id": prior["selected_solution_id"], "status": prior["status"],
                "package_ids": _loads(prior["package_ids_json"], []),
                "package_names": _loads(prior["package_names_json"], []),
                "addon_ids": _loads(prior["addon_ids_json"], []),
                "addon_names": _loads(prior["addon_names_json"], []),
                "total_price": prior["total_price"], "error_message": prior["error_message"],
                "previous_plan": _loads(prior["previous_plan_json"], {
                    "package_ids": _loads(prior["previous_package_ids_json"], []),
                    "package_names": _loads(prior["previous_package_names_json"], []),
                    "addon_ids": _loads(prior["previous_addon_ids_json"], []),
                    "addon_names": _loads(prior["previous_addon_names_json"], []),
                    "total_price": prior["previous_total_price"],
                }),
                "new_plan": _loads(prior["new_plan_json"], {}),
                "created_at": prior["created_at"], "updated_at": prior["updated_at"],
                "applied_at": prior["applied_at"], "completed_at": prior["completed_at"],
                "simulation": True,
            }
        customer = connection.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone()
        if customer is None:
            raise ValueError(f"Unknown customer: {customer_id}")
        record_event("apply.validation.customer", inputs={"customer_id": customer_id},
                     outputs={"exists": True}, metadata={"request_id": request_id,
                     "customer_id": customer_id, "tool": "apply-tool", "stage": "CUSTOMER_VALIDATION"},
                     tags=("apply-tool", "validation"))
        waiting = request["status"] == "WAITING_FOR_USER" and request["stage"] == "SELECTION"
        selected_for_apply = request["status"] == "PROCESSING" and request["stage"] == "APPLYING"
        if require_saved_selection and not selected_for_apply:
            raise ValueError("Customer selection must be saved before applying the solution")
        if not (waiting or selected_for_apply):
            raise ValueError("Request is not waiting for customer selection")
        checkpoint = connection.execute("SELECT final_solutions_json, selected_solution_json FROM workflow_checkpoints WHERE request_id=?",
                                        (request_id,)).fetchone()
        if checkpoint is None or not checkpoint[0]:
            raise ValueError("No persisted final solutions are available for this request")
        final_output = _loads(checkpoint[0], {})
        candidates = final_output.get("solutions", []) if isinstance(final_output, dict) else []
        selected = next((item for item in candidates if item.get("solution_id") == selected_solution_id), None)
        if selected is None:
            raise ValueError(f"Unknown selected solution: {selected_solution_id}")
        record_event("apply.validation.solution", inputs={"request_id": request_id,
                     "solution_ids": [item.get("solution_id") for item in candidates]},
                     outputs={"selected_solution_id": selected_solution_id, "valid": True},
                     metadata={"request_id": request_id, "customer_id": customer_id,
                               "tool": "apply-tool", "stage": "SOLUTION_VALIDATION"},
                     tags=("apply-tool", "validation"))
        if selected_for_apply:
            saved_choice = _loads(checkpoint["selected_solution_json"], {})
            saved_id = saved_choice.get("solution_id", saved_choice.get("id")) if isinstance(saved_choice, dict) else None
            if saved_id != selected_solution_id:
                raise ValueError("Selected solution does not match the customer's persisted choice")
        canonical_package_ids = [selected["package"]["package_id"]]
        canonical_addon_ids = [item["addon_id"] for item in selected.get("addons", [])]
        if package_ids is not None and package_ids != canonical_package_ids:
            raise ValueError("Submitted package IDs do not match the selected solution")
        if addon_ids is not None and addon_ids != canonical_addon_ids:
            raise ValueError("Submitted add-on IDs do not match the selected solution")
        record_event("apply.validation.components", inputs={"package_ids": canonical_package_ids,
                     "addon_ids": canonical_addon_ids}, outputs={"valid": True},
                     metadata={"request_id": request_id, "customer_id": customer_id,
                               "tool": "apply-tool", "stage": "COMPONENT_VALIDATION"},
                     tags=("apply-tool", "validation"))
        current = connection.execute("SELECT * FROM current_plans WHERE customer_id=?", (customer_id,)).fetchone()
        if current is None:
            raise ValueError("Customer has no initialized current plan")
        package, addons, total_price = _catalog_composition(connection, canonical_package_ids, canonical_addon_ids)
        previous = _snapshot(current)
        record_event("current_plan.before_apply", inputs={"customer_id": customer_id},
                     outputs=previous, metadata={"request_id": request_id, "customer_id": customer_id,
                     "entity": "current_plans", "stage": "BEFORE_SNAPSHOT"},
                     tags=("database", "current-plan"))
        new_snapshot = CurrentPlanService.selected_plan_snapshot(
            customer_id, customer["name"], package, addons, total_price)

    def record_failure(reason: str, *, order_created: bool) -> dict:
        failed_at = _now()
        with closing(database._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if order_created:
                updated_order = connection.execute(
                    "UPDATE orders SET status='FAILED', error_message=?, updated_at=?, completed_at=? WHERE order_id=?",
                    (reason, failed_at, failed_at, order_id))
                if updated_order.rowcount != 1:
                    raise sqlite3.IntegrityError(f"Could not mark order {order_id} as FAILED")
                connection.execute("INSERT INTO order_status_history(order_id,status,changed_at) VALUES (?, 'FAILED', ?)",
                                   (order_id, failed_at))
            updated_request = connection.execute("""UPDATE requests SET business_status='FAILED', error_message=?, completed_at=?,
                updated_at=?, status='ERROR', stage='ERROR' WHERE request_id=?""",
                               (reason, failed_at, failed_at, request_id))
            if updated_request.rowcount != 1:
                raise sqlite3.IntegrityError(f"Could not mark request {request_id} as FAILED")
        record_event("apply.failure.persisted", inputs={"request_id": request_id, "order_id": order_id,
                     "error_type": "APPLICATION_FAILURE"},
                     outputs={"order_status": "FAILED" if order_created else None,
                              "request_status": "FAILED", "current_plan_changed": False},
                     metadata={"request_id": request_id, "customer_id": customer_id,
                               "order_id": order_id if order_created else None,
                               "tool": "apply-tool", "stage": "FAILURE_BOOKKEEPING"},
                     tags=("apply-tool", "failure", "database"))
        return {"success": False, "request_id": request_id, "order_id": order_id if order_created else None,
                "customer_id": customer_id, "selected_solution_id": selected_solution_id,
                "package_ids": new_snapshot["package_ids"], "package_names": new_snapshot["package_names"],
                "addon_ids": new_snapshot["addon_ids"], "addon_names": new_snapshot["addon_names"],
                "total_price": total_price, "previous_plan": previous, "new_plan": None,
                "status": "FAILED", "error_message": reason, "failure_recorded": True, "simulation": simulation,
                "created_at": timestamp, "updated_at": failed_at, "completed_at": failed_at}

    def failure_recording_error(reason: str, error: Exception, *, order_created: bool) -> dict:
        """Never report FAILED as persisted when SQLite rejected bookkeeping."""
        failed_at = _now()
        return {
            "success": False, "request_id": request_id,
            "order_id": order_id if order_created else None, "customer_id": customer_id,
            "selected_solution_id": selected_solution_id,
            "status": "ERROR", "failure_recorded": False,
            "error_message": f"{reason}; failure status could not be saved: {error}",
            "simulation": simulation, "created_at": timestamp,
            "updated_at": failed_at, "completed_at": None,
        }

    def record_failure_safely(reason: str, *, order_created: bool) -> dict:
        try:
            result = record_failure(reason, order_created=order_created)
            return result
        except sqlite3.Error as error:
            record_event("apply.failure_persistence_error", inputs={"request_id": request_id,
                         "order_id": order_id if order_created else None,
                         "error_type": type(error).__name__},
                         outputs={"failure_recorded": False, "status": "ERROR"},
                         metadata={"request_id": request_id, "customer_id": customer_id,
                                   "order_id": order_id if order_created else None,
                                   "tool": "apply-tool", "stage": "FAILURE_PERSISTENCE"},
                         tags=("apply-tool", "failure", "database"))
            return failure_recording_error(reason, error, order_created=order_created)

    # Persist the order before activation so an interrupted simulation remains
    # visible and recoverable as PENDING/PROCESSING.
    try:
        selected_at = _now()
        with closing(database._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""UPDATE requests SET business_status='CUSTOMER_SELECTED',
                selected_solution_id=?, updated_at=? WHERE request_id=?""",
                               (selected_solution_id, selected_at, request_id))
            connection.execute("UPDATE workflow_checkpoints SET selected_solution_json=? WHERE request_id=?",
                               (json.dumps(selected, ensure_ascii=False), request_id))
        record_event("order.pending", inputs={"request_id": request_id, "order_id": order_id},
                     outputs={"status": "PENDING"}, metadata={"request_id": request_id,
                     "customer_id": customer_id, "order_id": order_id, "entity": "orders"},
                     tags=("apply-tool", "order", "database"))
        with closing(database._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""INSERT INTO orders (
                order_id,request_id,customer_id,customer_name,selected_solution_id,
                package_ids_json,package_names_json,addon_ids_json,addon_names_json,total_price,
                previous_package_ids_json,previous_package_names_json,previous_addon_ids_json,previous_addon_names_json,
                previous_total_price,previous_plan_json,new_plan_json,status,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                order_id, request_id, customer_id, customer["name"], selected_solution_id,
                json.dumps(new_snapshot["package_ids"]), json.dumps(new_snapshot["package_names"]),
                json.dumps(new_snapshot["addon_ids"]), json.dumps(new_snapshot["addon_names"]), total_price,
                json.dumps(previous["package_ids"]), json.dumps(previous["package_names"]),
                json.dumps(previous["addon_ids"]), json.dumps(previous["addon_names"]), previous["total_price"],
                json.dumps(previous, ensure_ascii=False), json.dumps(new_snapshot, ensure_ascii=False),
                "PENDING", timestamp, timestamp))
            connection.execute("INSERT INTO order_status_history(order_id,status,changed_at) VALUES (?, 'PENDING', ?)",
                               (order_id, timestamp))
            connection.execute("""UPDATE requests SET business_status='PENDING', selected_solution_id=?,
                order_id=?, updated_at=?, status='PROCESSING', stage='APPLYING' WHERE request_id=?""",
                               (selected_solution_id, order_id, timestamp, request_id))
            connection.execute("UPDATE workflow_checkpoints SET selected_solution_json=? WHERE request_id=?",
                               (json.dumps(selected, ensure_ascii=False), request_id))
        processing_at = _now()
        with closing(database._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE orders SET status='PROCESSING', updated_at=? WHERE order_id=?", (processing_at, order_id))
            connection.execute("INSERT INTO order_status_history(order_id,status,changed_at) VALUES (?, 'PROCESSING', ?)",
                               (order_id, processing_at))
            connection.execute("""UPDATE requests SET business_status='PROCESSING', selected_solution_id=?, order_id=?,
                new_package_ids_json=?, new_package_names_json=?, new_addon_ids_json=?, new_addon_names_json=?, total_price=?, updated_at=?
                ,new_plan_json=?,previous_plan_json=?
                WHERE request_id=?""", (selected_solution_id, order_id, json.dumps(new_snapshot["package_ids"]),
                json.dumps(new_snapshot["package_names"]), json.dumps(new_snapshot["addon_ids"]),
                json.dumps(new_snapshot["addon_names"]), total_price, processing_at,
                json.dumps(new_snapshot, ensure_ascii=False), json.dumps(previous, ensure_ascii=False), request_id))
        record_event("order.processing", inputs={"request_id": request_id, "order_id": order_id},
                     outputs={"status": "PROCESSING"}, metadata={"request_id": request_id,
                     "customer_id": customer_id, "order_id": order_id, "entity": "orders"},
                     tags=("apply-tool", "order", "database"))
        if activation is not None:
            activation()
        record_event("activation.simulation", inputs={"request_id": request_id,
                     "selected_solution_id": selected_solution_id},
                     outputs={"activation": "SIMULATED_SUCCESS"},
                     metadata={"request_id": request_id, "customer_id": customer_id,
                               "order_id": order_id, "tool": "apply-tool"},
                     tags=("apply-tool", "activation"))
    except Exception as error:
        reason = str(error) or error.__class__.__name__
        with closing(database._connect()) as connection:
            existing = connection.execute("SELECT * FROM orders WHERE request_id=?", (request_id,)).fetchone()
            created_order = connection.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if existing is not None and existing["order_id"] != order_id:
            return {"success": existing["status"] == "ACTIVE", "request_id": request_id,
                    "order_id": existing["order_id"], "customer_id": customer_id,
                    "selected_solution_id": existing["selected_solution_id"], "status": existing["status"],
                    "error_message": existing["error_message"], "simulation": simulation}
        record_event("apply.rollback_or_failure", inputs={"request_id": request_id,
                     "order_id": order_id, "error_type": type(error).__name__},
                     outputs={"rolled_back": True}, metadata={"request_id": request_id,
                     "customer_id": customer_id, "order_id": order_id, "stage": "ROLLBACK"},
                     tags=("apply-tool", "failure"))
        return record_failure_safely(reason, order_created=created_order is not None)

    completed_at = _now()
    result = {
        "success": True, "request_id": request_id, "order_id": order_id,
        "customer_id": customer_id, "selected_solution_id": selected_solution_id,
        "package_ids": new_snapshot["package_ids"], "package_names": new_snapshot["package_names"],
        "addon_ids": new_snapshot["addon_ids"], "addon_names": new_snapshot["addon_names"],
        "total_price": total_price, "previous_plan": previous, "new_plan": new_snapshot,
        "status": "ACTIVE", "simulation": simulation,
        "created_at": timestamp, "updated_at": completed_at,
        "applied_at": completed_at, "completed_at": completed_at,
    }
    try:
        with closing(database._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            latest_package, latest_addons, latest_total = _catalog_composition(
                connection, new_snapshot["package_ids"], new_snapshot["addon_ids"])
            if (latest_total != total_price or dict(latest_package) != dict(package)
                    or [dict(item) for item in latest_addons] != [dict(item) for item in addons]):
                raise ValueError("Catalog values changed while the selected solution was processing")
            CurrentPlanService.replace_current_plan_in_transaction(
                connection, customer_id, package["package_id"], new_snapshot["addon_ids"], completed_at)
            connection.execute("UPDATE orders SET status='ACTIVE', updated_at=?, applied_at=?, completed_at=? WHERE order_id=?",
                               (completed_at, completed_at, completed_at, order_id))
            connection.execute("INSERT INTO order_status_history(order_id,status,changed_at) VALUES (?, 'ACTIVE', ?)",
                               (order_id, completed_at))
            connection.execute("""UPDATE requests SET business_status='ACTIVE', error_message=NULL,
                selected_solution_id=?, order_id=?, new_package_ids_json=?, new_package_names_json=?,
                new_addon_ids_json=?, new_addon_names_json=?, total_price=?, completed_at=?, updated_at=?,
                status='COMPLETED', stage='COMPLETED' WHERE request_id=?""", (
                selected_solution_id, order_id, json.dumps(new_snapshot["package_ids"]),
                json.dumps(new_snapshot["package_names"]), json.dumps(new_snapshot["addon_ids"]),
                json.dumps(new_snapshot["addon_names"]), total_price, completed_at, completed_at, request_id))
            connection.execute("UPDATE workflow_checkpoints SET selected_solution_json=?, applied_solution_json=? WHERE request_id=?",
                               (json.dumps(selected, ensure_ascii=False), json.dumps(result, ensure_ascii=False), request_id))
        updated_plan = CurrentPlanService(database).get_current_plan(customer_id)
        record_event("current_plan.after_apply", inputs={"customer_id": customer_id},
                     outputs=updated_plan, metadata={"request_id": request_id,
                     "customer_id": customer_id, "order_id": order_id,
                     "entity": "current_plans", "stage": "AFTER_SNAPSHOT"},
                     tags=("database", "current-plan"))
        record_event("order.active", inputs={"request_id": request_id, "order_id": order_id},
                     outputs={"status": "ACTIVE", "applied_at": completed_at,
                              "completed_at": completed_at}, metadata={"request_id": request_id,
                     "customer_id": customer_id, "order_id": order_id, "entity": "orders"},
                     tags=("apply-tool", "order", "database"))
        record_event("request.completed", inputs={"request_id": request_id, "order_id": order_id},
                     outputs={"status": "ACTIVE", "order_id": order_id,
                              "selected_solution_id": selected_solution_id},
                     metadata={"request_id": request_id, "customer_id": customer_id,
                               "order_id": order_id, "entity": "requests"},
                     tags=("apply-tool", "request", "database"))
        record_event("apply.transaction.commit", inputs={"request_id": request_id,
                     "order_id": order_id}, outputs={"committed": True},
                     metadata={"request_id": request_id, "customer_id": customer_id,
                               "order_id": order_id, "tool": "apply-tool"},
                     tags=("apply-tool", "database"))
    except Exception as error:
        # The atomic activation transaction rolled back; mark the still-processing
        # order/request failed in a new transaction and leave current_plans intact.
        record_event("apply.transaction.rollback", inputs={"request_id": request_id,
                     "order_id": order_id, "error_type": type(error).__name__},
                     outputs={"current_plan_committed": False},
                     metadata={"request_id": request_id, "customer_id": customer_id,
                               "order_id": order_id, "tool": "apply-tool"},
                     tags=("apply-tool", "rollback", "database"))
        return record_failure_safely(str(error) or error.__class__.__name__, order_created=True)
    return result


def apply_selected_solution(*args, **kwargs) -> dict:
    """Return validation failures as structured results and persist them safely."""
    database = kwargs.get("database") or SearchDatabase()
    customer_id = kwargs.get("customer_id", args[0] if args else "")
    request_id = kwargs.get("request_id", args[1] if len(args) > 1 else "")
    selected_id = kwargs.get("selected_solution_id", args[2] if len(args) > 2 else "")
    input_payload = {"customer_id": customer_id, "request_id": request_id,
                     "selected_solution_id": selected_id,
                     "package_ids": kwargs.get("package_ids", []),
                     "addon_ids": kwargs.get("addon_ids", [])}
    agent_run: dict[str, str | None] = {}
    evaluation_written = False

    def apply_and_evaluate():
        nonlocal evaluation_written
        applied = _apply_selected_solution(*args, **kwargs)
        try:
            from backend.evaluation.runtime import evaluate_apply_runtime
            evaluate_apply_runtime(applied, database, request_id=request_id,
                                   agent_run_id=agent_run.get("run_id"),
                                   session_id=agent_run.get("session_id"))
            evaluation_written = True
        except Exception as evaluation_error:
            logging.getLogger(__name__).warning("Runtime Apply evaluation failed (%s).",
                                                type(evaluation_error).__name__)
        return applied

    try:
        result = trace_span(
            "apply_tool", apply_and_evaluate,
            inputs=input_payload,
            metadata={"request_id": request_id, "customer_id": customer_id,
                      "workflow_id": request_id, "tool": "apply-tool", "stage": "APPLY"},
            tags=("apply-tool",), run_type="tool",
            run_context_callback=agent_run.update,
        )
    except ValueError as error:
        database = kwargs.get("database") or SearchDatabase()
        customer_id = kwargs.get("customer_id", args[0] if args else "")
        request_id = kwargs.get("request_id", args[1] if len(args) > 1 else "")
        selected_id = kwargs.get("selected_solution_id", args[2] if len(args) > 2 else "")
        submitted_packages = kwargs.get("package_ids", [])
        submitted_addons = kwargs.get("addon_ids", [])
        timestamp = _now()
        try:
            with closing(database._connect()) as connection, connection:
                current = connection.execute("SELECT * FROM current_plans WHERE customer_id=?", (customer_id,)).fetchone()
                previous = _snapshot(current) if current else None
                updated = connection.execute("""UPDATE requests SET business_status='FAILED', error_message=?, completed_at=?,
                updated_at=?, status='ERROR', stage='ERROR'
                WHERE request_id=? AND customer_id=? AND order_id IS NULL
                  AND ((status='WAITING_FOR_USER' AND stage='SELECTION')
                       OR (status='PROCESSING' AND stage='APPLYING'))""",
                                            (str(error), timestamp, timestamp, request_id, customer_id))
                failure_recorded = updated.rowcount == 1
                if not failure_recorded:
                    persist_error = RuntimeError("No eligible request row was updated")
        except sqlite3.Error as persist_error:
            previous = None
            failure_recorded = False
        result = {"success": False, "request_id": request_id, "order_id": None,
                "customer_id": customer_id, "selected_solution_id": selected_id,
                "package_ids": submitted_packages, "addon_ids": submitted_addons,
                "previous_plan": previous, "new_plan": None, "total_price": None,
                "status": "FAILED" if failure_recorded else "ERROR",
                "failure_recorded": failure_recorded,
                "error_message": (str(error) if failure_recorded else
                                   f"{error}; failure status could not be saved: {persist_error}"), "simulation": True,
                "created_at": timestamp, "updated_at": timestamp, "completed_at": timestamp}
    if not evaluation_written:
        try:
            from backend.evaluation.runtime import evaluate_apply_runtime
            evaluate_apply_runtime(result, database, request_id=request_id,
                                   agent_run_id=agent_run.get("run_id"),
                                   session_id=agent_run.get("session_id"))
        except Exception as evaluation_error:
            logging.getLogger(__name__).warning("Runtime Apply evaluation failed (%s).",
                                                type(evaluation_error).__name__)
    return result


@tool(args_schema=ApplySolutionInput)
def apply_tool(
    customer_id: str,
    request_id: str,
    selected_solution_id: str,
    package_ids: list[str],
    addon_ids: list[str],
) -> dict:
    """Apply the solution the customer already selected for this TELORA request.

    The selection must already be persisted by the customer-selection step.
    Package and add-on names and prices are resolved from the SQLite catalog;
    telecom activation is simulated by this prototype.
    """
    return apply_selected_solution(
        customer_id=customer_id,
        request_id=request_id,
        selected_solution_id=selected_solution_id,
        package_ids=package_ids,
        addon_ids=addon_ids,
        require_saved_selection=True,
    )


# Explicit alias for registries that use descriptive variable names.
apply_solution_tool = apply_tool
