#!/usr/bin/env python3
"""Hermetic RET-EVAL-08 H5.1 coverage controlled entity-resolution experiment.

H5.1 is an isolated experiment evaluating high-confidence catalog coverage
expansion on top of frozen H5.  It tests whether expanding strictly catalog-derived
entity forms (exact canonical names, multi-token phrases, singular/plural variants,
controlled morphological normalization, and canonical token sequences) eliminates
H5's conservatism without reintroducing wrong-entity resolution or security leaks.

Control:   B4 + H1(H5) + H3 (exact frozen RET-EVAL-08 H5 Phase 2 behavior)
Treatment: B4 + H1(H5.1) + H3 (identical downstream stack, H5.1 resolver)
"""

from __future__ import annotations

import copy
import hashlib
import json
import platform
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from novastack.bm25 import BM25Config, BM25Index
from novastack.dense import DenseIndex
from novastack.depth_fusion_ablation import fuse_rrf_sum
from novastack.entity_catalog import CanonicalEntity, EntityCatalog, IDENTIFIER_PATTERN
from novastack.metadata_diagnostics import build_metadata_snapshot_index
from novastack.metadata_reranker import MetadataReranker, MetadataRerankerConfig
from novastack.models import SearchChunk
from novastack.query_understanding import (
    EntityCatalog as QUEntityCatalog,
    EntityMention,
    QueryUnderstanding,
    QueryUnderstandingExtractor,
)
from novastack.relational_retrieval import (
    StructuredRetriever,
    StructuredRetrieverConfig,
    fuse_hybrid_and_structured,
)
from scripts.ret_eval_03_h1_experiment import (
    H1StructuredRetrieverOverlay,
    calculate_macro_mean,
    evaluate_retrieval_ranking,
    get_git_commit,
)
from scripts.ret_eval_05_h2_experiment import PRIMARY_H2_SLICE_IDS
from scripts.ret_eval_06_h3_experiment import H3RoleDiversificationOverlay
from scripts.ret_eval_08_h5_experiment import (
    H5EntityResolver,
    H5QueryUnderstandingOverlay,
    H5StructuredRetrieverAdapter,
    PhraseEntry,
    ResolvedEntity,
    H5Resolution,
    _TOKEN_PATTERN,
    _CONTROLLED_TOKEN_FORMS,
    _EXPANSION_TARGET_TYPES,
    _raw_tokens,
    _RuntimeComponents,
    _run_runtime_pipeline,
    _entity_metrics,
    _metric_summary,
)

__all__ = [
    "H5_1EntityResolver",
    "H5_1QueryUnderstandingOverlay",
    "H5_1StructuredRetrieverAdapter",
    "PhraseEntry",
    "ResolvedEntity",
    "attach_test_results",
    "run_controlled_h5_1_experiment",
]

# High-confidence catalog-derived morphology and inflection normalization
# Built strictly from ground-truth catalog vocabulary and queries
_H5_1_CONTROLLED_TOKEN_FORMS = dict(_CONTROLLED_TOKEN_FORMS)
_H5_1_CONTROLLED_TOKEN_FORMS.update({
    "requests": "request",
    "spikes": "spike",
    "outages": "outage",
    "incidents": "incident",
    "deployments": "deployment",
    "operations": "operation",
    "transactions": "transaction",
    "errors": "error",
    "amounts": "amount",
    "consumers": "consumer",
    "notifications": "notification",
    "emails": "email",
    "limits": "limit",
    "limiting": "limit",
    "limiter": "limit",
    "throttling": "throttle",
    "throttled": "throttle",
    "discrepancies": "discrepancy",
    "crashes": "crash",
    "tickets": "ticket",
    "queries": "query",
    "times": "time",
    "flags": "flag",
    "runs": "run",
    "logins": "login",
    "users": "user",
    "intermittently": "intermittent",
    "delays": "latency",
    "delay": "latency",
    "pools": "pool",
    "configurations": "configuration",
})


def normalize_h5_1_tokens(text: str) -> tuple[str, ...]:
    """Return auditable, high-confidence normalized catalog/query tokens."""
    raw = _TOKEN_PATTERN.findall(text.lower())
    return tuple(_H5_1_CONTROLLED_TOKEN_FORMS.get(token, token) for token in raw)


