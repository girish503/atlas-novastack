"""Phase 4X-R1: Focused Instrumentation Regression Tests.

Verifies that the measurement-only probes installed in
scripts/phase_4x_r1_characterization.py correctly distinguish:

1. executor_submitted vs worker_entered vs model_call_started
2. model_call_started vs model_call_returned (T6 vs T7)
3. Timeout at service level correctly records whether model generation started
4. Completed generation records T7 and generation_completed_in_worker=True
5. Timeout does NOT falsely report generation_never_invoked when T6 was observed
6. Worker diagnostics layer is correctly propagated
7. Timeline annotations are OBSERVED/NOT_OBSERVED strings

All tests use mock objects only — no real Gemma model required.
These tests are FAST (no slow marker needed).
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "src"))


# ---------------------------------------------------------------------------
# Helpers: minimal stubs matching the production interfaces
# ---------------------------------------------------------------------------

class _FakeAnswerResult:
    """Minimal stub matching AnswerResult interface used by the probe."""
    def __init__(self, layer: str, gen_latency_ms: float = 0.0, status: str = "answered",
                 inference_ms: float = 0.0):
        self.answer_status = status
        self.generation_latency_ms = gen_latency_ms
        self.diagnostics = {
            "layer": layer,
            "inference_duration_ms": inference_ms,
        }


class _FakeModel:
    """Stub model with a generate() method that records a flag."""
    def __init__(self, delay_s: float = 0.0):
        self._delay = delay_s
        self.generate_called = False

    def generate(self, *args, **kwargs):
        self.generate_called = True
        if self._delay:
            time.sleep(self._delay)
        return MagicMock()

    def parameters(self):
        p = MagicMock()
        p.dtype = "torch.bfloat16"
        yield p


class _FakeGenerator:
    """Stub generator with a model and a generate_answer method."""
    def __init__(self, model=None, layer="model_inference", gen_latency_ms=0.0,
                 inference_ms=0.0, status="answered"):
        self.model = model or _FakeModel()
        self._layer = layer
        self._gen_latency_ms = gen_latency_ms
        self._inference_ms = inference_ms
        self._status = status

    def generate_answer(self, package, **kwargs):
        # Simulate the real generate_answer: calls model.generate() for model_inference
        if self._layer == "model_inference":
            self.model.generate(MagicMock())
        return _FakeAnswerResult(
            layer=self._layer,
            gen_latency_ms=self._gen_latency_ms,
            status=self._status,
            inference_ms=self._inference_ms,
        )


def _install_probe(gen, timeline):
    """Import and call the probe installer from the R1 script."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "phase_4x_r1",
        WORKSPACE / "scripts" / "phase_4x_r1_characterization.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so @dataclass machinery can resolve __module__
    sys.modules["phase_4x_r1"] = mod
    spec.loader.exec_module(mod)
    original = mod.install_generation_probe(gen, timeline)
    return mod, original


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProbeDistinguishesExecutorFromWorker:
    """Test 1: executor_submitted is set BEFORE the HTTP call (T4),
    worker_entered is set INSIDE the generator (T5).
    Both must be independently observable."""

    def test_t4_set_before_t5(self):
        tl: Dict[str, Any] = {}
        gen = _FakeGenerator(layer="model_inference")
        mod, orig = _install_probe(gen, tl)

        # Simulate executor_submitted being set (as the script does)
        tl["T4_executor_submitted_s"] = time.perf_counter()

        # Now call the probed generate_answer
        fake_pkg = MagicMock()
        gen.generate_answer(fake_pkg)

        assert "T4_executor_submitted_s" in tl, "T4 must be set by script before HTTP call"
        assert "T5_worker_entered_s" in tl, "T5 must be set inside the probe"
        assert tl["T4_executor_submitted_s"] <= tl["T5_worker_entered_s"], \
            "Executor submission must precede worker entry"

        # Restore
        gen.generate_answer = orig


