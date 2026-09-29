"""Low-level workflow persistence, independent of agent orchestration."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from typing import Any

from backend.schemas.checkpoint import WorkflowCheckpoint
from backend.schemas.request import Request, RequestStatus, WorkflowStage, utc_now
from backend.schemas.requirements import Requirements
from backend.services.tracing_service import record_event


OUTPUT_FIELDS = tuple(name for name in WorkflowCheckpoint.model_fields if name != "request_id")
TRANSITION_REQUEST_FIELDS = frozenset({
    "business_status", "selected_solution_id", "new_package_ids_json", "new_package_names_json",
    "new_addon_ids_json", "new_addon_names_json", "total_price", "new_plan_json",
    "recommended_solution_ids_json", "completed_at",
})
# PROCESSING stages in which Search/Design agents run inside an executor process.
# APPLYING is deliberately excluded: interrupted Apply keeps its own recovery semantics.
BUILD_STAGES = (WorkflowStage.SEARCHING, WorkflowStage.DESIGNING,
                WorkflowStage.VALIDATING, WorkflowStage.PREPARING_SOLUTIONS)


def recommendation_ids_json(final_solutions) -> str:
    if hasattr(final_solutions, "model_dump"):
        final_solutions = final_solutions.model_dump(mode="json")
    solutions = final_solutions.get("solutions", []) if isinstance(final_solutions, dict) else []
    return json.dumps([item["solution_id"] for item in solutions])


def _plan_snapshot(plan) -> dict | None:
    if plan is None:
        return None
    return {
        "customer_id": plan["customer_id"], "customer_name": plan["customer_name"],
        "package_id": plan["package_id"], "package_name": plan["package_name"],
        "package_data_gb": plan["package_data_gb"], "package_voice_minutes": plan["package_voice_minutes"],
        "package_sms": plan["package_sms"], "package_monthly_price_sar": plan["package_monthly_price_sar"],
        "package_social_data_gb": plan["package_social_data_gb"],
        "package_roaming_data_gb": plan["package_roaming_data_gb"],
        "package_international_minutes": plan["package_international_minutes"],
        "package_multi_sim": plan["package_multi_sim"],
        "package_data_sharing": plan["package_data_sharing"],
        "package_contract_months": plan["package_contract_months"],
        "package_included_benefits": json.loads(plan["package_included_benefits_json"]),
        "package_details": json.loads(plan["package_details_json"]),
        "addon_ids": json.loads(plan["addon_ids_json"]), "addon_names": json.loads(plan["addon_names_json"]),
        "addon_details": json.loads(plan["addon_details_json"]), "total_price": plan["total_price"],
        "created_at": plan["created_at"], "updated_at": plan["updated_at"],
    }


class InvalidLifecycleTransition(ValueError):
    """The persisted request is not in an expected source state."""


class WorkflowPersistence:
    """Mixin using SearchDatabase's connection factory. Missing updates raise KeyError."""

    def _connect(self) -> sqlite3.Connection:
        raise NotImplementedError

    def create_request(self, request: Request) -> None:
        request = Request.model_validate(request.model_dump())
        with closing(self._connect()) as connection, connection:
            self._insert_request(connection, request)

    def record_workflow_trace_root(self, request_id: str, root_run_id: str,
                                   workflow_id: str | None = None) -> None:
        """Keep a stable root trace ID so a resumed customer interaction can link back."""
        timestamp = utc_now().isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("""INSERT OR IGNORE INTO workflow_trace_roots
                (request_id,root_run_id,workflow_id,created_at,updated_at) VALUES (?,?,?,?,?)""",
                (request_id, root_run_id, workflow_id or request_id, timestamp, timestamp))

    def get_workflow_trace_root(self, request_id: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT root_run_id FROM workflow_trace_roots WHERE request_id=?",
                                     (request_id,)).fetchone()
        return row[0] if row else None

    def get_workflow_trace_root_record(self, request_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM workflow_trace_roots WHERE request_id=?",
                                     (request_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _insert_request(connection, request: Request, executor_id: str | None = None) -> None:
        customer = connection.execute("SELECT name FROM customers WHERE customer_id = ?",
                                      (request.customer_id,)).fetchone()
        if customer is None:
            raise ValueError(f"Unknown customer: {request.customer_id}")
        connection.execute(
            "INSERT INTO requests (request_id, customer_id, customer_name, requirements_json, status, stage, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (request.request_id, request.customer_id, customer[0],
             json.dumps(request.requirements.to_dict(), allow_nan=False),
             request.status.value, request.stage.value,
             request.created_at.isoformat(), request.updated_at.isoformat()),
        )
        plan = connection.execute("SELECT * FROM current_plans WHERE customer_id = ?",
                                  (request.customer_id,)).fetchone()
        needs = [item for item in (request.requirements.primary_need, request.requirements.secondary_need,
                                   request.requirements.third_need) if item is not None]
        usage = {item.need: item.level for item in needs}
        connection.execute("""
            UPDATE requests SET business_status='STARTED', executor_id=?, usage_level_json=?, budget=?,
                previous_package_ids_json=?, previous_package_names_json=?,
                previous_addon_ids_json=?, previous_addon_names_json=?, previous_total_price=?, previous_plan_json=?
            WHERE request_id=?
        """, (executor_id, json.dumps(usage), request.requirements.preferred_budget_sar,
              json.dumps([plan['package_id']] if plan and plan['package_id'] else []),
              json.dumps([plan['package_name']] if plan and plan['package_name'] else []),
              plan['addon_ids_json'] if plan else '[]', plan['addon_names_json'] if plan else '[]',
              plan['total_price'] if plan else None, json.dumps(_plan_snapshot(plan), allow_nan=False), request.request_id))

    def create_request_with_checkpoint(self, request: Request, executor_id: str | None = None) -> None:
        """Initialize both records in one transaction; duplicate IDs are errors."""
        request = Request.model_validate(request.model_dump())
        checkpoint = WorkflowCheckpoint(request_id=request.request_id)
        with closing(self._connect()) as connection, connection:
            self._insert_request(connection, request, executor_id)
            self._insert_checkpoint(connection, checkpoint)

    def claim_execution(self, request_id: str, executor_id: str) -> None:
        """Record which process is about to run this request's Search/Design work."""
        with closing(self._connect()) as connection, connection:
            if connection.execute("UPDATE requests SET executor_id=? WHERE request_id=?",
                                  (executor_id, request_id)).rowcount != 1:
                raise KeyError(request_id)

    def get_executor_id(self, request_id: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT executor_id FROM requests WHERE request_id=?",
                                     (request_id,)).fetchone()
        return row[0] if row else None

    def abandoned_build_request_ids(self, current_executor_id: str) -> list[str]:
        """Build-stage requests owned by a dead web boot or by nobody (legacy rows).

        Assumes one web worker: a web boot other than the current one cannot
        still be running. CLI-owned rows are never returned.
        """
        stages = [stage.value for stage in BUILD_STAGES]
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""SELECT request_id FROM requests WHERE status=? AND stage IN ({','.join('?' for _ in stages)})
                    AND (executor_id IS NULL OR (executor_id LIKE 'web:%' AND executor_id<>?))
                    ORDER BY julianday(created_at), request_id""",
                (RequestStatus.PROCESSING.value, *stages, current_executor_id)).fetchall()
        return [row[0] for row in rows]

    def get_request(self, request_id: str) -> Request | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,)).fetchone()
        return self._request_from_row(row)

    @staticmethod
    def _request_from_row(row) -> Request | None:
        if row is None:
            return None
        values = dict(row)
        values["requirements"] = json.loads(values.pop("requirements_json"))
        allowed = Request.model_fields
        return Request.model_validate({key: value for key, value in values.items() if key in allowed})

    def _update_request(self, connection, request_id, status, stage) -> None:
        # Serialize read/update so updated_at advances even on a very fast repeat call.
        row = connection.execute("SELECT updated_at FROM requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            raise KeyError(request_id)
        timestamp = max(utc_now(), datetime.fromisoformat(row[0]) + timedelta(microseconds=1))
        assignments = ["updated_at = ?"]
        values = [timestamp.isoformat()]
        if status is not None:
            assignments.append("status = ?")
            values.append(RequestStatus(status).value)
        if stage is not None:
            assignments.append("stage = ?")
            values.append(WorkflowStage(stage).value)
        connection.execute(f"UPDATE requests SET {', '.join(assignments)} WHERE request_id = ?", (*values, request_id))

    def update_request(self, request_id: str, *, status: RequestStatus | None = None,
                       stage: WorkflowStage | None = None) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._update_request(connection, request_id, status, stage)

    def set_search_phase(self, request_id: str, phase: str | None) -> None:
        """Progress metadata inside SEARCHING only -- never a lifecycle transition."""
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE requests SET search_phase = ? WHERE request_id = ? AND stage = ?",
                               (phase, request_id, WorkflowStage.SEARCHING.value))

    def get_search_phase(self, request_id: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT search_phase FROM requests WHERE request_id = ?",
                                     (request_id,)).fetchone()
        return row[0] if row else None

    def record_recommendations(self, request_id: str, final_solutions) -> None:
        with closing(self._connect()) as connection, connection:
            ids_json = recommendation_ids_json(final_solutions)
            connection.execute("""UPDATE requests SET recommended_solution_ids_json=?,
                business_status=CASE WHEN json_array_length(?) > 0 THEN 'SOLUTIONS_PRESENTED' ELSE business_status END
                WHERE request_id=?""", (ids_json, ids_json, request_id))

    @staticmethod
    def _serialize_outputs(request_id: str, outputs: dict[str, Any]) -> dict[str, str | None]:
        unknown = set(outputs) - set(OUTPUT_FIELDS)
        if unknown:
            raise ValueError(f"Unknown checkpoint outputs: {sorted(unknown)}")
        checkpoint = WorkflowCheckpoint.model_validate({"request_id": request_id, **outputs})
        values = checkpoint.model_dump(mode="json")
        return {name: json.dumps(values[name], allow_nan=False) if values[name] is not None else None
                for name in outputs}

    def create_checkpoint(self, checkpoint: WorkflowCheckpoint) -> None:
        with closing(self._connect()) as connection, connection:
            self._insert_checkpoint(connection, checkpoint)

    def _insert_checkpoint(self, connection, checkpoint: WorkflowCheckpoint) -> None:
        outputs = self._serialize_outputs(checkpoint.request_id, checkpoint.model_dump(exclude={"request_id"}))
        columns = ", ".join(f"{name}_json" for name in outputs)
        placeholders = ", ".join("?" for _ in outputs)
        connection.execute(f"INSERT INTO workflow_checkpoints (request_id, {columns}) VALUES (?, {placeholders})",
                           (checkpoint.request_id, *outputs.values()))

    def get_checkpoint(self, request_id: str) -> WorkflowCheckpoint | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM workflow_checkpoints WHERE request_id = ?", (request_id,)).fetchone()
        return self._checkpoint_from_row(row)

    @staticmethod
    def _checkpoint_from_row(row) -> WorkflowCheckpoint | None:
        if row is None:
            return None
        return WorkflowCheckpoint.model_validate({"request_id": row["request_id"], **{
            name: json.loads(row[f"{name}_json"]) if row[f"{name}_json"] is not None else None
            for name in OUTPUT_FIELDS}})

    def get_workflow_records(self, request_id: str) -> tuple[Request | None, WorkflowCheckpoint | None]:
        """Read both authoritative records from one consistent SQLite snapshot."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            request_row = connection.execute("SELECT * FROM requests WHERE request_id = ?",
                                             (request_id,)).fetchone()
            checkpoint_row = connection.execute("SELECT * FROM workflow_checkpoints WHERE request_id = ?",
                                                (request_id,)).fetchone()
            return self._request_from_row(request_row), self._checkpoint_from_row(checkpoint_row)

    @staticmethod
    def _update_checkpoint(connection, request_id, outputs) -> None:
        if not outputs:
            raise ValueError("At least one checkpoint output is required")
        assignments = ", ".join(f"{name}_json = ?" for name in outputs)
        cursor = connection.execute(f"UPDATE workflow_checkpoints SET {assignments} WHERE request_id = ?",
                                    (*outputs.values(), request_id))
        if cursor.rowcount != 1:
            raise KeyError(request_id)

    def update_checkpoint(self, request_id: str, **outputs: Any) -> None:
        """Omitted outputs stay unchanged; explicit None clears an output."""
        serialized = self._serialize_outputs(request_id, outputs)
        with closing(self._connect()) as connection, connection:
            self._update_checkpoint(connection, request_id, serialized)

    def update_workflow(self, request_id: str, *, status: RequestStatus,
                        stage: WorkflowStage, **outputs: Any) -> None:
        """Atomically update existing checkpoint outputs and request progress."""
        status, stage = RequestStatus(status), WorkflowStage(stage)
        serialized = self._serialize_outputs(request_id, outputs)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._update_checkpoint(connection, request_id, serialized)
            self._update_request(connection, request_id, status, stage)

    def transition_workflow(
        self, request_id: str, *,
        allowed_from: frozenset[tuple[RequestStatus, WorkflowStage]],
        status: RequestStatus, stage: WorkflowStage,
        outputs: dict[str, Any] | None = None,
        requirements: Requirements | None = None,
        request_updates: dict[str, Any] | None = None,
    ) -> None:
        """Apply service-supplied lifecycle rules under the transaction's write lock.

        Uses the same checkpoint/progress writers as update_workflow. Optional
        requirements replacement participates in that same transaction.
        """
        status, stage = RequestStatus(status), WorkflowStage(stage)
        serialized = self._serialize_outputs(request_id, outputs or {})
        request_updates = request_updates or {}
        unknown_fields = set(request_updates) - TRANSITION_REQUEST_FIELDS
        if unknown_fields:
            raise ValueError(f"Unsupported request transition fields: {sorted(unknown_fields)}")
        requirements_json = None
        if requirements is not None:
            if not isinstance(requirements, Requirements):
                raise TypeError("requirements must be Requirements")
            requirements_json = json.dumps(requirements.to_dict(), allow_nan=False)
        previous_status = None
        previous_stage = None
        customer_id = None
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status, stage, customer_id FROM requests WHERE request_id = ?",
                                     (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            if (row[0], row[1]) not in allowed_from:
                raise InvalidLifecycleTransition(
                    f"Cannot transition {request_id} from {row[0]}/{row[1]} to {status.value}/{stage.value}")
            if connection.execute("SELECT 1 FROM workflow_checkpoints WHERE request_id = ?",
                                  (request_id,)).fetchone() is None:
                raise KeyError(f"Missing checkpoint for {request_id}")
            if serialized:
                self._update_checkpoint(connection, request_id, serialized)
            if requirements_json is not None:
                connection.execute("UPDATE requests SET requirements_json = ? WHERE request_id = ?",
                                   (requirements_json, request_id))
            self._update_request(connection, request_id, status, stage)
            if request_updates:
                assignments = ", ".join(f"{field} = ?" for field in request_updates)
                connection.execute(f"UPDATE requests SET {assignments} WHERE request_id = ?",
                                    (*request_updates.values(), request_id))
            previous_status, previous_stage, customer_id = row[0], row[1], row[2]
        record_event(
            "request.lifecycle_transition",
            inputs={"request_id": request_id, "from_status": previous_status,
                    "from_stage": previous_stage},
            outputs={"status": status.value, "stage": stage.value,
                     "updated_fields": sorted(request_updates)},
            metadata={"request_id": request_id, "customer_id": customer_id,
                      "workflow_id": request_id, "entity": "requests",
                      "status": status.value, "stage": stage.value},
            tags=("database", "request", "workflow"),
        )
