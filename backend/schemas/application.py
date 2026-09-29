"""Input schemas for deterministic solution application tools."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApplySolutionInput(BaseModel):
    """Identifiers needed to apply a solution already selected by a customer."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=1, description="Customer who owns the request")
    request_id: str = Field(min_length=1, description="TELORA request containing the saved customer selection")
    selected_solution_id: str = Field(min_length=1, description="Solution ID saved after customer selection")
    package_ids: list[str] = Field(min_length=1, max_length=1, description="Package IDs in that solution")
    addon_ids: list[str] = Field(default_factory=list, max_length=2, description="Add-on IDs in that solution")

    @field_validator("customer_id", "request_id", "selected_solution_id")
    @classmethod
    def identifiers_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Identifier cannot be blank")
        return value

    @field_validator("package_ids", "addon_ids")
    @classmethod
    def component_ids_are_unique_and_nonblank(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Component IDs cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("Component IDs must be unique")
        return values