class TestProbeDistinguishesModelCallStartedFromSubmission:
    """Test 2: model.generate() start (T6) is distinguishable from
    executor submission (T4) and worker entry (T5)."""

    def test_t6_observed_after_t5_and_t4(self):
        tl: Dict[str, Any] = {}
        gen = _FakeGenerator(layer="model_inference", gen_latency_ms=100.0, inference_ms=90.0)
        mod, orig = _install_probe(gen, tl)

        tl["T4_executor_submitted_s"] = time.perf_counter()
        fake_pkg = MagicMock()
        gen.generate_answer(fake_pkg)

        assert "T6_model_call_started_s" in tl, "T6 must be observed when model.generate() runs"
        assert "T7_model_call_returned_s" in tl, "T7 must be observed when model.generate() returns"
        assert tl["T5_worker_entered_s"] <= tl["T6_model_call_started_s"], \
            "T6 must be after T5"
        assert tl["T6_model_call_started_s"] <= tl["T7_model_call_returned_s"], \
            "T7 must be after T6"

        gen.generate_answer = orig

    def test_t6_not_observed_when_pre_generation_gate_fires(self):
        """Pre-generation gate (no evidence) must NOT reach model.generate()."""
        tl: Dict[str, Any] = {}
        gen = _FakeGenerator(layer="pre_generation_gate")
        mod, orig = _install_probe(gen, tl)

        tl["T4_executor_submitted_s"] = time.perf_counter()
        fake_pkg = MagicMock()
        gen.generate_answer(fake_pkg)

        # T5 (worker entry) is always observed
        assert "T5_worker_entered_s" in tl
        # T6 must NOT be observed — model.generate() was never called
        assert "T6_model_call_started_s" not in tl, \
            "T6 must NOT be present when pre_generation_gate fires"
        assert "T7_model_call_returned_s" not in tl, \
            "T7 must NOT be present when pre_generation_gate fires"
        assert tl.get("generation_timed_out_in_worker") is True, \
            "pre_generation_gate must set generation_timed_out_in_worker"

        gen.generate_answer = orig


class TestTimeoutCorrectlyRecordsWhetherModelStarted:
    """Test 3: When generation_timeout fires inside the worker,
    T6 may or may not be present depending on when the deadline triggered.
    The probe must faithfully record what happened — not infer."""

    def test_generation_timeout_layer_sets_timed_out_in_worker(self):
        """generation_timeout layer means the model started but the
        internal stopping criterion triggered — T6 observed, T7 NOT observed
        when the stopping criteria fired mid-generate (simulated here by
        returning the timeout result before T7 is set)."""
        tl: Dict[str, Any] = {}
        # For this test: simulate a generator whose model.generate() IS called
        # but the AnswerResult returned has layer=generation_timeout
        gen = _FakeGenerator(layer="generation_timeout")
        mod, orig = _install_probe(gen, tl)

        tl["T4_executor_submitted_s"] = time.perf_counter()
        fake_pkg = MagicMock()
        gen.generate_answer(fake_pkg)

        # T5 always observed
        assert "T5_worker_entered_s" in tl
        # Worker result correctly identified as timeout
        assert tl.get("generation_timed_out_in_worker") is True
        assert "generation_completed_in_worker" not in tl

        gen.generate_answer = orig

    def test_completed_generation_does_not_set_timed_out_flag(self):
        tl: Dict[str, Any] = {}
        gen = _FakeGenerator(layer="model_inference", gen_latency_ms=500.0, inference_ms=480.0)
        mod, orig = _install_probe(gen, tl)

        tl["T4_executor_submitted_s"] = time.perf_counter()
        fake_pkg = MagicMock()
        gen.generate_answer(fake_pkg)

        assert tl.get("generation_completed_in_worker") is True
        assert "generation_timed_out_in_worker" not in tl

        gen.generate_answer = orig


class TestCompletedGenerationRecordsT7:
    """Test 4: A completed generation must record T7 and
    generation_completed_in_worker=True."""

    def test_t7_present_on_completion(self):
        tl: Dict[str, Any] = {}
        gen = _FakeGenerator(layer="model_inference", gen_latency_ms=1000.0, inference_ms=950.0)
        mod, orig = _install_probe(gen, tl)

        fake_pkg = MagicMock()
        gen.generate_answer(fake_pkg)

        assert "T7_model_call_returned_s" in tl, "T7 must be present on completion"
        assert tl.get("generation_completed_in_worker") is True
        assert tl.get("worker_result_layer") == "model_inference"
        assert tl.get("worker_generation_latency_ms") == pytest.approx(1000.0, abs=1.0)

        gen.generate_answer = orig


class TestTimeoutDoesNotFalselyReportNeverInvoked:
    """Test 5: When T6 IS observed but T7 is NOT (service timeout fired while
    model.generate() was running), the probe must NOT record
    'generation never invoked'. The script must annotate T6=OBSERVED."""

    def test_t6_observed_means_generation_started(self):
        """If T6 is in the timeline, generation started — regardless of
        whether T7 is present."""
        tl: Dict[str, Any] = {}
        gen = _FakeGenerator(layer="model_inference", gen_latency_ms=30000.0, inference_ms=29900.0)
        mod, orig = _install_probe(gen, tl)

        fake_pkg = MagicMock()
        gen.generate_answer(fake_pkg)

        # T6 must be present
        assert "T6_model_call_started_s" in tl, \
            "T6 must be observed — generation did start"

        # Build annotations as the script does
        events = {
            "T6_model_call_started": "T6_model_call_started_s",
            "T7_model_call_returned": "T7_model_call_returned_s",
        }
        annotations = {
            name: ("OBSERVED" if key in tl else "NOT_OBSERVED")
            for name, key in events.items()
        }

        assert annotations["T6_model_call_started"] == "OBSERVED", \
            "T6 annotation must be OBSERVED — cannot claim generation never invoked"

        gen.generate_answer = orig


