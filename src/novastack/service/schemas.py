"""Phase 4M / 4T: Pydantic Schemas for ATLAS API Service.

Defines schemas for caller context, query requests, answers, health, and readiness.
Enforces fail-closed validation:
- tenant_id is strictly required and non-empty.
- query is strictly required and non-empty.
"""
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


class CallerContext(BaseModel):
    """Caller security and identity context.

    tenant_id is strictly required. No silent fallback to default tenants.
    For HTTP requests, Phase 4T checks any supplied values against the signed
    bearer token and replaces this object with the verified token claims before
    authorization or retrieval.  It is never an authority source by itself.
    """
    tenant_id: str = Field(..., description="Tenant ID (mandatory, non-empty)")
    user_id: Optional[str] = Field(None, description="Caller user ID")
    user_role: Optional[str] = Field(None, description="Caller primary role")
    roles: Optional[list[str]] = Field(default_factory=list, description="Caller roles list")
    user_department: Optional[str] = Field(None, description="Caller primary department")
    departments: Optional[list[str]] = Field(default_factory=list, description="Caller departments list")

    @field_validator("tenant_id")
    @classmethod
    def validate_tenant_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("tenant_id is required and cannot be empty or whitespace only")
        return v.strip()

    @property
    def effective_role(self) -> Optional[str]:
        """Resolve primary role from user_role or roles list."""
        if self.user_role and self.user_role.strip():
            return self.user_role.strip()
        if self.roles and len(self.roles) > 0 and self.roles[0].strip():
            return self.roles[0].strip()
        return None

    @property
    def effective_department(self) -> Optional[str]:
        """Resolve primary department from user_department or departments list."""
        if self.user_department and self.user_department.strip():
            return self.user_department.strip()
        if self.departments and len(self.departments) > 0 and self.departments[0].strip():
            return self.departments[0].strip()
        return None


class QueryRequest(BaseModel):
    """Query execution request payload."""
    query: str = Field(..., description="Query string (mandatory, non-empty)")
    user_context: CallerContext = Field(
        ...,
        description="Caller context cross-checked against the verified bearer token",
    )
    evaluation_id: Optional[str] = Field(None, description="Optional evaluation or correlation ID")
    request_id: Optional[str] = Field(None, description="Optional caller correlation or request ID")

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query is required and cannot be empty or whitespace only")
        return v.strip()


class CitationItem(BaseModel):
    """Structured citation item returned in query response."""
    citation_id: str
    document_id: Optional[str] = None
    chunk_id: Optional[str] = None
    raw_tag: Optional[str] = None
    valid: bool = True


class QueryResponse(BaseModel):
    """Structured query answer response."""
    answer_id: str
    query: str
    answer_text: str
    answer_status: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    abstention_reason: Optional[str] = None
    latency_ms: float
    request_id: Optional[str] = None
    was_generation_invoked: bool = False
    generation_latency_ms: float = 0.0
    index_generation_id: Optional[str] = Field(
        None,
        description="Validated index generation used consistently for this request",
    )


class HealthResponse(BaseModel):
    """Process liveness status response."""
    status: str = "ok"


class ReadyResponse(BaseModel):
    """Component readiness status response."""
    status: str = "ready"
    components: dict[str, bool]
    active_generation_id: Optional[str] = None


class ErrorResponse(BaseModel):
    """Standard error response payload without stack trace leakage."""
    detail: str
    error_type: Optional[str] = None
    answer_status: str = "error"
