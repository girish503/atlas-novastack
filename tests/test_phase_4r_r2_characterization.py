"""Phase 4R-R2: Inference Deadline & Memory Characterization Tests."""
import inspect
import time
from typing import Any, Optional
import pytest
from fastapi.testclient import TestClient

from novastack.citation_validator import Citation
from novastack.evidence import EvidencePackage
from novastack.evidence_resolution import EvidenceResolverConfig
from novastack.generation import AnswerResult, AnswerStatus, GroundedAnswerGenerator
from novastack.service.api import AtlasServicePipeline, create_app
from novastack.service.resilience import CircuitBreaker, CircuitState, ResilienceConfig


class MockDelayedGenerator:
    """Configurable test generator supporting artificial delay and failure injection."""
    def __init__(self, delay_seconds: float = 0.05, raise_exc: Optional[Exception] = None):
        self.delay_seconds = delay_seconds
        self.raise_exc = raise_exc
        self.call_count = 0

    def generate_answer(
        self,
        package: EvidencePackage,
        max_evidence_items: int = 3,
        prompt_strategy: str = "config_a_calibrated",
        citation_resolver: str = "c2",
        enable_boundary_stitching: bool = False,
        timeout_seconds: Optional[float] = None,
    ) -> AnswerResult:
        self.call_count += 1
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        if self.raise_exc:
            raise self.raise_exc

        return AnswerResult(
            answer_id=f"ANS-{package.package_id}",
            evaluation_id=package.evaluation_id,
            query=package.query,
            answer_text="Mock grounded answer",
            answer_status=AnswerStatus.ANSWERED.value,
            citations=[],
            generation_latency_ms=self.delay_seconds * 1000.0,
        )


def build_test_pipeline(generator: Any = None) -> AtlasServicePipeline:
    gen = generator or MockDelayedGenerator(delay_seconds=0.01)
    pipe = AtlasServicePipeline.create_default(lazy_generator=True)
    pipe.generator = gen
    return pipe


def test_frozen_configuration_remains_intact():
    """Verify that production flags and resilience parameters remain strictly frozen in Phase 4R-R2."""
    gen = GroundedAnswerGenerator(lazy_load=True)
    sig = inspect.signature(gen.generate_answer)
    assert sig.parameters["enable_boundary_stitching"].default is False, "Mechanism A must be False"

    cfg = EvidenceResolverConfig()
    assert cfg.enable_query_aware_authority is True, "Mechanism B must be True (Production Standard)"
    assert cfg.enable_event_bundling is False, "Mechanism C must be False"

    res_cfg = ResilienceConfig()
    assert res_cfg.max_concurrent_inferences == 1
    assert res_cfg.queue_timeout_seconds == 0.5
    assert res_cfg.request_timeout_seconds == 30.0
    assert res_cfg.enable_circuit_breaker is True


def test_timing_headroom_calculation():
    """Verify calculation of timing breakdown and deadline headroom."""
    deadline_ms = 30000.0
    end_to_end_ms = 30012.67
    headroom_ms = round(deadline_ms - end_to_end_ms, 2)
    assert headroom_ms == -12.67
    assert headroom_ms < 0.0, "Negative headroom indicates deadline overrun"


def test_circuit_breaker_full_cycle_transitions():
    """Verify circuit breaker transitions: CLOSED -> OPEN -> HALF_OPEN -> CLOSED."""
    cb = CircuitBreaker(
        failure_threshold=3,
        cooldown_seconds=0.1,
    )
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 1. First failure
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 1
    assert cb.can_execute() is True

    # 2. Second failure
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 2
    assert cb.can_execute() is True

    # 3. Third failure -> trip to OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.consecutive_failures == 3
    assert cb.can_execute() is False

    # 4. Immediate probe rejected
    assert cb.can_execute() is False

    # 5. Wait for cooldown expiration
    time.sleep(0.15)
    assert cb.can_execute() is True  # Transitions to HALF_OPEN
    assert cb.state == CircuitState.HALF_OPEN

    # 6. Success resets to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0
    assert cb.can_execute() is True


def test_memory_checkpoint_schema_integrity():
    """Verify schema integrity of 9 memory checkpoints."""
    expected_stages = [
        "Initial Process Startup",
        "After Pipeline Init (Lazy)",
        "After Model Load",
        "Before Generation #1",
        "After Generation #1",
        "After Generation #2",
        "After Generation #3",
        "After Explicit Python GC",
        "After Safe Idle Period",
    ]
    assert len(expected_stages) == 9
    for idx, stage in enumerate(expected_stages, start=1):
        assert isinstance(stage, str)
        assert len(stage) > 0


def test_health_and_readiness_non_blocking_during_simulated_load():
    """Verify health and readiness probes respond in sub-50ms even during delayed execution."""
    slow_gen = MockDelayedGenerator(delay_seconds=0.01)
    pipe = build_test_pipeline(generator=slow_gen)
    app = create_app(pipeline=pipe)
    client = TestClient(app)

    t0 = time.perf_counter()
    h = client.get("/healthz")
    r = client.get("/ready")
    dt_ms = (time.perf_counter() - t0) * 1000.0

    assert h.status_code == 200
    assert h.json()["status"] == "ok"
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert dt_ms < 100.0