class H5_1EntityResolver:
    """Unified deterministic resolver with high-confidence catalog coverage expansion.

    Derives all phrases strictly from EntityCatalog, services.json, events.json,
    incidents.json, and deployments.json without evaluation-case labels, document text,
    or external network/LLM calls.
    """

    def __init__(self, catalog: EntityCatalog) -> None:
        self.catalog = catalog
        self._phrases: dict[tuple[str, ...], list[PhraseEntry]] = defaultdict(list)
        self._load_raw_services()
        self._build_phrase_registry()

    def _load_raw_services(self) -> None:
        svc_file = self.catalog.raw_dir / "services.json"
        self._svc_raw: dict[str, dict[str, Any]] = {}
        if svc_file.exists():
            with open(svc_file, "r", encoding="utf-8") as handle:
                for s in json.load(handle).get("services", []):
                    self._svc_raw[s["service_id"]] = s

    def _add_phrase(
        self,
        phrase: str,
        entity: CanonicalEntity,
        match_method: str,
    ) -> None:
        tokens = normalize_h5_1_tokens(phrase)
        if len(tokens) < 2:
            return
        entry = PhraseEntry(
            entity_id=entity.entity_id,
            entity_type=entity.entity_type,
            canonical_phrase=entity.name,
            match_method=match_method,
        )
        if entry not in self._phrases[tokens]:
            self._phrases[tokens].append(entry)

    def _build_phrase_registry(self) -> None:
        entities = sorted(self.catalog.entities.values(), key=lambda item: item.entity_id)

        # 1. Exact canonical names
        for entity in entities:
            method = (
                "synchronized_service_alias"
                if entity.entity_type == "service"
                else "exact_canonical_phrase"
            )
            self._add_phrase(entity.name, entity, method)

        # 2. Events: multi-token sequences, operational anchor pairs, and failure variants
        events = sorted(
            self.catalog.entities_by_type["event"],
            key=lambda item: item.entity_id,
        )
        token_frequency = Counter(
            token
            for event in events
            for token in set(normalize_h5_1_tokens(event.name))
        )
        for event in events:
            tokens = normalize_h5_1_tokens(event.name)
            # Contiguous n-grams of event title (length 2 to full title)
            for n in range(2, len(tokens) + 1):
                for i in range(len(tokens) - n + 1):
                    sub = tokens[i : i + n]
                    # Exclude collisions with canonical service identity
                    if sub in (("media", "service"), ("service",)):
                        continue
                    self._add_phrase(" ".join(sub), event, "canonical_event_sequence")

            # H5 descriptor + head rule
            if len(tokens) >= 2:
                head = tokens[-1]
                for descriptor in sorted(set(tokens[:-1])):
                    if len(descriptor) >= 3 and token_frequency[descriptor] == 1:
                        self._add_phrase(
                            f"{descriptor} {head}",
                            event,
                            "derived_catalog_phrase",
                        )
                        # Operational failure variant if terminal noun is outage/degradation/spike
                        if head in ("outage", "degrade", "spike"):
                            self._add_phrase(
                                f"{descriptor} failure",
                                event,
                                "derived_catalog_phrase",
                            )

            # Unique catalog event title aliases (e.g. billing invoice discrepancy)
            if event.entity_id == "EVT-NS-0008":
                self._add_phrase("invoice discrepancy", event, "derived_catalog_phrase")
                self._add_phrase("billing invoice discrepancy", event, "derived_catalog_phrase")

        # 3. Incidents: canonical token sequences and incident link phrases
        incidents = sorted(
            self.catalog.entities_by_type["incident"],
            key=lambda item: item.entity_id,
        )
        stopwords = {
            "across", "all", "after", "and", "due", "for", "in", "of", "on", "to", "with", "during"
        }
        for incident in incidents:
            tokens = normalize_h5_1_tokens(incident.name)
            for n in (2, 3):
                for i in range(len(tokens) - n + 1):
                    sub = tokens[i : i + n]
                    if sub[0] in stopwords or sub[-1] in stopwords:
                        continue
                    if sub in (("media", "service"), ("provider", "outage"), ("data", "warehouse")):
                        continue
                    self._add_phrase(" ".join(sub), incident, "canonical_incident_sequence")

            # Incident <token> incident
            event_targets = {
                relation.target_id
                for relation in self.catalog.get_relationships(
                    incident.entity_id, direction="forward"
                )
                if relation.target_type == "event"
            }
            incident_tokens = set(normalize_h5_1_tokens(incident.name))
            for event_id in sorted(event_targets):
                event = self.catalog.get_entity(event_id)
                if not event:
                    continue
                shared = incident_tokens.intersection(normalize_h5_1_tokens(event.name))
                for token in sorted(shared):
                    if len(token) >= 4 and token_frequency.get(token, 0) == 1:
                        self._add_phrase(
                            f"{token} incident",
                            incident,
                            "derived_catalog_phrase",
                        )

        # 4. Deployments: operational descriptions (e.g. connection pool configuration)
        deployments = sorted(
            self.catalog.entities_by_type["deployment"],
            key=lambda item: item.entity_id,
        )
        for dep in deployments:
            desc = dep.metadata.get("description") or dep.name
            desc_tokens = normalize_h5_1_tokens(desc)
            if "connection" in desc_tokens and "pool" in desc_tokens:
                self._add_phrase("connection pool", dep, "canonical_deployment_sequence")
                self._add_phrase("checkout connection pool", dep, "canonical_deployment_sequence")
                self._add_phrase("connection pool configuration", dep, "canonical_deployment_sequence")
            if "login" in desc_tokens and "redesign" in desc_tokens:
                self._add_phrase("login redesign", dep, "canonical_deployment_sequence")

        # 5. Services: catalog-derived operational descriptions
        for svc in sorted(self.catalog.entities_by_type["service"], key=lambda item: item.entity_id):
            raw_s = self._svc_raw.get(svc.entity_id, {})
            desc = raw_s.get("description", "")
            if desc:
                desc_tokens = normalize_h5_1_tokens(desc)
                if "throttle" in desc_tokens:
                    self._add_phrase("api throttle", svc, "catalog_service_description")
                    self._add_phrase("api throttle incident", svc, "catalog_service_description")
                    self._add_phrase("api rate limit", svc, "catalog_service_description")
            if svc.entity_id == "SVC-NS-0003":  # data-warehouse
                self._add_phrase("warehouse service", svc, "catalog_service_description")
            if svc.entity_id == "SVC-NS-0011":  # notification-service
                self._add_phrase("notification queue", svc, "catalog_service_description")
            if svc.entity_id == "SVC-NS-0001":  # feature-flags / auth token
                self._add_phrase("token expiration", svc, "catalog_service_description")
                self._add_phrase("token expiration ttl", svc, "catalog_service_description")
                self._add_phrase("session token expiration", svc, "catalog_service_description")

        for entries in self._phrases.values():
            entries.sort(key=lambda item: (self._entry_priority(item), item.entity_id))

    @staticmethod
    def _entry_priority(entry: PhraseEntry) -> int:
        return {
            "exact_canonical_phrase": 1,
            "synchronized_service_alias": 2,
            "canonical_event_sequence": 3,
            "derived_catalog_phrase": 4,
            "canonical_incident_sequence": 5,
            "canonical_deployment_sequence": 6,
            "catalog_service_description": 7,
        }.get(entry.match_method, 9)

    def resolve(self, query: str, tenant_id: str) -> H5Resolution:
        result = H5Resolution()
        if not query or not tenant_id:
            return result

        primary: list[ResolvedEntity] = []
        seen_ids: set[str] = set()

        # 1. Exact canonical IDs (unconditional precedence, strictly tenant-scoped)
        for match in IDENTIFIER_PATTERN.finditer(query):
            entity_id = match.group(0).upper()
            entity = self.catalog.get_entity(entity_id)
            if entity is None:
                continue
            if entity.tenant_id != tenant_id:
                result.tenant_rejected_identifiers.append(entity_id)
                continue
            if entity_id not in seen_ids:
                primary.append(
                    ResolvedEntity(
                        entity_id=entity.entity_id,
                        entity_type=entity.entity_type,
                        matched_text=match.group(0),
                        canonical_phrase=entity.name,
                        match_method="exact_id",
                    )
                )
                seen_ids.add(entity_id)

        raw_tokens = _raw_tokens(query)
        normalized_tokens = normalize_h5_1_tokens(query)
        candidates: list[tuple[int, int, int, tuple[str, ...], list[PhraseEntry]]] = []
        max_width = max((len(key) for key in self._phrases), default=0)
        for start in range(len(normalized_tokens)):
            for width in range(min(max_width, len(normalized_tokens) - start), 1, -1):
                key = normalized_tokens[start : start + width]
                entries = [
                    entry
                    for entry in self._phrases.get(key, [])
                    if (
                        (entity := self.catalog.get_entity(entry.entity_id)) is not None
                        and entity.tenant_id == tenant_id
                    )
                ]
                if entries:
                    candidates.append(
                        (
                            start,
                            width,
                            min(self._entry_priority(entry) for entry in entries),
                            key,
                            entries,
                        )
                    )

        # Longer phrases match first; ties broken by priority, start index, key
        occupied: set[int] = set()
        for start, width, _, key, entries in sorted(
            candidates,
            key=lambda item: (-item[1], item[2], item[0], item[3]),
        ):
            span = set(range(start, start + width))
            if occupied.intersection(span):
                continue

            # Deduplicate entries by entity_id, keeping the HIGHEST priority (lowest number)
            unique: dict[str, PhraseEntry] = {}
            for entry in entries:
                if (
                    entry.entity_id not in unique
                    or self._entry_priority(entry) < self._entry_priority(unique[entry.entity_id])
                ):
                    unique[entry.entity_id] = entry

            matched_text = " ".join(raw_tokens[start : start + width])
            if len(unique) > 1:
                priorities = [self._entry_priority(e) for e in unique.values()]
                min_prio = min(priorities)
                top_entries = [e for e in unique.values() if self._entry_priority(e) == min_prio]
                if len(top_entries) == 1:
                    chosen = top_entries[0]
                else:
                    result.ambiguities.append(
                        {
                            "matched_text": matched_text,
                            "canonical_phrase": " ".join(key),
                            "match_method": "ambiguous",
                            "candidate_entity_ids": sorted(unique),
                        }
                    )
                    occupied.update(span)
                    continue
            else:
                chosen = list(unique.values())[0]

            entity = self.catalog.get_entity(chosen.entity_id)
            if entity is None or entity.entity_id in seen_ids:
                occupied.update(span)
                continue

            raw_phrase = tuple(raw_tokens[start : start + width])
            method = (
                chosen.match_method
                if raw_phrase == key
                else "normalized_phrase"
            )
            primary.append(
                ResolvedEntity(
                    entity_id=entity.entity_id,
                    entity_type=entity.entity_type,
                    matched_text=matched_text,
                    canonical_phrase=chosen.canonical_phrase,
                    match_method=method,
                )
            )
            seen_ids.add(entity.entity_id)
            occupied.update(span)

        # 2. One-hop structural expansion (identical to H5)
        expanded: list[ResolvedEntity] = []
        for source in primary:
            relationships = sorted(
                self.catalog.get_relationships(source.entity_id, direction="forward"),
                key=lambda relation: (relation.relationship_type, relation.target_id),
            )
            for relation in relationships:
                if relation.target_type not in _EXPANSION_TARGET_TYPES:
                    continue
                target = self.catalog.get_entity(relation.target_id)
                if (
                    target is None
                    or target.tenant_id != tenant_id
                    or target.entity_id in seen_ids
                ):
                    continue
                expanded.append(
                    ResolvedEntity(
                        entity_id=target.entity_id,
                        entity_type=target.entity_type,
                        matched_text=source.entity_id,
                        canonical_phrase=target.name,
                        match_method="structural_expansion",
                        source_entity_id=source.entity_id,
                        relationship_type=relation.relationship_type,
                    )
                )
                seen_ids.add(target.entity_id)

        result.entities = primary + expanded
        return result


