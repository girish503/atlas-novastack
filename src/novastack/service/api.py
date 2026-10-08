"""Phase 4M: ATLAS FastAPI Service.

Provides:
- GET /healthz: Process liveness only (200 OK + minimal status JSON).
- GET /ready: Component readiness verification (BM25, Dense, Reranker, Generator).
- POST /query: Strict validated query execution endpoint under fail-closed caller security.
"""
import asyncio
import concurrent.futures
import contextvars
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from novastack.bm25 import BM25Config, BM25Index
from novastack.dense import DenseIndex
from novastack.depth_fusion_ablation import fuse_rrf_sum
from novastack.entity_catalog import EntityCatalog
from novastack.evidence_resolution import (
    EvidenceResolver,
    EvidenceResolverConfig,
)
from novastack.index_manager import IndexGenerationSnapshot, IndexManager
from novastack.provider import AnswerGeneratorProvider, LocalHuggingFaceProvider, create_default_provider
from novastack.metadata_diagnostics import build_metadata_snapshot_index
from novastack.metadata_reranker import MetadataReranker, MetadataRerankerConfig
from novastack.models import SearchDocument
from novastack.observability import (
    CONTENT_TYPE_PROMETHEUS,
    REQUEST_ID_HEADER,
    generate_request_id,
    get_metrics,
    get_request_id,
    get_tracer,
    log_event,
    reset_request_id,
    set_request_id,
    validate_or_generate_request_id,
)
from novastack.query_understanding import (
    EntityCatalog as QUEntityCatalog,
    QueryUnderstandingExtractor,
)
from novastack.relational_retrieval import (
    StructuredRetriever,
    StructuredRetrieverConfig,
    fuse_hybrid_and_structured,
)
from novastack.service.resilience import (
    AtlasServiceError,
    AtlasTimeoutError,
    CapacityExhaustedError,
    CircuitBreaker,
    CircuitState,
    InferenceConcurrencyLimiter,
    InternalServerError,
    ModelUnavailableError,
    ResilienceConfig,
    sanitize_error_detail,
)
from novastack.service.identity import (
    IdentityAuthenticationError,
    IdentityConfig,
    IdentityConfigurationError,
    IdentityContextMismatchError,
    JwtIdentityVerifier,
    assert_context_matches_identity,
)
from novastack.service.schemas import (
    CallerContext,
    ErrorResponse,
    HealthResponse,
    QueryRequest,
    QueryResponse,
    ReadyResponse,
)

logger = logging.getLogger("novastack.service")


