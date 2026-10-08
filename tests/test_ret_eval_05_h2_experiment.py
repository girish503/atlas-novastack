"""Unit and Regression Tests for RET-EVAL-05 H2 Controlled Experiment.

Verifies:
1. Mandatory H2 phrase resolutions (EVAL-0042..EVAL-0050).
2. Collision prevention (generic single tokens do NOT resolve to events).
3. Substring safety ("misconfigured" does NOT match "config").
4. Deterministic precedence ordering (Exact ID > Multi-token phrase > Single-token alias).
5. Downstream H1 graph reachability and activation.
6. Fail-closed security boundaries: tenant isolation and forbidden document exclusion.
"""

import json
from pathlib import Path
import pytest

from novastack.entity_catalog import EntityCatalog
from novastack.query_understanding import (
    EntityCatalog as QUEntityCatalog,
    QueryUnderstandingExtractor,
)
from novastack.relational_retrieval import (
    StructuredRetriever,
    StructuredRetrieverConfig,
)
from scripts.ret_eval_03_h1_experiment import H1StructuredRetrieverOverlay
from scripts.ret_eval_05_h2_experiment import (
    H2EntityResolutionRegistry,
    H2QueryUnderstandingOverlay,
    H2StructuredRetrieverAdapter,
    normalize_query_phrase,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_RAW_DIR = _DATA_DIR / "raw" / "novastack"
_PROC_DIR = _DATA_DIR / "processed" / "novastack"


@pytest.fixture(scope="module")
def shared_catalog():
    return EntityCatalog(_RAW_DIR, _PROC_DIR / "search_chunks.json")


@pytest.fixture(scope="module")
def h2_registry(shared_catalog):
    return H2EntityResolutionRegistry(catalog=shared_catalog, raw_dir=_RAW_DIR)


@pytest.fixture(scope="module")
def h2_qu_overlay(shared_catalog, h2_registry):
    qu_catalog = QUEntityCatalog(_RAW_DIR)
    base_qu = QueryUnderstandingExtractor(qu_catalog)
    return H2QueryUnderstandingOverlay(
        base_extractor=base_qu,
        registry=h2_registry,
        catalog=shared_catalog,
    )


# -----------------------------------------------------------------------------
# 1. Mandatory H2 Phrase Resolution Tests
# -----------------------------------------------------------------------------

def test_h2_checkout_outage_resolution(h2_registry):
    matches = h2_registry.resolve_query("Trace the checkout outage causal chain")
    match_ids = [m["entity_id"] for m in matches]
    assert "EVT-NS-0001" in match_ids, "checkout outage must resolve to EVT-NS-0001"


def test_h2_authentication_failure_resolution(h2_registry):
    matches = h2_registry.resolve_query("What happened during the authentication failure?")
    match_ids = [m["entity_id"] for m in matches]
    assert "EVT-NS-0002" in match_ids, "authentication failure must resolve to EVT-NS-0002"


def test_h2_payment_failure_resolution(h2_registry):
    matches = h2_registry.resolve_query("Trace the payment failure across microservices")
    match_ids = [m["entity_id"] for m in matches]
    assert "EVT-NS-0003" in match_ids, "payment failure must resolve to EVT-NS-0003"


def test_h2_search_latency_resolution(h2_registry):
    matches = h2_registry.resolve_query("Why did search latency spike to 10 seconds?")
    match_ids = [m["entity_id"] for m in matches]
    assert "EVT-NS-0004" in match_ids, "search latency must resolve to EVT-NS-0004"


def test_h2_api_throttling_resolution(h2_registry):
    matches = h2_registry.resolve_query("Trace the API throttling incident and HTTP 429 errors")
    match_ids = [m["entity_id"] for m in matches]
    assert "EVT-NS-0007" in match_ids, "api throttling must resolve to EVT-NS-0007"


def test_h2_customer_billing_errors_resolution(h2_registry):
    matches = h2_registry.resolve_query("Customer billing errors reported in support tickets")
    match_ids = [m["entity_id"] for m in matches]
    assert "EVT-NS-0008" in match_ids, "customer billing errors must resolve to EVT-NS-0008"


# -----------------------------------------------------------------------------
# 2. Collision Prevention Tests (Generic Single Tokens Must NOT Resolve Standalone)
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("generic_query", [
    "checkout",
    "payment",
    "search",
    "failure",
    "outage",
    "latency",
    "authentication",
    "api",
])
def test_generic_single_tokens_do_not_resolve_to_events(h2_registry, generic_query):
    matches = h2_registry.resolve_query(generic_query)
    # Generic single tokens must produce 0 H2 multi-token event matches
    event_matches = [m for m in matches if m["entity_type"] == "event"]
    assert len(event_matches) == 0, f"Generic token '{generic_query}' must NOT match standalone event"


def test_customer_billing_alone_not_broadly_aliased(h2_registry):
    # Enforces prompt rule: 'customer billing' alone must NOT be broadly aliased
    matches = h2_registry.resolve_query("customer billing")
    assert len(matches) == 0, "Broad alias 'customer billing' must not resolve standalone without error/discrepancy"


# -----------------------------------------------------------------------------
# 3. Substring & Token Boundary Safety Tests
# -----------------------------------------------------------------------------

def test_misconfigured_does_not_match_config(h2_registry, h2_qu_overlay):
    # Verify 'misconfigured' does not trigger false match
    qu, h2_matches = h2_qu_overlay.extract("TEST-001", "The service was misconfigured in production")
    matched_ids = [m["entity_id"] for m in h2_matches]
    assert "SVC-NS-0007" not in matched_ids
    assert len(h2_matches) == 0


def test_normalization_whitespace_and_punctuation():
    norm1 = normalize_query_phrase("checkout   outage!!!")
    assert norm1 == "checkout outage"
    norm2 = normalize_query_phrase("API   throttling?")
    assert norm2 == "api throttling"


# -----------------------------------------------------------------------------
# 4. Precedence & QU Overlay Tests
# -----------------------------------------------------------------------------

def test_precedence_exact_id_and_multi_token_phrase(h2_qu_overlay):
    query = "EVT-NS-0001 checkout outage in staging"
    qu, h2_matches = h2_qu_overlay.extract("TEST-PREC-01", query)
    entity_ids = [e.entity_id for e in qu.entities]
    assert entity_ids[0] == "EVT-NS-0001"
    # Ensure no duplicate entity mentions for EVT-NS-0001
    assert entity_ids.count("EVT-NS-0001") == 1


def test_h2_phrase_outranks_single_token_service_alias(h2_qu_overlay):
    # In 'checkout outage', 'checkout' alone would match TEAM-NS-0003 or SVC-NS-0005.
    # H2 event anchor EVT-NS-0001 must be prioritized at the head of the entities list.
    qu, h2_matches = h2_qu_overlay.extract("TEST-PREC-02", "Trace the checkout outage")
    assert len(qu.entities) > 0
    assert qu.entities[0].entity_id == "EVT-NS-0001", "EVT-NS-0001 must be first entity mention"


# -----------------------------------------------------------------------------
# 5. Downstream H1 Graph Reachability Tests
# -----------------------------------------------------------------------------

def test_h2_activates_h1_depth_2_traversal(shared_catalog, h2_registry):
    s_config = StructuredRetrieverConfig(
        max_neighbors_per_hop=10,
        enable_runbook_reverse_index=True,
    )
    base_retriever = StructuredRetriever(shared_catalog, s_config)
    adapter = H2StructuredRetrieverAdapter(base_retriever, h2_registry, shared_catalog)
    h1_overlay = H1StructuredRetrieverOverlay(adapter)

    eval_case = {
        "evaluation_id": "EVAL-0044",
        "tenant_id": "TENANT-NOVASTACK",
        "user_role": None,
        "forbidden_document_ids": [],
    }
    struct_res, diags = h1_overlay.retrieve(
        "Trace the full causal chain of the checkout outage: what symptom appeared, which service was affected, which deployment caused it, and which PR resolved it?",
        eval_case,
        top_k=50,
    )

    retrieved_docs = {c.document_id for c in struct_res.candidates}
    assert "DOC-PM-EVT-NS-0001-01" in retrieved_docs, "Postmortem must be in candidate pool"
    assert "DOC-DEP-DEP-NS-0001-01" in retrieved_docs, "Deployment changelog must be in candidate pool"
    assert "DOC-PR-PR-NS-0001-01" in retrieved_docs, "Fixing PR must be in candidate pool"


# -----------------------------------------------------------------------------
# 6. Security Boundaries & Fail-Closed Invariants
# -----------------------------------------------------------------------------

def test_h2_respects_forbidden_documents(shared_catalog, h2_registry):
    s_config = StructuredRetrieverConfig(max_neighbors_per_hop=10)
    base_retriever = StructuredRetriever(shared_catalog, s_config)
    adapter = H2StructuredRetrieverAdapter(base_retriever, h2_registry, shared_catalog)
    h1_overlay = H1StructuredRetrieverOverlay(adapter)

    forbidden_id = "DOC-PM-EVT-NS-0001-01"
    eval_case = {
        "evaluation_id": "SEC-TEST-01",
        "tenant_id": "TENANT-NOVASTACK",
        "forbidden_document_ids": [forbidden_id],
    }
    struct_res, _ = h1_overlay.retrieve(
        "checkout outage",
        eval_case,
        top_k=50,
    )
    retrieved_docs = {c.document_id for c in struct_res.candidates}
    assert forbidden_id not in retrieved_docs, "Forbidden document must NEVER appear in candidate pool"


def test_h2_respects_tenant_isolation(shared_catalog, h2_registry):
    s_config = StructuredRetrieverConfig(max_neighbors_per_hop=10)
    base_retriever = StructuredRetriever(shared_catalog, s_config)
    adapter = H2StructuredRetrieverAdapter(base_retriever, h2_registry, shared_catalog)
    h1_overlay = H1StructuredRetrieverOverlay(adapter)

    # Foreign tenant querying NovaStack incident
    eval_case = {
        "evaluation_id": "SEC-TEST-02",
        "tenant_id": "TENANT-ACME-CORP",
        "forbidden_document_ids": [],
    }
    struct_res, _ = h1_overlay.retrieve(
        "checkout outage",
        eval_case,
        top_k=50,
    )
    retrieved_docs = {c.document_id for c in struct_res.candidates}
    assert len(retrieved_docs) == 0, "Foreign tenant must receive ZERO candidate chunks from NovaStack graph"
