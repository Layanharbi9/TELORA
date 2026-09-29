"""Latest workflow outputs only; request metadata belongs to Request."""

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator

from backend.schemas.search import CandidatePool


# Temporary structured JSON until the later-stage schemas are implemented.
TemporaryWorkflowOutput = dict[str, JsonValue] | list[JsonValue]


class WorkflowCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    request_id: str
    candidate_pool: CandidatePool | None = None
    candidate_solutions: TemporaryWorkflowOutput | None = None
    solution_validation: TemporaryWorkflowOutput | None = None
    final_solutions: TemporaryWorkflowOutput | None = None
    selected_solution: TemporaryWorkflowOutput | None = None
    applied_solution: TemporaryWorkflowOutput | None = None

    @field_validator("request_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ID must not be blank")
        return value