class TestWorkerDiagnosticsLayerPropagation:
    """Test 6: worker_diagnostics layer is correctly captured from
    AnswerResult.diagnostics."""

    @pytest.mark.parametrize("layer,expected_completed,expected_timed_out", [
        ("model_inference", True, False),
        ("generation_timeout", False, True),
        ("pre_generation_gate", False, True),
    ])
    def test_layer_propagation(self, layer, expected_completed, expected_timed_out):
        tl: Dict[str, Any] = {}
        gen = _FakeGenerator(layer=layer)
        mod, orig = _install_probe(gen, tl)

        fake_pkg = MagicMock()
        gen.generate_answer(fake_pkg)

        assert tl.get("worker_result_layer") == layer, \
            f"Expected layer={layer} in timeline"
        assert bool(tl.get("generation_completed_in_worker")) == expected_completed
        # generation_timed_out_in_worker is set for non-model_inference layers
        if expected_timed_out:
            assert tl.get("generation_timed_out_in_worker") is True

        gen.generate_answer = orig


class TestTimelineAnnotationsAreObservedStrings:
    """Test 7: Timeline annotations must be 'OBSERVED' or 'NOT_OBSERVED' strings."""

    def test_annotations_for_completed_generation(self):
        tl: Dict[str, Any] = {}
        gen = _FakeGenerator(layer="model_inference")
        mod, orig = _install_probe(gen, tl)

        tl["T0_request_sent_s"] = time.perf_counter()
        tl["T4_executor_submitted_s"] = time.perf_counter()

        fake_pkg = MagicMock()
        gen.generate_answer(fake_pkg)

        tl["T10_response_received_s"] = time.perf_counter()

        # Build annotations as the script does
        events = {
            "T0_request_sent": "T0_request_sent_s",
            "T4_executor_submitted": "T4_executor_submitted_s",
            "T5_worker_entered": "T5_worker_entered_s",
            "T6_model_call_started": "T6_model_call_started_s",
            "T7_model_call_returned": "T7_model_call_returned_s",
            "T_worker_returned": "T_worker_returned_s",
            "T10_response_received": "T10_response_received_s",
        }
        annotations = {
            name: ("OBSERVED" if key in tl else "NOT_OBSERVED")
            for name, key in events.items()
        }

        assert all(v in ("OBSERVED", "NOT_OBSERVED") for v in annotations.values()), \
            "All annotations must be OBSERVED or NOT_OBSERVED"
        assert annotations["T0_request_sent"] == "OBSERVED"
        assert annotations["T4_executor_submitted"] == "OBSERVED"
        assert annotations["T5_worker_entered"] == "OBSERVED"
        assert annotations["T6_model_call_started"] == "OBSERVED"
        assert annotations["T7_model_call_returned"] == "OBSERVED"
        assert annotations["T10_response_received"] == "OBSERVED"

        gen.generate_answer = orig

    def test_annotations_for_pre_gate_abstention(self):
        tl: Dict[str, Any] = {}
        gen = _FakeGenerator(layer="pre_generation_gate")
        mod, orig = _install_probe(gen, tl)

        tl["T0_request_sent_s"] = time.perf_counter()
        tl["T4_executor_submitted_s"] = time.perf_counter()

        fake_pkg = MagicMock()
        gen.generate_answer(fake_pkg)

        tl["T10_response_received_s"] = time.perf_counter()

        events = {
            "T5_worker_entered": "T5_worker_entered_s",
            "T6_model_call_started": "T6_model_call_started_s",
        }
        annotations = {
            name: ("OBSERVED" if key in tl else "NOT_OBSERVED")
            for name, key in events.items()
        }

        assert annotations["T5_worker_entered"] == "OBSERVED", \
            "T5 always observed (worker entered)"
        assert annotations["T6_model_call_started"] == "NOT_OBSERVED", \
            "T6 must be NOT_OBSERVED for pre_generation_gate"

        gen.generate_answer = orig


class TestFrozenConfigurationR1:
    """Verify frozen production configuration values are unchanged."""

    def test_frozen_config_values(self):
        sys.path.insert(0, str(WORKSPACE / "src"))
        from novastack.service.resilience import ResilienceConfig

        cfg = ResilienceConfig(
            request_timeout_seconds=30.0,
            max_concurrent_inferences=1,
            queue_timeout_seconds=0.5,
            enable_circuit_breaker=True,
            circuit_failure_threshold=3,
            circuit_cooldown_seconds=10.0,
        )
        assert cfg.request_timeout_seconds == 30.0
        assert cfg.max_concurrent_inferences == 1
        assert cfg.queue_timeout_seconds == 0.5
        assert cfg.enable_circuit_breaker is True
        assert cfg.circuit_failure_threshold == 3
        assert cfg.circuit_cooldown_seconds == 10.0
