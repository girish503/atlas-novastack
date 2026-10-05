#!/usr/bin/env python3
"""Generate the NovaStack synthetic enterprise dataset and source corpus.

Writes separate JSON files to data/raw/novastack/:
    Organisational:   users.json, teams.json, customers.json, services.json
    Event Truth:      events.json, incidents.json, deployments.json,
                      pull_requests.json, event_relationships.json
    Source Corpus:    source_records.json

Usage:
    python scripts/generate.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Ensure the src/ directory is importable when running as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from novastack.adversarial_corpus import AdversarialCorpusGenerator
from novastack.background_corpus import BackgroundCorpusGenerator
from novastack.config import DATASET_VERSION, RANDOM_SEED
from novastack.corpus_generator import EventSourceCorpusGenerator
from novastack.generator import NovaStackGenerator
from novastack.eval_generator import (
    EvaluationDatasetGenerator,
    build_evaluation_taxonomy,
    validate_evaluation_cases,
)
from novastack.noise_generator import TemporalNoiseGenerator
from novastack.rendering import validate_source_records
from novastack.security_corpus import SecurityCorpusGenerator


def main() -> None:
    print(f"NovaStack Generator v{DATASET_VERSION}  (seed={RANDOM_SEED})")
    print("=" * 60)

    # 1. Generate Organisational & Ground-Truth Layers
    gen = NovaStackGenerator()
    data = gen.generate()

    # Validate ground-truth consistency.
    gt_errors = gen.validate()
    if gt_errors:
        print(f"\n[FAIL] {len(gt_errors)} ground-truth consistency error(s):")
        for e in gt_errors:
            print(f"  - {e}")
        sys.exit(1)
    print("[OK] All ground-truth consistency checks passed.")

    # 2. Generate Observational Source Corpus (Milestones 4A, 4B, 4C, 4D-1 & 4D-2)
    event_gen = EventSourceCorpusGenerator(gen, seed=RANDOM_SEED)
    event_records = event_gen.generate_corpus()

    bkg_gen = BackgroundCorpusGenerator(gen, seed=RANDOM_SEED)
    bkg_records = bkg_gen.generate_corpus()

    clean_records = event_records + bkg_records

    noise_gen = TemporalNoiseGenerator(gen, clean_records, seed=RANDOM_SEED)
    noise_records = noise_gen.generate_noise_records()

    sec_gen = SecurityCorpusGenerator(gen, seed=RANDOM_SEED)
    sec_records = sec_gen.generate_corpus()
    sec_fixtures = sec_gen.generate_fixtures()

    adv_gen = AdversarialCorpusGenerator(gen, seed=RANDOM_SEED)
    adv_records = adv_gen.generate_corpus()
    adv_fixtures = adv_gen.generate_fixtures()

    source_records = clean_records + noise_records + sec_records + adv_records

    # Validate source records.
    all_entity_ids = set()
    all_entity_ids.update(u.user_id for u in gen.users)
    all_entity_ids.update(t.team_id for t in gen.teams)
    all_entity_ids.update(s.service_id for s in gen.services)
    all_entity_ids.update(c.customer_id for c in gen.customers)
    all_entity_ids.update(e.event_id for e in gen.events)
    all_entity_ids.update(i.incident_id for i in gen.incidents)
    all_entity_ids.update(d.deployment_id for d in gen.deployments)
    all_entity_ids.update(p.pull_request_id for p in gen.pull_requests)

    valid_tenants = {"TENANT-NOVASTACK", "TENANT-ORBITAL", "TENANT-PINECONE"}

    src_errors = validate_source_records(
        source_records, all_entity_ids, valid_tenants
    )
    if src_errors:
        print(f"\n[FAIL] {len(src_errors)} source-corpus validation error(s):")
        for e in src_errors:
            print(f"  - {e}")
        sys.exit(1)
    print("[OK] All source-corpus consistency checks passed.")

    # 3. Generate Evaluation Dataset (Milestone 4E-1)
    eval_gen = EvaluationDatasetGenerator(
        generator=gen,
        source_records=source_records,
        security_fixtures=sec_fixtures,
        adversarial_fixtures=adv_fixtures,
        seed=RANDOM_SEED,
    )
    eval_cases = eval_gen.generate_dataset()
    eval_errors = validate_evaluation_cases(
        eval_cases,
        source_records,
        gen,
        sec_fixtures,
        adv_fixtures,
    )
    if eval_errors:
        print(f"\n[FAIL] {len(eval_errors)} evaluation dataset validation error(s):")
        for e in eval_errors:
            print(f"  - {e}")
        sys.exit(1)
    print("[OK] All evaluation dataset consistency checks passed.")


    # Print entity counts.
    print("\n--- Organisational Entities (Milestone 1) ---")
    print(f"  Users:               {len(data['users'])}")
    print(f"  Teams:               {len(data['teams'])}")
    print(f"  Customers:           {len(data['customers'])}")
    print(f"  Services:            {len(data['services'])}")

    print("\n--- Ground-Truth Event Layer (Milestone 2) ---")
    print(f"  Events:              {len(data['events'])}")
    print(f"  Incidents:           {len(data['incidents'])}")
    print(f"  Deployments:         {len(data['deployments'])}")
    print(f"  Pull Requests:       {len(data['pull_requests'])}")
    print(f"  Event Relationships: {len(data['event_relationships'])}")

    print("\n--- Observational Source Corpus (Milestones 4A, 4B, 4C, 4D-1 & 4D-2) ---")
    print(f"  Total Combined Records: {len(source_records)}")
    print(f"    - Event-Related Records (4A):    {len(event_records)}")
    print(f"    - Background Records (4B):       {len(bkg_records)}")
    print(f"    - Controlled Noise Records (4C): {len(noise_records)}")
    print(f"    - Security Test Records (4D-1):  {len(sec_records)}")
    print(f"    - Adversarial Records (4D-2):    {len(adv_records)}")

    print("\n  Controlled Noise Breakdown (Milestone 4C):")
    print(f"    Near Duplicates:               {noise_gen.near_duplicate_count:>4}")
    print(f"    Stale Records:                 {noise_gen.stale_record_count:>4}")
    print(f"    Version Chains:                {noise_gen.version_chain_count:>4}")
    print(f"    Superseded Standalone:         {noise_gen.superseded_count:>4}")
    print(f"    Draft Records:                 {noise_gen.draft_count:>4}")
    print(f"    Conflicting Observations:      {noise_gen.conflicting_count:>4}")
    print(f"    Corrected Observations:        {noise_gen.correction_count:>4}")

    print("\n  Security Scenario Breakdown (Milestone 4D-1):")
    print(f"    Cross-Tenant Isolation:        {sec_gen.cross_tenant_count:>4}")
    print(f"    Role-Based Access:             {sec_gen.role_based_count:>4}")
    print(f"    Classification Boundaries:     {sec_gen.classification_count:>4}")
    print(f"    Department Restrictions:       {sec_gen.department_count:>4}")
    print(f"    Document-Level Permissions:    {sec_gen.document_permission_count:>4}")
    print(f"    Version-Specific ACLs:         {sec_gen.version_specific_count:>4}")
    print(f"    Duplicate ACL Differentiation: {sec_gen.duplicate_acl_count:>4}")
    print(f"    Superseded Restricted:         {sec_gen.superseded_restricted_count:>4}")

    print(f"\n  Security Ground-Truth Fixtures:  {len(sec_fixtures):>4}")
    fix_scenario_counts = Counter(f.security_scenario for f in sec_fixtures)
    for sc, cnt in sorted(fix_scenario_counts.items()):
        print(f"    - {sc:<30} {cnt:>3}")
    fix_access_counts = Counter(f.expected_access for f in sec_fixtures)
    print(f"    Expected Access Breakdown:     ALLOW: {fix_access_counts.get('allow', 0)}, DENY: {fix_access_counts.get('deny', 0)}")

    print("\n  Adversarial Breakdown (Milestone 4D-2):")
    print(f"    Direct Instructions:           {adv_gen.direct_instruction_count:>4}")
    print(f"    Indirect Prompt Injections:    {adv_gen.indirect_prompt_injection_count:>4}")
    print(f"    Retrieval Poisoning:           {adv_gen.retrieval_poisoning_count:>4}")
    print(f"    Evidence Manipulation:         {adv_gen.evidence_manipulation_count:>4}")
    print(f"    Instruction/Data Confusion:    {adv_gen.instruction_data_confusion_count:>4}")
    print(f"    Citation Manipulation:         {adv_gen.citation_manipulation_count:>4}")
    print(f"    Hidden/Obfuscated Injections:  {adv_gen.hidden_obfuscated_count:>4}")
    print(f"    Cross-Tenant Adversarial:      {adv_gen.cross_tenant_adversarial_count:>4}")
    print(f"    Total Poisoned Records:        {adv_gen.poisoned_count:>4}")
    print(f"    Total Non-Poisoned Records:    {adv_gen.non_poisoned_count:>4}")

    print(f"\n  Adversarial Ground-Truth Fixtures: {len(adv_fixtures):>4}")
    adv_cat_counts = Counter(f.attack_category for f in adv_fixtures)
    for cat, cnt in sorted(adv_cat_counts.items()):
        print(f"    - {cat:<30} {cnt:>3}")
    adv_beh_counts = Counter(f.expected_behavior for f in adv_fixtures)
    print("    Expected Behavior Breakdown:")
    for beh, cnt in sorted(adv_beh_counts.items()):
        print(f"      * {beh:<35} {cnt:>2}")

    # Breakdown by lifecycle status
    status_counts = Counter(r.status for r in source_records)
    print("\n  Records by Lifecycle Status:")
    for st, count in sorted(status_counts.items()):
        print(f"    {st:<25} {count:>4}")

    # Breakdown by department
    dept_counts = Counter(r.department for r in source_records)
    print("\n  Records by Department:")
    for dept, count in sorted(dept_counts.items()):
        print(f"    {dept:<25} {count:>4}")

    # Breakdown by source type
    type_counts = Counter(r.source_type for r in source_records)
    print("\n  Records by Source Type:")
    for st, count in sorted(type_counts.items()):
        print(f"    {st:<25} {count:>4}")

    # Breakdown by tenant
    tenant_counts_src = Counter(r.tenant_id for r in source_records)
    print("\n  Records by Tenant:")
    for tid, count in sorted(tenant_counts_src.items()):
        print(f"    {tid:<25} {count:>4}")

    # Breakdown by authority level
    auth_counts = Counter(r.authority_level for r in source_records)
    print("\n  Records by Authority Level:")
    for auth, count in sorted(auth_counts.items()):
        print(f"    {auth:<25} {count:>4}")

    # Breakdown by classification
    class_counts = Counter(r.classification for r in source_records)
    print("\n  Records by Classification:")
    for cls, count in sorted(class_counts.items()):
        print(f"    {cls:<25} {count:>4}")

    # Event provenance breakdown
    records_with_event_prov = 0
    records_without_event_prov = 0
    for r in source_records:
        has_ev = bool(
            (r.source_entity_id and r.source_entity_id.startswith("EVT-"))
            or any(rel.startswith("EVT-") for rel in r.related_entity_ids)
        )
        if has_ev:
            records_with_event_prov += 1
        else:
            records_without_event_prov += 1

    print("\n  Provenance Distribution:")
    print(f"    With Event Provenance:    {records_with_event_prov:>4} "
          f"({(records_with_event_prov/len(source_records))*100:.1f}%)")
    print(f"    Without Event Provenance: {records_without_event_prov:>4} "
          f"({(records_without_event_prov/len(source_records))*100:.1f}%)")
    print(f"    Validation Errors:        0")
    print(f"    Validation Warnings:      0")

    # Evaluation Dataset breakdown (Milestone 4E-1)
    print("\n--- Evaluation Dataset (Milestone 4E-1) ---")
    print(f"  Total Evaluation Cases:        {len(eval_cases):>4}")
    eval_diff_counts = Counter(c.difficulty for c in eval_cases)
    print("  Cases by Difficulty:")
    for diff in ("easy", "medium", "hard"):
        print(f"    - {diff:<10}                   {eval_diff_counts.get(diff, 0):>4}")
    eval_cat_counts = Counter(c.query_category for c in eval_cases)
    print(f"  Query Categories Covered:      {len(eval_cat_counts):>4} / 20")
    eval_access_counts = Counter(c.expected_access for c in eval_cases)
    print(f"  Expected Access Distribution:  ALLOW: {eval_access_counts.get('allow', 0)}, DENY: {eval_access_counts.get('deny', 0)}")
    eval_sec_linked = sum(1 for c in eval_cases if c.security_fixture_id)
    eval_adv_linked = sum(1 for c in eval_cases if c.adversarial_fixture_id)
    print(f"  Security Fixtures Linked:      {eval_sec_linked:>4}")
    print(f"  Adversarial Fixtures Linked:   {eval_adv_linked:>4}")
    print(f"  Evaluation Validation Errors:     0")
    print(f"  Evaluation Validation Warnings:   0")


    # Per-tenant breakdown for org entities.
    print("\n--- Organisational Entities by Tenant ---")
    for entity_type in ("users", "teams", "customers", "services"):
        tenant_counts: dict[str, int] = {}
        for record in data[entity_type]:
            tid = record["tenant_id"]
            tenant_counts[tid] = tenant_counts.get(tid, 0) + 1
        counts_str = ", ".join(f"{tid}: {cnt}" for tid, cnt in sorted(tenant_counts.items()))
        print(f"  {entity_type:<12} -> {counts_str}")

    # Write JSON files.
    out_dir = _PROJECT_ROOT / "data" / "raw" / "novastack"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_entity_types = (
        "users",
        "teams",
        "customers",
        "services",
        "events",
        "incidents",
        "deployments",
        "pull_requests",
        "event_relationships",
    )

    print("\n--- Writing Output Files ---")
    for entity_type in all_entity_types:
        out_path = out_dir / f"{entity_type}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": DATASET_VERSION,
                    "seed": RANDOM_SEED,
                    "count": len(data[entity_type]),
                    entity_type: data[entity_type],
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"  Wrote {out_path.relative_to(_PROJECT_ROOT)}"
              f"  ({len(data[entity_type])} records)")

    # Write source_records.json
    source_records_path = out_dir / "source_records.json"
    serialized_source_records = [r.to_dict() for r in source_records]
    with open(source_records_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": DATASET_VERSION,
                "seed": RANDOM_SEED,
                "count": len(serialized_source_records),
                "source_records": serialized_source_records,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"  Wrote {source_records_path.relative_to(_PROJECT_ROOT)}"
          f"  ({len(serialized_source_records)} records)")

    # Write security_fixtures.json
    security_fixtures_path = out_dir / "security_fixtures.json"
    serialized_security_fixtures = [f.to_dict() for f in sec_fixtures]
    with open(security_fixtures_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": DATASET_VERSION,
                "seed": RANDOM_SEED,
                "count": len(serialized_security_fixtures),
                "security_fixtures": serialized_security_fixtures,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"  Wrote {security_fixtures_path.relative_to(_PROJECT_ROOT)}"
          f"  ({len(serialized_security_fixtures)} records)")

    # Write adversarial_fixtures.json
    adversarial_fixtures_path = out_dir / "adversarial_fixtures.json"
    serialized_adversarial_fixtures = [f.to_dict() for f in adv_fixtures]
    with open(adversarial_fixtures_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": DATASET_VERSION,
                "seed": RANDOM_SEED,
                "count": len(serialized_adversarial_fixtures),
                "adversarial_fixtures": serialized_adversarial_fixtures,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"  Wrote {adversarial_fixtures_path.relative_to(_PROJECT_ROOT)}"
          f"  ({len(serialized_adversarial_fixtures)} records)")

    # Write evaluation files (Milestone 4E-1)
    eval_dir = _PROJECT_ROOT / "data" / "evaluation" / "novastack"
    eval_dir.mkdir(parents=True, exist_ok=True)

    eval_cases_path = eval_dir / "evaluation_cases.json"
    serialized_eval_cases = [c.to_dict() for c in eval_cases]
    with open(eval_cases_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": DATASET_VERSION,
                "seed": RANDOM_SEED,
                "count": len(serialized_eval_cases),
                "evaluation_cases": serialized_eval_cases,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"  Wrote {eval_cases_path.relative_to(_PROJECT_ROOT)}"
          f"  ({len(serialized_eval_cases)} records)")

    taxonomy_path = eval_dir / "evaluation_taxonomy.json"
    taxonomy_data = build_evaluation_taxonomy()
    with open(taxonomy_path, "w", encoding="utf-8") as f:
        json.dump(
            taxonomy_data,
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"  Wrote {taxonomy_path.relative_to(_PROJECT_ROOT)}"
          f"  (taxonomy definitions)")

    print("\nDone.")



if __name__ == "__main__":
    main()


