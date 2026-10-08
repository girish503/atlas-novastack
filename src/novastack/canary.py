"""Phase 2 / Phase 4 / Phase 5: ATLAS Controlled Canary Routing and Observability.

Provides:
- CanaryConfig: Environment-configurable traffic split, salt, tenant allowlist, and kill-switch.
- CanaryRouter: Cryptographically deterministic, tenant-aware traffic partitioning.
- CanaryTelemetryRecord: Comprehensive, sanitized structured telemetry for canary evaluation.
- CanaryPipelineAdapter: Thin facade managing baseline (H5) and candidate (H5.1) execution.

Security Invariants:
- Fail-closed: Disabling or unconfiguring canary immediately reverts 100% of traffic to Baseline.
- No authorization bypass: Caller authentication occurs strictly before canary routing.
- Zero secret leakage: Telemetry records contain no tokens, credentials, or secrets.
- Deterministic routing: Identical queries/requests for a tenant produce identical variant assignments.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("novastack.canary")

BASELINE_VERSION = "0.4.14-rc1"
CANARY_CANDIDATE_VERSION = "0.4.14-rc1+h5.1"


@dataclass
class CanaryConfig:
    """Configuration for controlled canary traffic routing."""

    enabled: bool = False
    traffic_percentage: float = 0.0  # 0.0 to 100.0 (e.g. 5.0 for 5% canary)
    salt: str = "atlas-canary-h5-1-v1"
    allowed_tenants: Optional[Set[str]] = None

    @classmethod
    def from_env(cls) -> "CanaryConfig":
        """Load canary configuration from environment variables."""
        enabled = os.environ.get("ATLAS_CANARY_ENABLED", "false").lower() in ("true", "1", "yes")
        try:
            pct = float(os.environ.get("ATLAS_CANARY_TRAFFIC_PERCENTAGE", "0.0"))
        except ValueError:
            pct = 0.0
        pct = max(0.0, min(100.0, pct))
        salt = os.environ.get("ATLAS_CANARY_SALT", "atlas-canary-h5-1-v1")
        tenants_str = os.environ.get("ATLAS_CANARY_ALLOWED_TENANTS", "").strip()
        tenants = set(t.strip() for t in tenants_str.split(",") if t.strip()) if tenants_str else None
        return cls(enabled=enabled, traffic_percentage=pct, salt=salt, allowed_tenants=tenants)


@dataclass
class CanaryTelemetryRecord:
    """Structured, sanitized audit telemetry for each routed canary request."""

    request_id: str
    tenant_id: str
    variant: str  # "baseline" | "h5_1"
    canary_bucket: int  # 0-99 (-1 if disabled)
    candidate_version: str
    query: str
    query_type: Optional[str] = None
    resolved_entities: List[str] = field(default_factory=list)
    entity_resolution_reason: Optional[str] = None
    retrieval_configuration: str = "B4+H1+H3"
    candidate_count: int = 0
    top_document_ids: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    citation_ids: List[str] = field(default_factory=list)
    answer_status: str = "unknown"
    abstention_status: Optional[str] = None
    generation_invoked: bool = False
    latency_breakdown: Dict[str, float] = field(default_factory=dict)
    http_status: int = 200
    error_category: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CanaryRouter:
    """Deterministic, tenant-aware traffic router."""

    def __init__(self, config: Optional[CanaryConfig] = None):
        self.config = config or CanaryConfig.from_env()
        self.route_call_count: int = 0

    def route_request(self, tenant_id: str, routing_key: str) -> Tuple[str, int]:
        """Deterministically routes a request to 'baseline' or 'h5_1'.

        Returns:
            Tuple[variant_name, bucket_number (0-99, or -1 if disabled)]
        """
        self.route_call_count += 1

        # Check explicit config first
        is_enabled = self.config.enabled
        pct = self.config.traffic_percentage

        # Live environment override (allows immediate kill-switch / dynamic toggle)
        env_enabled = os.environ.get("ATLAS_CANARY_ENABLED")
        if env_enabled is not None:
            is_enabled = env_enabled.lower() in ("true", "1", "yes")

        env_pct = os.environ.get("ATLAS_CANARY_TRAFFIC_PERCENTAGE")
        if env_pct is not None:
            try:
                pct = float(env_pct)
            except ValueError:
                pct = 0.0

        if not is_enabled or pct <= 0.0:
            return "baseline", -1

        if self.config.allowed_tenants is not None and tenant_id not in self.config.allowed_tenants:
            return "baseline", -1

        # Use SHA-256 over tenant_id, routing_key, and salt for uniform 0-99 distribution
        seed = f"{tenant_id}:{routing_key}:{self.config.salt}".encode("utf-8")
        bucket = int(hashlib.sha256(seed).hexdigest()[:8], 16) % 100

        if bucket < pct:
            return "h5_1", bucket
        return "baseline", bucket


class CanaryPipelineAdapter:
    """Manages dual execution variants (Baseline H5 vs Candidate H5.1) with unified observability."""

    def __init__(
        self,
        base_pipeline: Any,
        catalog: Any,
        router: Optional[CanaryRouter] = None,
        telemetry_sink: Optional[Callable[[CanaryTelemetryRecord], None]] = None,
    ):
        self.pipeline = base_pipeline
        self.catalog = catalog
        self.router = router or CanaryRouter()
        self.telemetry_sink = telemetry_sink
        self.telemetry_records: List[CanaryTelemetryRecord] = []

        # Lazily instantiate resolvers on first use to maintain cold-start efficiency
        self._h5_components: Optional[Tuple[Any, Any, Any]] = None
        self._h5_1_components: Optional[Tuple[Any, Any, Any]] = None

    def _get_h5_components(self) -> Tuple[Any, Any, Any]:
        """Return (H5EntityResolver, H5QueryUnderstandingOverlay, H5StructuredRetrieverAdapter)."""
        if self._h5_components is None:
            from scripts.ret_eval_08_h5_experiment import (
                H5EntityResolver,
                H5QueryUnderstandingOverlay,
                H5StructuredRetrieverAdapter,
            )
            resolver = H5EntityResolver(self.catalog)
            qu_overlay = H5QueryUnderstandingOverlay(
                base_extractor=self.pipeline.qu_extractor,
                resolver=resolver,
                catalog=self.catalog,
            )
            struct_overlay = H5StructuredRetrieverAdapter(
                base_retriever=self.pipeline.structured_retriever,
                resolver=resolver,
                catalog=self.catalog,
            )
            self._h5_components = (resolver, qu_overlay, struct_overlay)
        return self._h5_components

    def _get_h5_1_components(self) -> Tuple[Any, Any, Any]:
        """Return (H5_1EntityResolver, H5_1QueryUnderstandingOverlay, H5_1StructuredRetrieverAdapter)."""
        if self._h5_1_components is None:
            from scripts.ret_eval_08_h5_1_experiment import (
                H5_1EntityResolver,
                H5_1QueryUnderstandingOverlay,
                H5_1StructuredRetrieverAdapter,
            )
            resolver = H5_1EntityResolver(self.catalog)
            qu_overlay = H5_1QueryUnderstandingOverlay(
                base_extractor=self.pipeline.qu_extractor,
                resolver=resolver,
                catalog=self.catalog,
            )
            struct_overlay = H5_1StructuredRetrieverAdapter(
                base_retriever=self.pipeline.structured_retriever,
                resolver=resolver,
                catalog=self.catalog,
            )
            self._h5_1_components = (resolver, qu_overlay, struct_overlay)
        return self._h5_1_components

    def execute_query(
        self,
        request: Any,
        timeout_seconds: Optional[float] = None,
        request_id: Optional[str] = None,
    ) -> Any:
        """Route and execute query under the assigned variant with comprehensive telemetry."""
        tenant_id = request.user_context.tenant_id
        routing_key = getattr(request, "evaluation_id", None) or request.query
        variant, bucket = self.router.route_request(tenant_id, routing_key)

        version = CANARY_CANDIDATE_VERSION if variant == "h5_1" else BASELINE_VERSION
        config_name = "B4+H1(H5.1)+H3" if variant == "h5_1" else "B4+H1(H5)+H3"

        # Swap in the appropriate QU extractor and Structured Retriever
        original_qu = self.pipeline.qu_extractor
        original_struct = self.pipeline.structured_retriever

        t_res_start = time.perf_counter()
        if variant == "h5_1":
            resolver, qu_overlay, struct_overlay = self._get_h5_1_components()
        else:
            resolver, qu_overlay, struct_overlay = self._get_h5_components()

        # Measure resolver latency on query
        resolved_entities = []
        try:
            h_res = resolver.resolve(request.query)
            resolved_entities = [e.entity_id for e in h_res.entities]
        except Exception as e:
            logger.warning(f"Entity resolution probe failed: {e}")
        t_res_end = time.perf_counter()
        resolver_ms = (t_res_end - t_res_start) * 1000

        t0 = time.perf_counter()
        try:
            self.pipeline.qu_extractor = qu_overlay
            self.pipeline.structured_retriever = struct_overlay

            # Execute pipeline
            response = self.pipeline.execute_query(
                request=request,
                timeout_seconds=timeout_seconds,
                request_id=request_id,
            )
            t_total = (time.perf_counter() - t0) * 1000

            # Collect telemetry
            top_docs = []
            if hasattr(response, "citations"):
                top_docs = [c.document_id for c in response.citations if c.document_id]

            rec = CanaryTelemetryRecord(
                request_id=response.request_id or request_id or "UNKNOWN",
                tenant_id=tenant_id,
                variant=variant,
                canary_bucket=bucket,
                candidate_version=version,
                query=request.query,
                query_type=getattr(request, "query_category", None),
                resolved_entities=resolved_entities,
                entity_resolution_reason=f"{variant}_resolver",
                retrieval_configuration=config_name,
                candidate_count=len(top_docs),
                top_document_ids=top_docs,
                evidence_ids=[c.raw_tag for c in getattr(response, "citations", []) if c.raw_tag],
                citation_ids=[c.citation_id for c in getattr(response, "citations", []) if hasattr(c, "citation_id")],
                answer_status=response.answer_status,
                abstention_status=response.abstention_reason,
                generation_invoked=getattr(response, "was_generation_invoked", False),
                latency_breakdown={
                    "resolver_ms": resolver_ms,
                    "total_ms": t_total,
                    "generation_ms": getattr(response, "generation_latency_ms", 0.0) or 0.0,
                },
                http_status=200,
            )
            self._record_telemetry(rec)
            return response

        except Exception as exc:
            t_total = (time.perf_counter() - t0) * 1000
            rec = CanaryTelemetryRecord(
                request_id=request_id or "ERROR",
                tenant_id=tenant_id,
                variant=variant,
                canary_bucket=bucket,
                candidate_version=version,
                query=request.query,
                retrieval_configuration=config_name,
                answer_status="error",
                error_category=type(exc).__name__,
                latency_breakdown={"resolver_ms": resolver_ms, "total_ms": t_total},
                http_status=500,
            )
            self._record_telemetry(rec)
            raise
        finally:
            self.pipeline.qu_extractor = original_qu
            self.pipeline.structured_retriever = original_struct

    def _record_telemetry(self, record: CanaryTelemetryRecord):
        self.telemetry_records.append(record)
        if self.telemetry_sink is not None:
            try:
                self.telemetry_sink(record)
            except Exception as e:
                logger.warning(f"Telemetry sink failed: {e}")