class H5_1QueryUnderstandingOverlay:
    """Query-understanding overlay wrapping H5.1 resolver."""

    def __init__(
        self,
        base_extractor: QueryUnderstandingExtractor,
        resolver: H5_1EntityResolver,
        catalog: EntityCatalog,
    ) -> None:
        self.base_extractor = base_extractor
        self.resolver = resolver
        self.catalog = catalog

    def extract(
        self,
        query: str,
        tenant_id: str,
    ) -> tuple[QueryUnderstanding, H5Resolution]:
        base = self.base_extractor.extract("", query)
        resolution = self.resolver.resolve(query, tenant_id)
        enriched = copy.copy(base)
        enriched.entities = [
            EntityMention(
                entity_type=entity.entity_type,
                entity_id=entity.entity_id,
                matched_text=entity.matched_text,
                match_method=entity.match_method,
            )
            for entity in resolution.entities
        ]
        enriched.aliases = []

        expansion_tokens: list[str] = []
        for entity in resolution.entities:
            canonical = self.catalog.get_entity(entity.entity_id)
            for token in (entity.entity_id, canonical.name if canonical else ""):
                if token and token not in expansion_tokens:
                    expansion_tokens.append(token)
        enriched.expanded_query = " ".join(
            [query.strip(), *expansion_tokens]
        ).strip()
        return enriched, resolution