class AtlasServicePipeline:
    """Encapsulates the initialized ATLAS engine components.

    Adheres strictly to concurrency & isolation rules:
    - Stateless query execution (no shared mutable state across requests).
    - Explicit caller context propagation through call stack.
    - Zero caching of unauthorized items in shared structures.
    """

    def __init__(
        self,
        bm25_index: Optional[BM25Index] = None,
        dense_index: Optional[DenseIndex] = None,
        reranker: Optional[MetadataReranker] = None,
        generator: Optional[AnswerGeneratorProvider] = None,
        resolver: Optional[EvidenceResolver] = None,
        catalog: Optional[EntityCatalog] = None,
        qu_extractor: Optional[QueryUnderstandingExtractor] = None,
        structured_retriever: Optional[StructuredRetriever] = None,
        metadata_snapshot_index: Optional[dict[str, Any]] = None,
        index_manager: Optional[IndexManager] = None,
        canary_router: Optional[Any] = None,
        h5_1_qu_extractor: Optional[Any] = None,
    ):
        self.bm25_index = bm25_index
        self.dense_index = dense_index
        self.reranker = reranker
        self.generator = generator
        self.resolver = resolver
        self.catalog = catalog
        self.qu_extractor = qu_extractor
        self.structured_retriever = structured_retriever
        self.metadata_snapshot_index = metadata_snapshot_index or {}
        # IndexManager is the sole source of truth for a live generation.  The
        # legacy fields above remain constructor-compatible for static/test
        # pipelines, but live requests never reread them when a manager exists.
        self.index_manager = index_manager
        # Explicit canary dependencies (RET-EVAL-09)
        from novastack.canary import CanaryConfig, CanaryRouter
        self.canary_router = canary_router or CanaryRouter(CanaryConfig.from_env())
        self.h5_1_qu_extractor = h5_1_qu_extractor

    @property
    def h5_1_extractor(self) -> Any:
        if self.h5_1_qu_extractor is None and self.catalog is not None and self.qu_extractor is not None:
            try:
                from scripts.ret_eval_08_h5_1_experiment import (
                    H5_1EntityResolver,
                    H5_1QueryUnderstandingOverlay,
                )
                h5_1_res = H5_1EntityResolver(self.catalog)
                self.h5_1_qu_extractor = H5_1QueryUnderstandingOverlay(
                    base_extractor=self.qu_extractor,
                    resolver=h5_1_res,
                    catalog=self.catalog,
                )
            except Exception as e:
                logger.warning("Failed to initialize H5.1 entity extractor: %s", e)
        return self.h5_1_qu_extractor

    def get_active_generation_id(self) -> Optional[str]:
        """Return the generation currently visible to *new* requests."""
        if self.index_manager is None:
            return None
        return self.index_manager.get_active_generation_id()

    def _resolver_for_generation(
        self,
        snapshot: Optional[IndexGenerationSnapshot],
    ) -> Optional[EvidenceResolver]:
        """Bind evidence assembly to the same snapshot as retrieval.

        EvidenceResolver is reconstructed with the existing frozen config and
        security indexes, but with the leased generation's immutable documents
        and chunks.  This prevents a new BM25/dense generation from being
        resolved against stale startup-only evidence maps.
        """
        if snapshot is None or self.resolver is None or not isinstance(self.resolver, EvidenceResolver):
            return self.resolver

        bound_resolver = EvidenceResolver(
            documents_index={d.document_id: d for d in snapshot.search_documents},
            chunks_index={c.chunk_id: c for c in snapshot.search_chunks},
            security_fixtures=list(self.resolver.security_fixtures.values()),
            config=self.resolver.config,
            catalog=self.resolver.catalog,
        )
        # Preserve all fixture-derived security state that may not be encoded
        # solely in a document ID/title.  The generation's own documents are
        # also inspected by the constructor above.
        for attr in (
            "poisoned_doc_ids",
            "adversarial_target_doc_ids",
            "instructional_doc_ids",
            "citation_manipulation_doc_ids",
        ):
            getattr(bound_resolver, attr).update(getattr(self.resolver, attr, set()))
        return bound_resolver

    def is_ready(self) -> tuple[bool, dict[str, bool]]:
        """Verify initialization status of core search & generation components with deep integrity validation."""
        if self.index_manager is not None:
            index_ready, index_components = self.index_manager.is_ready()
            bm25_ready = index_components["bm25"]
            dense_ready = index_components["dense"]
        else:
            index_ready = True
            index_components = {}
            bm25_ready = self.bm25_index is not None
            dense_ready = self.dense_index is not None
        reranker_ready = self.reranker is not None
        if self.generator is not None and hasattr(self.generator, "is_ready"):
            generator_ready = self.generator.is_ready()
        else:
            generator_ready = self.generator is not None

        # Deep integrity verification when actual index objects are provided
        if self.index_manager is None and bm25_ready and hasattr(self.bm25_index, "validate_integrity"):
            valid_bm25, _ = self.bm25_index.validate_integrity()
            if not valid_bm25:
                bm25_ready = False

        if self.index_manager is None and dense_ready and hasattr(self.dense_index, "validate_integrity"):
            valid_dense, _ = self.dense_index.validate_integrity()
            if not valid_dense:
                dense_ready = False

        # Verify alignment between BM25 and Dense indexes
        if self.index_manager is None and bm25_ready and dense_ready and hasattr(self.bm25_index, "chunks") and hasattr(self.dense_index, "chunks"):
            if len(self.bm25_index.chunks) != len(self.dense_index.chunks):
                bm25_ready = False
                dense_ready = False

        components = {
            "bm25": bm25_ready,
            "dense": dense_ready,
            "reranker": reranker_ready,
            "generator": generator_ready,
        }
        if self.index_manager is not None:
            components.update(index_components)
        all_ready = index_ready and all(components.values())
        return all_ready, components

    def execute_query(
        self,
        request: QueryRequest,
        timeout_seconds: Optional[float] = None,
        request_id: Optional[str] = None,
    ) -> QueryResponse:
        """Execute one query against a stable leased index generation."""
        lease = self.index_manager.acquire_active_generation() if self.index_manager else None
        if self.index_manager is not None and lease is None:
            raise ModelUnavailableError("No validated active index generation is available")

        snapshot = lease.snapshot if lease is not None else None
        bm25_index = snapshot.bm25_index if snapshot is not None else self.bm25_index
        dense_index = snapshot.dense_index if snapshot is not None else self.dense_index
        metadata_snapshot_index = (
            snapshot.metadata_snapshot_index if snapshot is not None else self.metadata_snapshot_index
        )
        resolver = self._resolver_for_generation(snapshot)
        generation_id = snapshot.generation.generation_id if snapshot is not None else None

        try:
            return self._execute_query_bound(
                request,
                timeout_seconds=timeout_seconds,
                request_id=request_id,
                bm25_index=bm25_index,
                dense_index=dense_index,
                metadata_snapshot_index=metadata_snapshot_index,
                resolver=resolver,
                generation_id=generation_id,
            )
        finally:
            if lease is not None:
                lease.close()

    def _execute_query_bound(
        self,
        request: QueryRequest,
        timeout_seconds: Optional[float],
        request_id: Optional[str],
        bm25_index: Optional[BM25Index],
        dense_index: Optional[DenseIndex],
        metadata_snapshot_index: Any,
        resolver: Optional[EvidenceResolver],
        generation_id: Optional[str],
    ) -> QueryResponse:
        """Run the unchanged intelligence pipeline using one fixed component bundle."""
        t_start = time.perf_counter()
        t_deadline = (t_start + timeout_seconds) if timeout_seconds is not None else None
        query = request.query
        user_ctx = request.user_context
        tenant_id = user_ctx.tenant_id
        eval_id = request.evaluation_id or f"API-{uuid.uuid4().hex[:8]}"
        rid = request_id or getattr(request, "request_id", None) or get_request_id() or f"REQ-{uuid.uuid4().hex[:8]}"

        metrics = get_metrics()
        tracer = get_tracer()

        if t_deadline and time.perf_counter() >= t_deadline:
            raise AtlasTimeoutError(f"Request processing exceeded configured deadline of {timeout_seconds}s")

        # 1. Canary Routing (strictly after verified tenant/identity and deadline check)
        routing_key = request.evaluation_id or query
        variant = "baseline"
        canary_bucket = -1
        if self.canary_router is not None:
            try:
                variant, canary_bucket = self.canary_router.route_request(
                    tenant_id=tenant_id,
                    routing_key=routing_key,
                )
            except Exception as e:
                logger.warning("Canary routing encountered exception; failing closed to baseline: %s", e)
                variant = "baseline"
                canary_bucket = -1

        # 2. Query Understanding & Entity Resolution
        t_qu_start = time.perf_counter()
        extractor = self.h5_1_extractor if (variant == "h5_1" and self.h5_1_extractor is not None) else self.qu_extractor
        qu = None
        if extractor is not None:
            if hasattr(extractor, "resolver"):
                qu_res = extractor.extract(query=query, tenant_id=tenant_id)
                if isinstance(qu_res, tuple):
                    qu, _ = qu_res
                else:
                    qu = qu_res
            else:
                qu = extractor.extract(eval_id, query)

        expanded_q = qu.expanded_query if qu else query
        metrics.record_stage_latency("query_understanding", time.perf_counter() - t_qu_start)

        # 2. Multi-Channel Retrieval bounded to caller tenant
        with tracer.start_span("retrieval") as span_retrieval:
            filters = {"tenant_id": tenant_id}
            t_bm_start = time.perf_counter()
            bm_res = bm25_index.search(query=expanded_q, top_k=50, filters=filters) if bm25_index else []
            metrics.record_stage_latency("bm25", time.perf_counter() - t_bm_start)

            t_dn_start = time.perf_counter()
            dn_res = dense_index.search(query=query, top_k=50, filters=filters) if dense_index else []
            metrics.record_stage_latency("dense", time.perf_counter() - t_dn_start)

            eval_case_dict = {
                "evaluation_id": eval_id,
                "tenant_id": tenant_id,
                "user_id": user_ctx.user_id,
                "user_role": user_ctx.effective_role,
                "user_department": user_ctx.effective_department,
                "expected_access": "allow",
            }

            t_rel_start = time.perf_counter()
            struct_cands = []
            if self.structured_retriever:
                struct_res = self.structured_retriever.retrieve(query=query, eval_case=eval_case_dict, top_k=50)
                struct_cands = struct_res.candidates
            metrics.record_stage_latency("relational_retrieval", time.perf_counter() - t_rel_start)

            t_fus_start = time.perf_counter()
            hybrid = fuse_rrf_sum(bm_res, dn_res, top_k=50, k=60, deduplicate_docs=True)
            combined = fuse_hybrid_and_structured(
                hybrid_candidates=hybrid,
                structured_candidates=struct_cands,
                k=60,
                w_hybrid=1.0,
                w_struct=1.0,
                top_k=50,
                deduplicate_docs=True,
                catalog=self.catalog,
            )
            metrics.record_stage_latency("fusion", time.perf_counter() - t_fus_start)
            span_retrieval.set_attribute("candidate_count", len(combined))

        if t_deadline and time.perf_counter() >= t_deadline:
            raise AtlasTimeoutError(f"Request processing exceeded configured deadline of {timeout_seconds}s")

        # 3. Metadata Reranking
        with tracer.start_span("reranking") as span_rerank:
            t_mr_start = time.perf_counter()
            reranked = self.reranker.rerank(
                candidates=combined,
                qu=qu,
                metadata_index=metadata_snapshot_index,
            ) if self.reranker else combined
            metrics.record_stage_latency("metadata_ranking", time.perf_counter() - t_mr_start)
            span_rerank.set_attribute("reranked_count", len(reranked))

        if t_deadline and time.perf_counter() >= t_deadline:
            raise AtlasTimeoutError(f"Request processing exceeded configured deadline of {timeout_seconds}s")

        # 4. Evidence Assembly & Resolution (Fail-Closed Authorization)
        with tracer.start_span("evidence_resolution") as span_ev:
            if not resolver:
                raise ModelUnavailableError("EvidenceResolver component is not initialized")

            t_ev_start = time.perf_counter()
            channel_cands = {
                "bm25": bm_res,
                "dense": dn_res,
                "structured": struct_cands,
            }
            pkg = resolver.resolve_package(
                query=query,
                candidates=reranked,
                eval_case=eval_case_dict,
                qu=qu,
                channel_candidates=channel_cands,
            )
            metrics.record_stage_latency("evidence_resolution", time.perf_counter() - t_ev_start)
            span_ev.set_attribute("evidence_items_count", len(pkg.items) if pkg and hasattr(pkg, "items") else 0)

        # 5. Grounded Answer Generation
        with tracer.start_span("generation") as span_gen:
            if not self.generator:
                raise ModelUnavailableError("Generator component is not initialized")

            remaining_timeout = (t_deadline - time.perf_counter()) if t_deadline else None
            if remaining_timeout is not None and remaining_timeout <= 0:
                raise AtlasTimeoutError(f"Request processing exceeded configured deadline of {timeout_seconds}s")

            gen_kwargs = {
                "max_evidence_items": 3,
                "prompt_strategy": "config_a_calibrated",
                "citation_resolver": "c2",
                "enable_boundary_stitching": False,
            }
            if hasattr(self.generator, "generate_answer"):
                import inspect
                sig = inspect.signature(self.generator.generate_answer)
                accepts_timeout = "timeout_seconds" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                if accepts_timeout:
                    gen_kwargs["timeout_seconds"] = remaining_timeout

            t_gen_start = time.perf_counter()
            ans_res = self.generator.generate_answer(pkg, **gen_kwargs)
            metrics.record_stage_latency("generation", time.perf_counter() - t_gen_start)
            span_gen.set_attribute("answer_status", ans_res.answer_status)

        if getattr(ans_res, "abstention_reason", None) == "timeout":
            raise AtlasTimeoutError(f"Inference execution exceeded configured deadline of {timeout_seconds}s")

        # 6. Format Structured Citations
        with tracer.start_span("citation_resolution") as span_cit:
            t_cit_start = time.perf_counter()
            formatted_cits = []
            for idx, cit in enumerate(ans_res.citations):
                if hasattr(cit, "to_dict"):
                    formatted_cits.append(cit.to_dict())
                elif hasattr(cit, "__dict__"):
                    formatted_cits.append(cit.__dict__)
                elif isinstance(cit, dict):
                    formatted_cits.append(cit)
                else:
                    formatted_cits.append({"citation_id": f"CIT-{idx:03d}", "raw_tag": str(cit)})
            metrics.record_stage_latency("citation_resolution", time.perf_counter() - t_cit_start)
            span_cit.set_attribute("citation_count", len(formatted_cits))

        # Determine whether model generation was genuinely invoked
        was_gen = getattr(ans_res, "diagnostics", {}).get("layer") == "model_inference" or (
            getattr(ans_res, "generation_latency_ms", 0.0) > 0
            and getattr(ans_res, "answer_status", None) in ("answered", "partially_answered")
        )
        gen_lat = getattr(ans_res, "diagnostics", {}).get("inference_duration_ms", getattr(ans_res, "generation_latency_ms", 0.0))

        metrics.record_stage_latency("total", time.perf_counter() - t_start)
        latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
        return QueryResponse(
            answer_id=ans_res.answer_id,
            query=query,
            answer_text=ans_res.answer_text,
            answer_status=ans_res.answer_status,
            citations=formatted_cits,
            abstention_reason=ans_res.abstention_reason,
            latency_ms=latency_ms,
            request_id=rid,
            was_generation_invoked=bool(was_gen),
            generation_latency_ms=round(float(gen_lat), 2),
            index_generation_id=generation_id,
            canary_variant=variant,
            canary_bucket=canary_bucket,
        )

    @classmethod
    def create_default(
        cls,
        workspace_root: Optional[Path] = None,
        lazy_generator: bool = True,
        generator: Optional[AnswerGeneratorProvider] = None,
    ) -> "AtlasServicePipeline":
        """Factory initializing the production pipeline from disk artifacts."""
        if workspace_root is not None:
            root = workspace_root
        elif "ATLAS_WORKSPACE_ROOT" in os.environ and Path(os.environ["ATLAS_WORKSPACE_ROOT"]).exists():
            root = Path(os.environ["ATLAS_WORKSPACE_ROOT"])
        elif (Path.cwd() / "data" / "processed" / "novastack").exists():
            root = Path.cwd()
        else:
            cur = Path(__file__).resolve()
            candidate = cur.parent.parent.parent.parent
            for parent in [cur] + list(cur.parents):
                if (parent / "data" / "processed" / "novastack").exists():
                    candidate = parent
                    break
            root = candidate
        raw_dir = root / "data" / "raw" / "novastack"
        proc_dir = root / "data" / "processed" / "novastack"

        docs_list = json.loads((proc_dir / "search_documents.json").read_text(encoding="utf-8"))["search_documents"]
        chunks = json.loads((proc_dir / "search_chunks.json").read_text(encoding="utf-8"))["search_chunks"]
        adv_fixtures = json.loads((raw_dir / "adversarial_fixtures.json").read_text(encoding="utf-8"))["adversarial_fixtures"]
        sec_fixtures = json.loads((raw_dir / "security_fixtures.json").read_text(encoding="utf-8"))["security_fixtures"]

        search_documents = [SearchDocument.from_dict(document) for document in docs_list]
        metadata_snapshot_index = build_metadata_snapshot_index(docs_list, adv_fixtures)
        catalog = EntityCatalog(raw_dir, proc_dir / "search_chunks.json")
        qu_catalog = QUEntityCatalog(raw_dir)
        qu_extractor = QueryUnderstandingExtractor(qu_catalog)
        structured_retriever = StructuredRetriever(catalog=catalog, config=StructuredRetrieverConfig())

        dense_index = DenseIndex.load(
            chunks_path=proc_dir / "search_chunks.json",
            embeddings_path=proc_dir / "dense_embeddings.npz",
            metadata_path=proc_dir / "dense_index_metadata.json",
        )
        # Use the dense index's validated chunk ordering for both retrieval
        # channels before publishing the first live generation.
        bm25_config = BM25Config(k1=1.5, b=0.75)
        bm25_index = BM25Index.build_index(dense_index.chunks, config=bm25_config)
        index_manager = IndexManager(
            dense_config=dense_index.config,
            bm25_config=bm25_config,
        )
        index_manager.initialize_from_components(
            search_documents=search_documents,
            search_chunks=dense_index.chunks,
            bm25_index=bm25_index,
            dense_index=dense_index,
            metadata_snapshot_index=metadata_snapshot_index,
        )
        reranker = MetadataReranker(MetadataRerankerConfig())

        resolver = EvidenceResolver.load_from_paths(
            search_documents_path=proc_dir / "search_documents.json",
            search_chunks_path=proc_dir / "search_chunks.json",
            adversarial_fixtures_path=raw_dir / "adversarial_fixtures.json",
            security_fixtures_path=raw_dir / "security_fixtures.json",
            config=EvidenceResolverConfig(enable_query_aware_authority=True, enable_event_bundling=False),
            catalog=catalog,
        )

        if generator is None:
            doc_ids = {d["document_id"] for d in docs_list}
            chunk_ids = {c["chunk_id"] for c in chunks}
            generator = create_default_provider(
                lazy_load=lazy_generator,
                corpus_doc_ids=doc_ids,
                corpus_chunk_ids=chunk_ids,
            )

        from novastack.canary import CanaryConfig, CanaryRouter
        canary_router = CanaryRouter(CanaryConfig.from_env())
        h5_1_qu_extractor = None
        try:
            from scripts.ret_eval_08_h5_1_experiment import (
                H5_1EntityResolver,
                H5_1QueryUnderstandingOverlay,
            )
            h5_1_res = H5_1EntityResolver(catalog)
            h5_1_qu_extractor = H5_1QueryUnderstandingOverlay(
                base_extractor=qu_extractor,
                resolver=h5_1_res,
                catalog=catalog,
            )
        except Exception as e:
            logger.warning("H5.1 extractor initialization deferred in create_default: %s", e)

        return cls(
            bm25_index=bm25_index,
            dense_index=dense_index,
            reranker=reranker,
            generator=generator,
            resolver=resolver,
            catalog=catalog,
            qu_extractor=qu_extractor,
            structured_retriever=structured_retriever,
            metadata_snapshot_index=metadata_snapshot_index,
            index_manager=index_manager,
            canary_router=canary_router,
            h5_1_qu_extractor=h5_1_qu_extractor,
        )


