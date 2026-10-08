"""Unit and Contract tests for Canary Router and Observability."""

from __future__ import annotations

import os
import pytest

from novastack.canary import (
    BASELINE_VERSION,
    CANARY_CANDIDATE_VERSION,
    CanaryConfig,
    CanaryRouter,
    CanaryTelemetryRecord,
)


def test_canary_default_is_disabled_and_fails_closed():
    cfg = CanaryConfig()
    assert not cfg.enabled
    assert cfg.traffic_percentage == 0.0

    router = CanaryRouter(cfg)
    variant, bucket = router.route_request("TENANT-A", "test query 1")
    assert variant == "baseline"
    assert bucket == -1


def test_canary_deterministic_routing():
    cfg = CanaryConfig(enabled=True, traffic_percentage=50.0, salt="test-salt")
    router = CanaryRouter(cfg)

    # Calling repeatedly with the exact same inputs must yield identical variant & bucket
    v1, b1 = router.route_request("TENANT-NOVASTACK", "query-alpha")
    v2, b2 = router.route_request("TENANT-NOVASTACK", "query-alpha")
    assert v1 == v2
    assert b1 == b2


def test_canary_traffic_percentage_distribution():
    # 5% canary test across 1000 simulated distinct query keys
    cfg = CanaryConfig(enabled=True, traffic_percentage=5.0, salt="dist-test-salt")
    router = CanaryRouter(cfg)

    h5_1_count = 0
    baseline_count = 0

    for i in range(1000):
        v, b = router.route_request("TENANT-NOVASTACK", f"synthetic-query-{i}")
        if v == "h5_1":
            h5_1_count += 1
            assert 0 <= b < 5
        else:
            baseline_count += 1
            assert b >= 5

    # With uniform hashing across 1000 items, 5% is roughly 50 +/- 20
    assert 30 <= h5_1_count <= 70
    assert baseline_count == 1000 - h5_1_count


def test_canary_tenant_allowlist():
    cfg = CanaryConfig(
        enabled=True,
        traffic_percentage=100.0,
        allowed_tenants={"TENANT-ALLOW"},
    )
    router = CanaryRouter(cfg)

    v1, b1 = router.route_request("TENANT-ALLOW", "q1")
    assert v1 == "h5_1"
    assert b1 < 100

    v2, b2 = router.route_request("TENANT-DISALLOWED", "q1")
    assert v2 == "baseline"
    assert b2 == -1


def test_canary_instant_rollback():
    cfg = CanaryConfig(enabled=True, traffic_percentage=100.0)
    router = CanaryRouter(cfg)

    v, b = router.route_request("TENANT-A", "q")
    assert v == "h5_1"

    # Instant rollback by disabling config
    cfg.enabled = False
    v_rolled_back, b_rolled_back = router.route_request("TENANT-A", "q")
    assert v_rolled_back == "baseline"
    assert b_rolled_back == -1


def test_canary_telemetry_sanitization():
    rec = CanaryTelemetryRecord(
        request_id="REQ-TEST",
        tenant_id="TENANT-A",
        variant="h5_1",
        canary_bucket=3,
        candidate_version=CANARY_CANDIDATE_VERSION,
        query="What is the root cause?",
        resolved_entities=["INC-001"],
        candidate_count=5,
        answer_status="answered",
        http_status=200,
    )
    d = rec.to_dict()
    assert "jwt_secret" not in d
    assert "authorization" not in d
    assert "bearer" not in d
    assert "password" not in d
    assert d["variant"] == "h5_1"
    assert d["canary_bucket"] == 3
