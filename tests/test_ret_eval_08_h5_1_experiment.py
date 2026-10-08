"""Mechanism, coverage expansion, and security tests for RET-EVAL-08 H5.1 experiment."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from novastack.entity_catalog import EntityCatalog
from novastack.relational_retrieval import StructuredRetriever
from scripts.ret_eval_03_h1_experiment import H1StructuredRetrieverOverlay
from scripts.ret_eval_08_h5_1_experiment import (
    H5_1EntityResolver,
    H5_1StructuredRetrieverAdapter,
    PhraseEntry,
    normalize_h5_1_tokens,
)

_ROOT = Path(__file__).resolve().parent.parent
_RAW = _ROOT / "data" / "raw" / "novastack"
_PROCESSED = _ROOT / "data" / "processed" / "novastack"
_TENANT = "TENANT-NOVASTACK"


@pytest.fixture(scope="module")
def catalog() -> EntityCatalog:
    return EntityCatalog(_RAW, _PROCESSED / "search_chunks.json")


@pytest.fixture()
def resolver(catalog: EntityCatalog) -> H5_1EntityResolver:
    return H5_1EntityResolver(catalog)


def _ids(result):
    return [entity.entity_id for entity in result.entities]


def _primary(result):
    return [entity for entity in result.entities if entity.match_method != "structural_expansion"]


# -----------------------------------------------------------------------------
# 1. Mechanism and Identifier Tests
# -----------------------------------------------------------------------------

def test_exact_identifier_resolution(resolver):
    result = resolver.resolve("Trace INC-NS-0002 across all services", _TENANT)
    assert _primary(result)[0].entity_id == "INC-NS-0002"
    assert _primary(result)[0].match_method == "exact_id"


def test_catalog_synchronized_service_alias(resolver):
    result = resolver.resolve("Is checkout service healthy?", _TENANT)
    match = next(entity for entity in _primary(result) if entity.entity_id == "SVC-NS-0005")
    assert match.match_method == "synchronized_service_alias"


def test_corrected_service_aliases_do_not_leak_authentication_to_media_service(resolver):
    # Authentication must NOT map to media-service (SVC-NS-0006)
    assert "SVC-NS-0006" not in _ids(resolver.resolve("authentication", _TENANT))
    assert "SVC-NS-0008" in _ids(resolver.resolve("log aggregator", _TENANT))


# -----------------------------------------------------------------------------
# 2. High-Confidence Coverage Expansion Tests
# -----------------------------------------------------------------------------

def test_canonical_event_sequence_resolution(resolver):
    result = resolver.resolve("What was discussed regarding search latency?", _TENANT)
    assert "EVT-NS-0004" in _ids(result)
    match = next(e for e in _primary(result) if e.entity_id == "EVT-NS-0004")
    assert match.match_method in ("canonical_event_sequence", "normalized_phrase")


def test_canonical_incident_sequence_resolution(resolver):
    result = resolver.resolve("Why were there checkout requests timing out?", _TENANT)
    assert "INC-NS-0001" in _ids(result)


def test_singular_plural_inflection_normalization(resolver):
    # 'requests' -> 'request', 'failures' -> 'failure'
    result1 = resolver.resolve("authentication failures reported", _TENANT)
    result2 = resolver.resolve("authentication failure reported", _TENANT)
    assert _ids(result1) == _ids(result2)
    assert "INC-NS-0002" in _ids(result1)


def test_operational_failure_anchor_variant(resolver):
    result = resolver.resolve("Was the checkout failure investigated?", _TENANT)
    assert "EVT-NS-0001" in _ids(result)


def test_deployment_configuration_sequence_resolution(resolver):
    result = resolver.resolve("Check connection pool configuration settings", _TENANT)
    assert "DEP-NS-0001" in _ids(result)


def test_service_operational_description_resolution(resolver):
    result = resolver.resolve("Check API rate limit quota and throttling", _TENANT)
    assert "SVC-NS-0010" in _ids(result)


def test_token_expiration_resolution_for_auth(resolver):
    result = resolver.resolve("What is the session token expiration TTL?", _TENANT)
    assert "SVC-NS-0001" in _ids(result)


def test_billing_invoice_discrepancy_resolution(resolver):
    result = resolver.resolve("Trace the billing invoice discrepancy", _TENANT)
    assert "EVT-NS-0008" in _ids(result)


# -----------------------------------------------------------------------------
# 3. Disambiguation and Priority Tests
# -----------------------------------------------------------------------------

def test_canonical_service_identity_not_overwritten_by_event(resolver):
    # media-service belongs to SVC-NS-0006, not EVT-NS-0009
    result = resolver.resolve("Which team owns media-service?", _TENANT)
    assert _primary(result)[0].entity_id == "SVC-NS-0006"


def test_data_warehouse_belongs_to_service(resolver):
    result = resolver.resolve("Which team owns data-warehouse?", _TENANT)
    assert _primary(result)[0].entity_id == "SVC-NS-0003"


def test_ambiguous_phrase_abstains_when_priorities_equal(resolver):
    resolver._phrases[("test", "collision")].append(
        PhraseEntry("SVC-NS-0001", "service", "feature-flags", "exact_canonical_phrase")
    )
    resolver._phrases[("test", "collision")].append(
        PhraseEntry("SVC-NS-0002", "service", "analytics-pipeline", "exact_canonical_phrase")
    )
    result = resolver.resolve("test collision", _TENANT)
    assert result.entities == []
    assert len(result.ambiguities) == 1
    assert result.ambiguities[0]["match_method"] == "ambiguous"


def test_ambiguous_resolution_cannot_seed_structured_retrieval(catalog, resolver):
    resolver._phrases[("collision", "probe")].append(
        PhraseEntry("SVC-NS-0001", "service", "feature-flags", "exact_canonical_phrase")
    )
    resolver._phrases[("collision", "probe")].append(
        PhraseEntry("SVC-NS-0002", "service", "analytics-pipeline", "exact_canonical_phrase")
    )
    adapter = H5_1StructuredRetrieverAdapter(StructuredRetriever(catalog), resolver, catalog)
    adapter.set_tenant_context(_TENANT)
    structured, _ = H1StructuredRetrieverOverlay(adapter).retrieve(
        "collision probe",
        {"tenant_id": _TENANT, "forbidden_document_ids": []},
    )
    assert structured.extracted_entities == []
    assert structured.candidates == []


# -----------------------------------------------------------------------------
# 4. Graph Expansion & Provenance Tests
# -----------------------------------------------------------------------------

def test_incident_one_hop_expands_to_linked_event(resolver):
    result = resolver.resolve("INC-NS-0002", _TENANT)
    expansion = next(entity for entity in result.entities if entity.entity_id == "EVT-NS-0002")
    assert expansion.match_method == "structural_expansion"
    assert expansion.source_entity_id == "INC-NS-0002"


def test_deployment_one_hop_expands_to_event_and_service(resolver):
    result = resolver.resolve("DEP-NS-0002", _TENANT)
    expansions = {
        entity.entity_id: entity
        for entity in result.entities
        if entity.match_method == "structural_expansion"
    }
    assert {"EVT-NS-0002", "SVC-NS-0001"}.issubset(expansions)
    assert all(entity.source_entity_id == "DEP-NS-0002" for entity in expansions.values())


def test_structural_expansion_carries_provenance(resolver):
    result = resolver.resolve("EVT-NS-0002", _TENANT)
    expansions = [
        entity for entity in result.entities if entity.match_method == "structural_expansion"
    ]
    assert expansions
    assert all(entity.source_entity_id for entity in expansions)
    assert all(entity.relationship_type for entity in expansions)
    assert all(entity.canonical_phrase for entity in expansions)


def test_cycles_cannot_expand_beyond_one_hop(resolver):
    result = resolver.resolve("EVT-NS-0002", _TENANT)
    expansions = [
        entity for entity in result.entities if entity.match_method == "structural_expansion"
    ]
    expansion_ids = {entity.entity_id for entity in expansions}
    assert all(entity.source_entity_id == "EVT-NS-0002" for entity in expansions)
    assert not any(entity.source_entity_id in expansion_ids for entity in expansions)


# -----------------------------------------------------------------------------
# 5. Security and Tenant Isolation Tests
# -----------------------------------------------------------------------------

def test_tenant_boundary_rejects_foreign_identifiers(resolver):
    result = resolver.resolve("INC-NS-0002", "TENANT-ORBITAL")
    assert result.entities == []
    assert result.tenant_rejected_identifiers == ["INC-NS-0002"]


def test_cross_tenant_phrase_isolation(resolver):
    # NovaStack entities cannot resolve when querying under Orbital tenant
    result = resolver.resolve("checkout outage", "TENANT-ORBITAL")
    assert result.entities == []


def test_empty_query_returns_no_entities(resolver):
    assert resolver.resolve("", _TENANT).entities == []


def test_hostile_non_catalog_query_cannot_escape_catalog_boundaries(resolver):
    result = resolver.resolve(
        "<script>alert('pwned')</script> ../../etc/passwd; DROP TABLE services; --",
        _TENANT,
    )
    assert result.entities == []
    assert result.ambiguities == []


def test_adapter_fails_closed_without_tenant_context(catalog, resolver):
    adapter = H5_1StructuredRetrieverAdapter(StructuredRetriever(catalog), resolver, catalog)
    assert adapter.extract_query_entities("checkout outage") == []


def test_runtime_resolver_has_no_evaluation_label_inputs():
    signature = inspect.signature(H5_1EntityResolver.resolve)
    assert list(signature.parameters) == ["self", "query", "tenant_id"]
    assert "expected_" not in inspect.getsource(H5_1EntityResolver)


def test_repeated_execution_is_identical(resolver):
    query = "What happened during the authentication incident?"
    first = resolver.resolve(query, _TENANT).to_dict()
    second = resolver.resolve(query, _TENANT).to_dict()
    assert first == second