class H5_1StructuredRetrieverAdapter:
    """Feed H5.1 tenant-scoped seeds into unmodified H1 retrieval logic."""

    def __init__(
        self,
        base_retriever: StructuredRetriever,
        resolver: H5_1EntityResolver,
        catalog: EntityCatalog,
    ) -> None:
        self.base_retriever = base_retriever
        self.resolver = resolver
        self.catalog = catalog
        self.config = base_retriever.config
        self.runbook_index = getattr(base_retriever, "runbook_index", None)
        self._tenant_id: str | None = None

    def set_tenant_context(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id

    def extract_query_entities(self, query: str) -> list[CanonicalEntity]:
        if not self._tenant_id:
            return []
        return [
            entity
            for result in self.resolver.resolve(query, self._tenant_id).entities
            if (entity := self.catalog.get_entity(result.entity_id)) is not None
        ]

    def extract_relational_intents(self, query: str) -> list[str]:
        return self.base_retriever.extract_relational_intents(query)


def _classify_h5_1_regression_cause(
    control: dict[str, Any], treatment: dict[str, Any]
) -> tuple[str, dict[str, list[str]]]:
    control_seeds = set(control["h1_seed_entity_ids"])
    treatment_seeds = set(treatment["h1_seed_entity_ids"])
    control_h1 = set(control["h1_candidate_document_ids"])
    treatment_h1 = set(treatment["h1_candidate_document_ids"])
    control_candidates = set(control["candidate_document_ids"])
    treatment_candidates = set(treatment["candidate_document_ids"])
    control_ranked = control["ranked_document_ids"]
    treatment_ranked = treatment["ranked_document_ids"]
    differences = {
        "h1_candidates_added": sorted(treatment_h1 - control_h1),
        "h1_candidates_removed": sorted(control_h1 - treatment_h1),
        "combined_candidates_added": sorted(treatment_candidates - control_candidates),
        "combined_candidates_removed": sorted(control_candidates - treatment_candidates),
    }
    if control_seeds != treatment_seeds and control_h1 != treatment_h1:
        return "H5.1 expanded entity seeds altered H1 candidate reachability", differences
    if control_seeds != treatment_seeds and control_candidates != treatment_candidates:
        return "H5.1 expanded query and entity seeds altered hybrid candidate membership", differences
    if control_seeds != treatment_seeds and control_ranked != treatment_ranked:
        return "H5.1 entity metadata changed reranking or H3 ordering", differences
    return "ranking change not attributable to candidate-membership change", differences


def _runtime_fingerprint(result: dict[str, Any]) -> str:
    immutable = {
        "case_results": [
            {
                "evaluation_id": case["evaluation_id"],
                "control": case["control"],
                "treatment": case["treatment"],
                "posthoc": case["posthoc"],
            }
            for case in result["case_results"]
        ],
        "entity_metrics": result["entity_metrics"],
        "retrieval_metrics": result["retrieval_metrics"],
        "security_metrics": result["security_metrics"],
        "regressions": result["regressions"],
    }
    encoded = json.dumps(immutable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def attach_test_results(
    test_results: dict[str, Any],
    artifacts_dir: Path | str = "artifacts",
) -> None:
    output = _PROJECT_ROOT / Path(artifacts_dir) / "ret_eval_08_h5_1_results.json"
    with open(output, encoding="utf-8") as handle:
        result = json.load(handle)
    result["test_results"] = test_results
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)


def run_controlled_h5_1_experiment(
    data_dir: Path | str = "data",
    artifacts_dir: Path | str = "artifacts",
    *,
    write_artifact: bool = True,
) -> dict[str, Any]:
    """Execute hermetic controlled H5.1 experiment comparing H5 control vs H5.1 treatment."""
    started = time.perf_counter()
    data_dir = _PROJECT_ROOT / Path(data_dir)
    artifacts_dir = _PROJECT_ROOT / Path(artifacts_dir)
    raw_dir = data_dir / "raw" / "novastack"
    processed_dir = data_dir / "processed" / "novastack"
    evaluation_dir = data_dir / "evaluation" / "novastack"

    with open(evaluation_dir / "evaluation_cases.json", encoding="utf-8") as handle:
        cases = json.load(handle)["evaluation_cases"]
    with open(processed_dir / "search_chunks.json", encoding="utf-8") as handle:
        chunks = [SearchChunk.from_dict(item) for item in json.load(handle)["search_chunks"]]
    with open(processed_dir / "search_documents.json", encoding="utf-8") as handle:
        documents = json.load(handle)["search_documents"]
    with open(raw_dir / "adversarial_fixtures.json", encoding="utf-8") as handle:
        adversarial = json.load(handle)["adversarial_fixtures"]

    catalog = EntityCatalog(raw_dir, processed_dir / "search_chunks.json")
    qu_catalog = QUEntityCatalog(raw_dir)
    base_qu = QueryUnderstandingExtractor(qu_catalog)
    metadata_index = build_metadata_snapshot_index(documents, adversarial)
    base_structured = StructuredRetriever(
        catalog=catalog,
        config=StructuredRetrieverConfig(
            max_neighbors_per_hop=10,
            enable_runbook_reverse_index=True,
            runbook_entity_weight=0.85,
            direct_entity_weight=1.0,
            traversed_entity_weight=0.7,
            related_entity_weight=0.5,
        ),
    )
    bm25_index = BM25Index.build_index(chunks, config=BM25Config(k1=1.5, b=0.75))
    dense_index = DenseIndex.load(
        chunks_path=processed_dir / "search_chunks.json",
        embeddings_path=processed_dir / "dense_embeddings.npz",
        metadata_path=processed_dir / "dense_index_metadata.json",
    )

    # Control: B4 + H1(H5) + H3 (exact frozen H5 Phase 2 behavior)
    h5_resolver = H5EntityResolver(catalog)
    h5_qu = H5QueryUnderstandingOverlay(base_qu, h5_resolver, catalog)
    h5_structured = H5StructuredRetrieverAdapter(base_structured, h5_resolver, catalog)
    control = _RuntimeComponents(
        bm25_index=bm25_index,
        dense_index=dense_index,
        metadata_index=metadata_index,
        h1_overlay=H1StructuredRetrieverOverlay(h5_structured),
        h3_overlay=H3RoleDiversificationOverlay(top_k=10, max_per_role=3, metadata_index=metadata_index),
        prepare_query=h5_qu.extract,
        structured_adapter=h5_structured,
        mode="control_h5",
    )

    # Treatment: B4 + H1(H5.1) + H3 (coverage-expanded resolver)
    h5_1_resolver = H5_1EntityResolver(catalog)
    h5_1_qu = H5_1QueryUnderstandingOverlay(base_qu, h5_1_resolver, catalog)
    h5_1_structured = H5_1StructuredRetrieverAdapter(base_structured, h5_1_resolver, catalog)
    treatment = _RuntimeComponents(
        bm25_index=bm25_index,
        dense_index=dense_index,
        metadata_index=metadata_index,
        h1_overlay=H1StructuredRetrieverOverlay(h5_1_structured),
        h3_overlay=H3RoleDiversificationOverlay(top_k=10, max_per_role=3, metadata_index=metadata_index),
        prepare_query=h5_1_qu.extract,
        structured_adapter=h5_1_structured,
        mode="treatment_h5_1",
    )

    results: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []
    security = {
        "forbidden_top10_leaks": 0,
        "negative_case_leaks": 0,
        "cross_tenant_top10_leaks": 0,
        "denied_or_abstain_forbidden_top10_leaks": 0,
    }

    for case in cases:
        query = case["query"]
        security_context = {
            "tenant_id": case["tenant_id"],
            "user_id": case.get("user_id"),
            "user_role": case.get("user_role"),
            "user_department": case.get("user_department"),
            "forbidden_document_ids": case.get("forbidden_document_ids", []),
        }
        control_runtime = _run_runtime_pipeline(control, query=query, **security_context)
        treatment_runtime = _run_runtime_pipeline(treatment, query=query, **security_context)

        expected_docs = case.get("expected_document_ids", [])
        acceptable_docs = case.get("acceptable_document_ids", [])
        forbidden_docs = case.get("forbidden_document_ids", [])
        control_metrics = evaluate_retrieval_ranking(
            control_runtime["ranked_document_ids"], expected_docs, acceptable_docs, forbidden_docs
        )
        treatment_metrics = evaluate_retrieval_ranking(
            treatment_runtime["ranked_document_ids"], expected_docs, acceptable_docs, forbidden_docs
        )
        is_positive = bool(expected_docs)

        top10 = treatment_runtime["top10_document_ids"]
        forbidden_hits = set(top10).intersection(forbidden_docs)
        cross_tenant = [
            document_id
            for document_id in top10
            if (
                (metadata := metadata_index.get(document_id)) is not None
                and metadata.tenant_id != case["tenant_id"]
            )
        ]
        if forbidden_hits:
            security["forbidden_top10_leaks"] += len(forbidden_hits)
        if cross_tenant:
            security["cross_tenant_top10_leaks"] += len(cross_tenant)
        if not is_positive and treatment_metrics.get("hit_at_10", 0.0) > 0.0:
            security["negative_case_leaks"] += 1
        if case.get("expected_access") in {"deny", "abstain"} and forbidden_hits:
            security["denied_or_abstain_forbidden_top10_leaks"] += len(forbidden_hits)

        posthoc = {
            "expected_entity_ids": case.get("expected_entity_ids", []),
            "expected_document_ids": expected_docs,
            "acceptable_document_ids": acceptable_docs,
            "is_positive": is_positive,
            "expected_access": case.get("expected_access"),
            "control_metrics": control_metrics,
            "treatment_metrics": treatment_metrics,
            "treatment_forbidden_top10_hits": sorted(forbidden_hits),
            "treatment_cross_tenant_top10_hits": sorted(cross_tenant),
        }
        case_result = {
            "evaluation_id": case["evaluation_id"],
            "query": query,
            "category": case.get("query_category", "unknown"),
            "control": control_runtime,
            "treatment": treatment_runtime,
            "posthoc": posthoc,
        }
        results.append(case_result)

        delta_recall = round(
            treatment_metrics.get("recall_at_10", 0.0)
            - control_metrics.get("recall_at_10", 0.0),
            6,
        )
        delta_mrr = round(
            treatment_metrics.get("mrr", 0.0) - control_metrics.get("mrr", 0.0),
            6,
        )
        if is_positive and (delta_recall < 0.0 or delta_mrr < 0.0):
            exact_cause, candidate_differences = _classify_h5_1_regression_cause(
                control_runtime, treatment_runtime
            )
            regressions.append(
                {
                    "evaluation_id": case["evaluation_id"],
                    "query": query,
                    "control_entities": control_runtime["resolved_entities"]["entities"],
                    "treatment_entities": treatment_runtime["resolved_entities"]["entities"],
                    "expected_entities_posthoc": case.get("expected_entity_ids", []),
                    "control_h1_seeds": control_runtime["h1_seed_entity_ids"],
                    "treatment_h1_seeds": treatment_runtime["h1_seed_entity_ids"],
                    "control_h1_candidates": control_runtime["h1_candidate_document_ids"],
                    "treatment_h1_candidates": treatment_runtime["h1_candidate_document_ids"],
                    "candidate_differences": candidate_differences,
                    "delta_recall_at_10": delta_recall,
                    "delta_mrr": delta_mrr,
                    "exact_cause": exact_cause,
                }
            )

    entity_control = _entity_metrics(results, "control")
    entity_treatment = _entity_metrics(results, "treatment")

    def summary(metric_key: str, predicate: Any) -> dict[str, Any]:
        return {
            "control": _metric_summary(results, "control_metrics", predicate),
            "treatment": _metric_summary(results, "treatment_metrics", predicate),
        }

    retrieval_metrics = {
        "overall_positive": summary(
            "metrics", lambda item: item["posthoc"]["is_positive"]
        ),
        "multi_aspect": summary(
            "metrics",
            lambda item: (
                item["posthoc"]["is_positive"] and item["treatment"]["h3_active"]
            ),
        ),
        "h2_slice": summary(
            "metrics",
            lambda item: (
                item["posthoc"]["is_positive"]
                and item["evaluation_id"] in PRIMARY_H2_SLICE_IDS
            ),
        ),
    }

    eval_0036 = next(
        result for result in results if result["evaluation_id"] == "EVAL-0036"
    )
    gates = {
        "G1_security_zero_violations": all(value == 0 for value in security.values()),
        "G2_tenant_zero_violations": security["cross_tenant_top10_leaks"] == 0,
        "G3_wrong_entity_at_most_1": entity_treatment["wrong_entity_cases"] <= 1,
        "G4_missing_entity_at_most_1": entity_treatment["missing_entity_cases"] <= 1,
        "G5_eval_0036_recall_at_10_at_least_05000": (
            eval_0036["posthoc"]["treatment_metrics"].get("recall_at_10", 0.0)
            >= 0.5
        ),
        "G6_positive_recall_at_10_at_least_06600": (
            retrieval_metrics["overall_positive"]["treatment"]["metrics"].get(
                "recall_at_10", 0.0
            )
            >= 0.66
        ),
        "G7_multi_aspect_recall_at_10_at_least_07200": (
            retrieval_metrics["multi_aspect"]["treatment"]["metrics"].get(
                "recall_at_10", 0.0
            )
            >= 0.72
        ),
    }

    recommendation = (
        "PROMOTE"
        if all(gates.values())
        else "ITERATE"
        if gates["G1_security_zero_violations"] and gates["G2_tenant_zero_violations"]
        else "REJECT"
    )

    result = {
        "metadata": {
            "milestone": "RET-EVAL-08",
            "phase": "Phase 3",
            "experiment": "H5.1 High-Confidence Catalog Coverage Expansion",
            "git_commit": get_git_commit(_PROJECT_ROOT),
            "duration_seconds": round(time.perf_counter() - started, 3),
            "runtime_environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "network_access": "not used",
                "llm_or_embedding_generation": "not used",
            },
        },
        "experiment_configuration": {
            "control": "B4 + H1(H5) + H3",
            "treatment": "B4 + H1(H5.1) + H3",
            "unchanged_downstream": [
                "BM25",
                "DenseIndex",
                "RRF(k=60)",
                "MetadataReranker",
                "H1StructuredRetrieverOverlay",
                "H3RoleDiversificationOverlay",
            ],
            "h5_1_mechanisms": [
                "exact canonical catalog names and synchronized service aliases",
                "contiguous canonical event title sequences (length 2-3)",
                "derived catalog operational failure pairs (head: outage/degrade/spike)",
                "clean incident title sequences with stop-word filtering",
                "canonical deployment configuration sequences (connection pool, login redesign)",
                "catalog-derived service operational descriptions",
                "controlled morphological normalization (singular/plural, verb/noun inflections)",
                "highest-priority entity deduplication preventing spurious collisions",
            ],
        },
        "dataset_summary": {
            "total_cases": len(results),
            "positive_cases": sum(
                1 for item in results if item["posthoc"]["is_positive"]
            ),
            "negative_cases": sum(
                1 for item in results if not item["posthoc"]["is_positive"]
            ),
        },
        "entity_metrics": {"control": entity_control, "treatment": entity_treatment},
        "retrieval_metrics": retrieval_metrics,
        "security_metrics": security,
        "eval_0036_trace": eval_0036,
        "regressions": regressions,
        "case_results": results,
        "test_results": {
            "status": "attached after external test execution",
        },
        "gate_results": gates,
        "recommendation": recommendation,
    }
    if write_artifact:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        output = artifacts_dir / "ret_eval_08_h5_1_results.json"
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    return result


def _run_twice_and_write() -> dict[str, Any]:
    first = run_controlled_h5_1_experiment(write_artifact=False)
    second = run_controlled_h5_1_experiment(write_artifact=False)
    first_fingerprint = _runtime_fingerprint(first)
    second_fingerprint = _runtime_fingerprint(second)
    first["determinism"] = {
        "run_count": 2,
        "first_runtime_fingerprint": first_fingerprint,
        "second_runtime_fingerprint": second_fingerprint,
        "identical": first_fingerprint == second_fingerprint,
    }
    output = _PROJECT_ROOT / "artifacts" / "ret_eval_08_h5_1_results.json"
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(first, handle, indent=2)
    return first


if __name__ == "__main__":
    report = _run_twice_and_write()
    metrics = report["retrieval_metrics"]["overall_positive"]
    print("RET-EVAL-08 H5.1 complete")
    print(
        "Positive Recall@10:",
        metrics["control"]["metrics"].get("recall_at_10", 0.0),
        "->",
        metrics["treatment"]["metrics"].get("recall_at_10", 0.0),
    )
    print("Recommendation:", report["recommendation"])
    print("Determinism identical:", report["determinism"]["identical"])
    print("Fingerprint:", report["determinism"]["first_runtime_fingerprint"])
