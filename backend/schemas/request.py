"""Persistent request metadata, separate from search result status."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from backend.schemas.requirements import Requirements


class RequestStatus(str, Enum):
    PROCESSING = "PROCESSING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    NEEDS_ADJUSTMENT = "NEEDS_ADJUSTMENT"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class WorkflowStage(str, Enum):
    REQUIREMENTS = "REQUIREMENTS"
    SEARCHING = "SEARCHING"
    DESIGNING = "DESIGNING"
    VALIDATING = "VALIDATING"
    PREPARING_SOLUTIONS = "PREPARING_SOLUTIONS"
    SELECTION = "SELECTION"
    APPLYING = "APPLYING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    customer_id: str
    requirements: Requirements
    status: RequestStatus = RequestStatus.PROCESSING
    stage: WorkflowStage = WorkflowStage.REQUIREMENTS
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)

    @field_validator("request_id", "customer_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ID must not be blank")
        return value
