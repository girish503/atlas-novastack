"""Phase 4O: Service Resilience, Timeouts, and Controlled Failure Handling.

Provides:
- ResilienceConfig: Configurable production request deadline and concurrency limits.
- Custom exception hierarchy mapping to standard HTTP status codes.
- Error sanitization redacting paths, tracebacks, credentials, and internals.
- CircuitBreaker: Minimal zero-dependency state machine protecting local model inference.
- InferenceConcurrencyLimiter: Bounded semaphore preventing CPU/memory exhaustion.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import logging
import os
import re
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("novastack.service.resilience")

# Regex patterns for sanitizing sensitive data and internal paths
_PATH_PATTERN = re.compile(r"([A-Za-z]:\\[^\s\n\"']+|/[A-Za-z0-9_.\-]+/[^\s\n\"']+)")
_SECRET_PATTERN = re.compile(r"(password|secret|token|key|bearer|authorization)\s*[:=]\s*[^\s\n\"']+", re.IGNORECASE)
_STACK_PATTERN = re.compile(r"(traceback \(most recent call last\):|file \"[^\"]+\", line \d+|in <module>)", re.IGNORECASE)


def sanitize_error_detail(detail: str) -> str:
    """Sanitize error messages to prevent leakage of paths, secrets, or stack traces."""
    if not detail or not str(detail).strip():
        return "Internal server processing failure"

    text = str(detail)
    if _STACK_PATTERN.search(text):
        return "Internal server processing failure"

    sanitized = _SECRET_PATTERN.sub("[REDACTED_CREDENTIAL]", text)
    sanitized = _PATH_PATTERN.sub("[REDACTED_PATH]", sanitized)
    return sanitized.strip()


class AtlasServiceError(Exception):
    """Base exception for service-level controlled failures."""

    def __init__(self, detail: str, error_type: str = "ServiceError", status_code: int = 500):
        sanitized = sanitize_error_detail(detail)
        super().__init__(sanitized)
        self.detail = sanitized
        self.error_type = error_type
        self.status_code = status_code


class AtlasTimeoutError(AtlasServiceError):
    """Raised when inference or request processing exceeds configured deadline."""

    def __init__(self, detail: str = "Request processing exceeded configured deadline"):
        super().__init__(detail, error_type="TimeoutError", status_code=504)


class ModelUnavailableError(AtlasServiceError):
    """Raised when local model or critical inference component is unavailable or uninitialized."""

    def __init__(self, detail: str = "Model service unavailable"):
        super().__init__(detail, error_type="ModelUnavailableError", status_code=503)


class CapacityExhaustedError(AtlasServiceError):
    """Raised when concurrent inference capacity is saturated and queue deadline expires."""

    def __init__(self, detail: str = "Inference capacity exhausted; please retry later"):
        super().__init__(detail, error_type="CapacityExhaustedError", status_code=429)


class InternalServerError(AtlasServiceError):
    """Raised for unexpected internal server errors, strictly sanitized."""

    def __init__(self, detail: str = "Internal server processing failure"):
        super().__init__(detail, error_type="InternalServerError", status_code=500)


@dataclass
class ResilienceConfig:
    """Production resilience configuration parameters."""

    request_timeout_seconds: float = 30.0
    max_concurrent_inferences: int = 1
    queue_timeout_seconds: float = 0.5
    enable_circuit_breaker: bool = True
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "ResilienceConfig":
        """Instantiate config reading overrides from environment variables."""
        return cls(
            request_timeout_seconds=float(os.environ.get("ATLAS_REQUEST_TIMEOUT_SECONDS", "30.0")),
            max_concurrent_inferences=int(os.environ.get("ATLAS_MAX_CONCURRENT_QUERIES", "1")),
            queue_timeout_seconds=float(os.environ.get("ATLAS_QUEUE_TIMEOUT_SECONDS", "0.5")),
            enable_circuit_breaker=os.environ.get("ATLAS_ENABLE_CIRCUIT_BREAKER", "true").lower() in ("true", "1", "yes"),
            circuit_failure_threshold=int(os.environ.get("ATLAS_CIRCUIT_FAILURE_THRESHOLD", "3")),
            circuit_cooldown_seconds=float(os.environ.get("ATLAS_CIRCUIT_COOLDOWN_SECONDS", "10.0")),
        )


class CircuitState(str, Enum):
    """States of the minimal inference circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Minimal zero-dependency state machine protecting CPU model inference.

    Transitions:
    - CLOSED -> (repeated failure_threshold consecutive failures) -> OPEN
    - OPEN -> (cooldown_seconds elapsed) -> HALF_OPEN
    - HALF_OPEN -> (successful probe) -> CLOSED
    - HALF_OPEN -> (failed probe) -> OPEN
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 10.0,
        enabled: bool = True,
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.1, cooldown_seconds)
        self.enabled = enabled
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time = 0.0
        self._lock = threading.Lock()

    def can_execute(self) -> bool:
        """Check if execution is allowed through the circuit breaker."""
        if not self.enabled:
            return True
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            now = time.perf_counter()
            if self.state == CircuitState.OPEN:
                if now - self.last_failure_time >= self.cooldown_seconds:
                    logger.info("Circuit breaker transitioning from OPEN to HALF_OPEN (probing)")
                    self.state = CircuitState.HALF_OPEN
                    return True
                return False
            if self.state == CircuitState.HALF_OPEN:
                # Allow single probe request
                return True
            return True

    def record_success(self) -> None:
        """Record a successful execution, resetting failure counters and closing the circuit."""
        if not self.enabled:
            return
        with self._lock:
            if self.state != CircuitState.CLOSED:
                logger.info("Circuit breaker probe succeeded; resetting state to CLOSED")
            self.state = CircuitState.CLOSED
            self.consecutive_failures = 0

    def record_failure(self) -> None:
        """Record a catastrophic component or inference failure."""
        if not self.enabled:
            return
        with self._lock:
            self.consecutive_failures += 1
            self.last_failure_time = time.perf_counter()
            if self.consecutive_failures >= self.failure_threshold:
                if self.state != CircuitState.OPEN:
                    logger.warning(
                        "Circuit breaker triggered after %d consecutive failures; entering OPEN state for %.1fs",
                        self.consecutive_failures,
                        self.cooldown_seconds,
                    )
                self.state = CircuitState.OPEN


class InferenceConcurrencyLimiter:
    """Asyncio semaphore limiting concurrent heavy inference executions."""

    def __init__(self, max_concurrent: int = 1, queue_timeout: float = 0.5):
        self.max_concurrent = max(0, max_concurrent)
        self.queue_timeout = max(0.0, queue_timeout)
        self._semaphore: Optional[asyncio.Semaphore] = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, self.max_concurrent))
        return self._semaphore

    async def acquire(self) -> bool:
        """Attempt to acquire an inference slot within queue_timeout seconds."""
        if self.max_concurrent <= 0:
            return False
        sem = self._get_semaphore()
        if self.queue_timeout <= 0:
            return sem.locked() is False and await asyncio.shield(sem.acquire())
        try:
            await asyncio.wait_for(sem.acquire(), timeout=self.queue_timeout)
            return True
        except (asyncio.TimeoutError, TimeoutError):
            return False

    def release(self) -> None:
        """Release an acquired inference slot."""
        if self._semaphore is not None:
            try:
                self._semaphore.release()
            except ValueError:
                pass
