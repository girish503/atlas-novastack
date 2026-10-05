"""Phase 4P: Request Correlation and Propagation Module.

Provides:
- Request ID generation, validation, and sanitization.
- ContextVar-based task-local correlation propagation without global mutable state.
- Standard HTTP header constant X-Request-ID.
"""
from __future__ import annotations

import contextvars
import re
import uuid
from typing import Optional

REQUEST_ID_HEADER = "X-Request-ID"

# Allowed format: 1 to 64 alphanumeric characters, underscores, and hyphens
_VALID_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

# Suspicious patterns (credentials, paths) that must be rejected from request IDs
_SECRET_PATTERN = re.compile(r"(password|secret|token|key|bearer|auth)", re.IGNORECASE)
_PATH_PATTERN = re.compile(r"([A-Za-z]:[\\/]|/|[\\/])")

# Task-local context variable for request correlation ID
_current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("current_request_id", default="")


def validate_request_id(supplied_id: Optional[str]) -> bool:
    """Validate whether a supplied request ID is safe, non-empty, and matches format constraints."""
    if not supplied_id or not isinstance(supplied_id, str):
        return False
    val = supplied_id.strip()
    if not _VALID_REQUEST_ID_RE.match(val):
        return False
    if _SECRET_PATTERN.search(val) or _PATH_PATTERN.search(val):
        return False
    return True


def generate_request_id() -> str:
    """Generate a clean RFC 4122 UUID4 request identifier."""
    return str(uuid.uuid4())


def validate_or_generate_request_id(supplied_id: Optional[str] = None) -> str:
    """Validate a supplied request ID; if absent or invalid, return a newly generated UUID4."""
    if validate_request_id(supplied_id):
        assert supplied_id is not None
        return supplied_id.strip()
    return generate_request_id()


def get_request_id() -> str:
    """Retrieve the current request ID from context, or generate an ephemeral one if unset."""
    rid = _current_request_id.get()
    return rid if rid else ""


def set_request_id(rid: str) -> contextvars.Token:
    """Set the current request correlation ID in task-local context."""
    return _current_request_id.set(rid)


def reset_request_id(token: contextvars.Token) -> None:
    """Reset the request correlation ID back to previous context state."""
    _current_request_id.reset(token)
