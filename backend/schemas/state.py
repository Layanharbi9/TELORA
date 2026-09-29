"""Shared workflow state and trace contracts for TELORA."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from backend.schemas.checkpoint import TemporaryWorkflowOutput
from backend.schemas.request import RequestStatus, WorkflowStage
from backend.schemas.requirements import Requirements
from backend.schemas.search import CandidatePool, SearchPlan


class SharedWorkflowState(BaseModel):
    """Runtime working memory rebuilt from SQLite, never an independent store.

    TemporaryWorkflowOutput fields reuse the checkpoint's safe structured JSON
    contract until final Solution/Validation/Application Pydantic schemas exist.
    Lifecycle and output writes must go through RequestService/persistence.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    request_id: str
    customer_id: str
    status: RequestStatus
    stage: WorkflowStage
    requirements: Requirements
    candidate_pool: CandidatePool | None = None
    candidate_solutions: TemporaryWorkflowOutput | None = None
    solution_validation: TemporaryWorkflowOutput | None = None
    final_solutions: TemporaryWorkflowOutput | None = None
    selected_solution: TemporaryWorkflowOutput | None = None
    applied_solution: TemporaryWorkflowOutput | None = None

    @field_validator("request_id", "customer_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ID must not be blank")
        return value


@dataclass(frozen=True)
class ToolTrace:
    tool_name: str
    input: dict[str, Any]
    output: dict[str, Any]
    status: str
    execution_time_ms: float
    task_index: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "tool_name": self.tool_name,
            "input": self.input,
            "output": self.output,
            "status": self.status,
            "execution_time_ms": self.execution_time_ms,
        }

        if self.task_index is not None:
            result["task_index"] = self.task_index
        return result


@dataclass(frozen=True)
class StageTrace:
    stage: str
    status: str
    execution_time_ms: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "status": self.status,
                "execution_time_ms": self.execution_time_ms, "details": self.details}


@dataclass(frozen=True)
class SearchTrace:
    agent_name: str
    tool_traces: list[ToolTrace]
    search_plan: Optional[SearchPlan] = None
    stages: list[StageTrace] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = {"agent_name": self.agent_name, "tool_traces": [trace.to_dict() for trace in self.tool_traces]}
        if self.search_plan is not None:
            result["search_plan"] = self.search_plan.model_dump(mode="json")
        if self.stages:
            result["stages"] = [stage.to_dict() for stage in self.stages]
        return result


@dataclass(frozen=True)
class TELORAState:
    request_id: str
    user_requirements: Requirements
    candidate_pool: Optional[CandidatePool] = None
    search_trace: Optional[SearchTrace] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "user_requirements": self.user_requirements.to_dict(),
            "candidate_pool": self.candidate_pool.model_dump(mode="json") if self.candidate_pool else None,
            "search_trace": self.search_trace.to_dict() if self.search_trace else None,
        }