def create_app(
    pipeline: Optional[AtlasServicePipeline] = None,
    resilience_config: Optional[ResilienceConfig] = None,
    identity_config: Optional[IdentityConfig] = None,
    inference_provider: Optional[AnswerGeneratorProvider] = None,
) -> FastAPI:
    """Create the FastAPI application with fail-closed identity verification and pluggable inference provider."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The module-level ASGI app is created at import time, whereas process
        # configuration is an operational concern at startup.  Re-read the
        # default-only verifier configuration here so import ordering cannot
        # create a stale identity state.  Explicit dependency-injected test or
        # host configurations remain immutable.
        if not app.state.identity_config_explicit:
            app.state.identity_config = IdentityConfig.from_env()
            app.state.identity_verifier = JwtIdentityVerifier(app.state.identity_config)
        if not hasattr(app.state, "pipeline") or app.state.pipeline is None:
            try:
                app.state.pipeline = AtlasServicePipeline.create_default(
                    lazy_generator=True,
                    generator=getattr(app.state, "inference_provider", None),
                )
            except Exception as e:
                logger.warning("Pipeline default initialization deferred: %s", e)
        yield
        if hasattr(app.state, "executor") and app.state.executor is not None:
            app.state.executor.shutdown(wait=False)

    app = FastAPI(
        title="ATLAS Enterprise Search & Answering API",
        version="4T.0.0",
        lifespan=lifespan,
    )
    res_cfg = resilience_config or ResilienceConfig.from_env()
    if pipeline is not None and inference_provider is not None:
        pipeline.generator = inference_provider
    app.state.pipeline = pipeline
    app.state.inference_provider = inference_provider
    app.state.resilience_config = res_cfg
    app.state.identity_config = identity_config or IdentityConfig.from_env()
    app.state.identity_config_explicit = identity_config is not None
    app.state.identity_verifier = JwtIdentityVerifier(app.state.identity_config)
    app.state.circuit_breaker = CircuitBreaker(
        failure_threshold=res_cfg.circuit_failure_threshold,
        cooldown_seconds=res_cfg.circuit_cooldown_seconds,
        enabled=res_cfg.enable_circuit_breaker,
    )
    app.state.limiter = InferenceConcurrencyLimiter(
        max_concurrent=res_cfg.max_concurrent_inferences,
        queue_timeout=res_cfg.queue_timeout_seconds,
    )
    app.state.executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=max(2, res_cfg.max_concurrent_inferences + 1)
    )

    # Exception Handler: RequestValidationError -> 422 JSON
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        rid = getattr(request.state, "request_id", None) or get_request_id() or generate_request_id()
        metrics = get_metrics()
        metrics.record_request(endpoint="/query", method=request.method, status=status.HTTP_422_UNPROCESSABLE_ENTITY, duration_seconds=0.0)
        metrics.record_error("ValidationError", status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        log_event(
            logger,
            "request_validation_failed",
            level=logging.WARNING,
            request_id=rid,
            endpoint=str(request.url.path),
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_type="ValidationError",
            answer_status="error",
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            headers={REQUEST_ID_HEADER: rid},
            content={
                "detail": sanitize_error_detail(str(exc)),
                "error_type": "ValidationError",
                "answer_status": "error",
            },
        )

    # Exception Handler: AtlasTimeoutError -> 504 Gateway Timeout
    @app.exception_handler(AtlasTimeoutError)
    async def timeout_exception_handler(request: Request, exc: AtlasTimeoutError):
        rid = getattr(request.state, "request_id", None) or get_request_id() or generate_request_id()
        metrics = get_metrics()
        metrics.record_request(endpoint="/query", method=request.method, status=status.HTTP_504_GATEWAY_TIMEOUT, duration_seconds=0.0)
        metrics.record_error("TimeoutError", status=status.HTTP_504_GATEWAY_TIMEOUT)
        metrics.record_timeout()
        log_event(
            logger,
            "request_timeout",
            level=logging.ERROR,
            request_id=rid,
            endpoint=str(request.url.path),
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            error_type="TimeoutError",
            answer_status="timeout",
        )
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            headers={REQUEST_ID_HEADER: rid},
            content={
                "detail": exc.detail,
                "error_type": "TimeoutError",
                "answer_status": "timeout",
                "citations": [],
            },
        )

    # Exception Handler: ModelUnavailableError -> 503 Service Unavailable
    @app.exception_handler(ModelUnavailableError)
    async def model_unavailable_handler(request: Request, exc: ModelUnavailableError):
        rid = getattr(request.state, "request_id", None) or get_request_id() or generate_request_id()
        metrics = get_metrics()
        metrics.record_request(endpoint="/query", method=request.method, status=status.HTTP_503_SERVICE_UNAVAILABLE, duration_seconds=0.0)
        metrics.record_error("ModelUnavailableError", status=status.HTTP_503_SERVICE_UNAVAILABLE)
        metrics.record_model_error()
        log_event(
            logger,
            "model_unavailable",
            level=logging.ERROR,
            request_id=rid,
            endpoint=str(request.url.path),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_type="ModelUnavailableError",
            answer_status="error",
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={REQUEST_ID_HEADER: rid},
            content={
                "detail": exc.detail,
                "error_type": "ModelUnavailableError",
                "answer_status": "error",
            },
        )

    # Exception Handler: CapacityExhaustedError -> 429 Too Many Requests
    @app.exception_handler(CapacityExhaustedError)
    async def capacity_exhausted_handler(request: Request, exc: CapacityExhaustedError):
        rid = getattr(request.state, "request_id", None) or get_request_id() or generate_request_id()
        metrics = get_metrics()
        metrics.record_request(endpoint="/query", method=request.method, status=status.HTTP_429_TOO_MANY_REQUESTS, duration_seconds=0.0)
        metrics.record_error("CapacityExhaustedError", status=status.HTTP_429_TOO_MANY_REQUESTS)
        metrics.record_capacity_exhausted()
        log_event(
            logger,
            "capacity_exhausted",
            level=logging.WARNING,
            request_id=rid,
            endpoint=str(request.url.path),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_type="CapacityExhaustedError",
            answer_status="error",
        )
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={REQUEST_ID_HEADER: rid},
            content={
                "detail": exc.detail,
                "error_type": "CapacityExhaustedError",
                "answer_status": "error",
            },
        )

    # Exception Handler: HTTPException -> maintain HTTP status
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        rid = getattr(request.state, "request_id", None) or get_request_id() or generate_request_id()
        metrics = get_metrics()
        metrics.record_request(endpoint=str(request.url.path), method=request.method, status=exc.status_code, duration_seconds=0.0)
        metrics.record_error("HTTPException", status=exc.status_code)
        log_event(
            logger,
            "http_exception",
            level=logging.WARNING,
            request_id=rid,
            endpoint=str(request.url.path),
            status_code=exc.status_code,
            error_type="HTTPException",
            answer_status="error",
        )
        response_headers = dict(exc.headers or {})
        response_headers[REQUEST_ID_HEADER] = rid
        return JSONResponse(
            status_code=exc.status_code,
            headers=response_headers,
            content={
                "detail": sanitize_error_detail(str(exc.detail)),
                "error_type": "HTTPException",
                "answer_status": "error",
            },
        )

    # Exception Handler: Top-level unhandled exception -> 500 without stack trace
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", None) or get_request_id() or generate_request_id()
        metrics = get_metrics()
        metrics.record_request(endpoint=str(request.url.path), method=request.method, status=status.HTTP_500_INTERNAL_SERVER_ERROR, duration_seconds=0.0)
        metrics.record_error(type(exc).__name__, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        log_event(
            logger,
            "unhandled_internal_error",
            level=logging.ERROR,
            request_id=rid,
            endpoint=str(request.url.path),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_type=type(exc).__name__,
            answer_status="error",
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            headers={REQUEST_ID_HEADER: rid},
            content={
                "detail": "Internal server processing failure",
                "error_type": type(exc).__name__,
                "answer_status": "error",
            },
        )

    @app.get("/healthz", response_model=HealthResponse, status_code=status.HTTP_200_OK)
    async def healthz():
        """Process liveness probe."""
        return HealthResponse(status="ok")

    @app.get("/ready", response_model=ReadyResponse)
    async def ready():
        """Component readiness probe including the identity-verification boundary."""
        identity_config: IdentityConfig = app.state.identity_config
        pipe = getattr(app.state, "pipeline", None)
        if pipe is None:
            components = {
                "bm25": False,
                "dense": False,
                "reranker": False,
                "generator": False,
                "authentication": identity_config.is_configured,
            }
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unready", "components": components, "active_generation_id": None},
            )
        all_ready, components = pipe.is_ready()
        components["authentication"] = identity_config.is_configured
        active_generation_id = pipe.get_active_generation_id()
        if not all_ready or not identity_config.is_configured:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "unready",
                    "components": components,
                    "active_generation_id": active_generation_id,
                },
            )
        return ReadyResponse(
            status="ready",
            components=components,
            active_generation_id=active_generation_id,
        )

    @app.get("/metrics")
    async def metrics():
        """Prometheus-compatible metrics exposition endpoint."""
        return Response(content=get_metrics().generate_prometheus_text(), media_type=CONTENT_TYPE_PROMETHEUS)

    @app.post("/query", response_model=QueryResponse, status_code=status.HTTP_200_OK)
    async def query(req: QueryRequest, request: Request, response: Response):
        """Execute a query under a cryptographically verified caller identity."""
        # 1. Resolve and propagate Request ID
        supplied_header_id = request.headers.get("x-request-id") or request.headers.get(REQUEST_ID_HEADER)
        supplied_id = getattr(req, "request_id", None) or supplied_header_id
        rid = validate_or_generate_request_id(supplied_id)
        request.state.request_id = rid
        token = set_request_id(rid)
        response.headers[REQUEST_ID_HEADER] = rid

        t_req_start = time.perf_counter()
        tracer = get_tracer()
        metrics = get_metrics()

        verifier: JwtIdentityVerifier = app.state.identity_verifier
        try:
            identity = verifier.verify_authorization_header(request.headers.get("authorization"))
            assert_context_matches_identity(req.user_context, identity)
        except IdentityConfigurationError:
            metrics.record_identity_verification_failure("configuration")
            log_event(
                logger,
                "identity_verification_failed",
                level=logging.ERROR,
                request_id=rid,
                endpoint="/query",
                identity_outcome="configuration",
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Identity verification is not configured",
            )
        except IdentityAuthenticationError:
            metrics.record_identity_verification_failure("authentication")
            log_event(
                logger,
                "identity_verification_failed",
                level=logging.WARNING,
                request_id=rid,
                endpoint="/query",
                identity_outcome="authentication",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer authentication failed",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except IdentityContextMismatchError:
            metrics.record_identity_verification_failure("context_mismatch")
            log_event(
                logger,
                "identity_verification_failed",
                level=logging.WARNING,
                request_id=rid,
                endpoint="/query",
                identity_outcome="context_mismatch",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Verified identity does not match requested caller context",
            )

        # The body is retained for backwards-compatible request routing only;
        # all authorization attributes consumed by the pipeline come from the
        # verified JWT claims, never from arbitrary client JSON.
        req = req.model_copy(update={"user_context": identity.to_caller_context()})

        pipe = getattr(app.state, "pipeline", None)
        if pipe is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service pipeline not initialized",
            )
        all_ready, _ = pipe.is_ready()
        if not all_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service pipeline components not ready",
            )

        cb: Optional[CircuitBreaker] = getattr(app.state, "circuit_breaker", None)
        if cb and not cb.can_execute():
            raise ModelUnavailableError("Circuit breaker OPEN: model cooling down after repeated failures")

        limiter: Optional[InferenceConcurrencyLimiter] = getattr(app.state, "limiter", None)
        acquired = False
        if limiter:
            acquired = await limiter.acquire()
            if not acquired:
                raise CapacityExhaustedError("Inference capacity exhausted; please retry later")

        cfg: ResilienceConfig = getattr(app.state, "resilience_config", None) or ResilienceConfig()
        timeout = cfg.request_timeout_seconds
        loop = asyncio.get_running_loop()
        executor = getattr(app.state, "executor", None)

        def _run_query():
            import inspect
            sig = inspect.signature(pipe.execute_query)
            kwargs = {}
            if "timeout_seconds" in sig.parameters:
                kwargs["timeout_seconds"] = timeout
            if "request_id" in sig.parameters:
                kwargs["request_id"] = rid
            return pipe.execute_query(req, **kwargs)

        with tracer.start_span("request") as root_span:
            root_span.set_attribute("endpoint", "/query")
            try:
                if executor:
                    ctx = contextvars.copy_context()
                    resp = await asyncio.wait_for(
                        loop.run_in_executor(executor, ctx.run, _run_query),
                        timeout=timeout,
                    )
                else:
                    resp = _run_query()

                if getattr(resp, "answer_status", None) == "timeout":
                    raise AtlasTimeoutError(f"Request processing exceeded configured deadline of {timeout}s")

                if cb:
                    cb.record_success()

                if hasattr(resp, "request_id"):
                    resp.request_id = rid

                if hasattr(resp, "was_generation_invoked"):
                    response.headers["x-generation-invoked"] = str(resp.was_generation_invoked).lower()
                if hasattr(resp, "generation_latency_ms"):
                    response.headers["x-generation-latency-ms"] = str(resp.generation_latency_ms)
                if hasattr(resp, "canary_variant"):
                    response.headers["x-canary-variant"] = str(resp.canary_variant)
                if hasattr(resp, "canary_bucket"):
                    response.headers["x-canary-bucket"] = str(resp.canary_bucket)

                duration_s = time.perf_counter() - t_req_start
                metrics.record_request(endpoint="/query", method="POST", status=200, duration_seconds=duration_s)
                metrics.record_answer_status(resp.answer_status)
                log_event(
                    logger,
                    "query_completed",
                    level=logging.INFO,
                    request_id=rid,
                    endpoint="/query",
                    status_code=200,
                    answer_status=resp.answer_status,
                    latency_ms=duration_s * 1000.0,
                    tenant_id=req.user_context.tenant_id,
                    evaluation_id=req.evaluation_id,
                    canary_variant=getattr(resp, "canary_variant", "baseline"),
                    canary_bucket=getattr(resp, "canary_bucket", -1),
                    candidate_version="0.4.14-rc1+h5.1" if getattr(resp, "canary_variant", "baseline") == "h5_1" else "0.4.14-rc1",
                    routing_key_type="evaluation_id" if req.evaluation_id else "query",
                    retrieval_configuration="B4+H1(H5.1)+H3" if getattr(resp, "canary_variant", "baseline") == "h5_1" else "B4+H1(H5)+H3",
                )
                return resp
            except (asyncio.TimeoutError, TimeoutError):
                if cb:
                    cb.record_failure()
                root_span.set_status("ERROR", error_type="TimeoutError")
                raise AtlasTimeoutError(f"Request processing exceeded configured deadline of {timeout}s")
            except AtlasServiceError as e:
                if cb:
                    cb.record_failure()
                root_span.set_status("ERROR", error_type=type(e).__name__)
                raise
            except HTTPException as e:
                root_span.set_status("ERROR", error_type="HTTPException")
                raise
            except Exception as e:
                if cb:
                    cb.record_failure()
                root_span.set_status("ERROR", error_type=type(e).__name__)
                raise
            finally:
                if limiter and acquired:
                    limiter.release()
                reset_request_id(token)

    return app


app = create_app()
