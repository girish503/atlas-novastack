"""Tests for NovaStack organisational entity generator — Milestone 1.

Covers:
    - Deterministic generation (same seed → same output)
    - Approximate entity counts
    - Unique IDs across all entities
    - Valid team references from users
    - Valid service ownership (owner team exists and is service-owning)
    - Valid customer account ownership
    - Tenant consistency (no cross-tenant references)
    - Manager validity (exists, same tenant, member of team)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src/ is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novastack.config import DEFAULT_TENANTS, RANDOM_SEED
from novastack.generator import NovaStackGenerator


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

@pytest.fixture(scope="module")
def generator() -> NovaStackGenerator:
    """Generate once for the entire test module (deterministic)."""
    gen = NovaStackGenerator(seed=RANDOM_SEED)
    gen.generate()
    return gen


@pytest.fixture(scope="module")
def data(generator: NovaStackGenerator) -> dict:
    """Serialised data dict for inspection."""
    return generator._to_dict()


# ---------------------------------------------------------------
# 1. Deterministic generation
# ---------------------------------------------------------------

class TestDeterminism:
    def test_same_seed_produces_identical_output(self) -> None:
        gen1 = NovaStackGenerator(seed=RANDOM_SEED)
        gen2 = NovaStackGenerator(seed=RANDOM_SEED)
        d1 = gen1.generate()
        d2 = gen2.generate()
        assert d1 == d2, "Two runs with the same seed must produce identical data"

    def test_different_seed_produces_different_output(self) -> None:
        gen1 = NovaStackGenerator(seed=RANDOM_SEED)
        gen2 = NovaStackGenerator(seed=RANDOM_SEED + 1)
        d1 = gen1.generate()
        d2 = gen2.generate()
        assert d1 != d2, "Different seeds should produce different data"


# ---------------------------------------------------------------
# 2. Approximate entity counts
# ---------------------------------------------------------------

class TestEntityCounts:
    def test_total_users_approximate(self, generator: NovaStackGenerator) -> None:
        expected = sum(t.user_count for t in DEFAULT_TENANTS)
        assert len(generator.users) == expected

    def test_total_teams_approximate(self, generator: NovaStackGenerator) -> None:
        expected = sum(t.team_count for t in DEFAULT_TENANTS)
        assert len(generator.teams) == expected

    def test_total_customers_approximate(self, generator: NovaStackGenerator) -> None:
        expected = sum(t.customer_count for t in DEFAULT_TENANTS)
        assert len(generator.customers) == expected

    def test_total_services_approximate(self, generator: NovaStackGenerator) -> None:
        expected = sum(t.service_count for t in DEFAULT_TENANTS)
        assert len(generator.services) == expected

    def test_per_tenant_user_counts(self, generator: NovaStackGenerator) -> None:
        for tenant in DEFAULT_TENANTS:
            actual = sum(
                1 for u in generator.users if u.tenant_id == tenant.tenant_id
            )
            assert actual == tenant.user_count, (
                f"Tenant {tenant.tenant_id}: expected {tenant.user_count} "
                f"users, got {actual}"
            )

    def test_per_tenant_team_counts(self, generator: NovaStackGenerator) -> None:
        for tenant in DEFAULT_TENANTS:
            actual = sum(
                1 for t in generator.teams if t.tenant_id == tenant.tenant_id
            )
            assert actual == tenant.team_count

    def test_per_tenant_service_counts(self, generator: NovaStackGenerator) -> None:
        for tenant in DEFAULT_TENANTS:
            actual = sum(
                1 for s in generator.services if s.tenant_id == tenant.tenant_id
            )
            assert actual == tenant.service_count

    def test_per_tenant_customer_counts(self, generator: NovaStackGenerator) -> None:
        for tenant in DEFAULT_TENANTS:
            actual = sum(
                1 for c in generator.customers if c.tenant_id == tenant.tenant_id
            )
            assert actual == tenant.customer_count


# ---------------------------------------------------------------
# 3. Unique IDs
# ---------------------------------------------------------------

class TestUniqueIds:
    def test_user_ids_unique(self, generator: NovaStackGenerator) -> None:
        ids = [u.user_id for u in generator.users]
        assert len(ids) == len(set(ids)), "Duplicate user IDs found"

    def test_team_ids_unique(self, generator: NovaStackGenerator) -> None:
        ids = [t.team_id for t in generator.teams]
        assert len(ids) == len(set(ids)), "Duplicate team IDs found"

    def test_service_ids_unique(self, generator: NovaStackGenerator) -> None:
        ids = [s.service_id for s in generator.services]
        assert len(ids) == len(set(ids)), "Duplicate service IDs found"

    def test_customer_ids_unique(self, generator: NovaStackGenerator) -> None:
        ids = [c.customer_id for c in generator.customers]
        assert len(ids) == len(set(ids)), "Duplicate customer IDs found"


# ---------------------------------------------------------------
# 4. Valid team references
# ---------------------------------------------------------------

class TestTeamReferences:
    def test_user_team_ids_exist(self, generator: NovaStackGenerator) -> None:
        team_ids = {t.team_id for t in generator.teams}
        for user in generator.users:
            for tid in user.team_ids:
                assert tid in team_ids, (
                    f"User {user.user_id} references non-existent team {tid}"
                )

    def test_user_has_at_least_one_team(self, generator: NovaStackGenerator) -> None:
        for user in generator.users:
            assert len(user.team_ids) >= 1, (
                f"User {user.user_id} has no team assignments"
            )

    def test_user_department_matches_team(self, generator: NovaStackGenerator) -> None:
        team_map = {t.team_id: t for t in generator.teams}
        for user in generator.users:
            for tid in user.team_ids:
                team = team_map[tid]
                assert user.department == team.department, (
                    f"User {user.user_id} department '{user.department}' "
                    f"does not match team '{team.name}' "
                    f"department '{team.department}'"
                )


# ---------------------------------------------------------------
# 5. Valid service ownership
# ---------------------------------------------------------------

class TestServiceOwnership:
    def test_service_owner_team_exists(self, generator: NovaStackGenerator) -> None:
        team_ids = {t.team_id for t in generator.teams}
        for svc in generator.services:
            assert svc.owner_team_id in team_ids, (
                f"Service {svc.service_id} references non-existent "
                f"owner team {svc.owner_team_id}"
            )

    def test_service_owner_is_service_owning_team(
        self, generator: NovaStackGenerator
    ) -> None:
        for svc in generator.services:
            assert svc.owner_team_id in generator._service_owning_team_ids, (
                f"Service {svc.service_id} owner team "
                f"{svc.owner_team_id} is not a service-owning team"
            )

    def test_service_owner_same_tenant(self, generator: NovaStackGenerator) -> None:
        team_map = {t.team_id: t for t in generator.teams}
        for svc in generator.services:
            owner_team = team_map[svc.owner_team_id]
            assert svc.tenant_id == owner_team.tenant_id, (
                f"Service {svc.service_id} (tenant {svc.tenant_id}) "
                f"owned by team in different tenant "
                f"({owner_team.tenant_id})"
            )


# ---------------------------------------------------------------
# 6. Valid customer account ownership
# ---------------------------------------------------------------

class TestCustomerAccountOwnership:
    def test_account_owner_exists(self, generator: NovaStackGenerator) -> None:
        user_ids = {u.user_id for u in generator.users}
        for cust in generator.customers:
            assert cust.account_owner_id in user_ids, (
                f"Customer {cust.customer_id} references non-existent "
                f"account owner {cust.account_owner_id}"
            )

    def test_account_owner_same_tenant(self, generator: NovaStackGenerator) -> None:
        user_map = {u.user_id: u for u in generator.users}
        for cust in generator.customers:
            owner = user_map[cust.account_owner_id]
            assert cust.tenant_id == owner.tenant_id, (
                f"Customer {cust.customer_id} (tenant {cust.tenant_id}) "
                f"has account owner in different tenant "
                f"({owner.tenant_id})"
            )


# ---------------------------------------------------------------
# 7. Tenant consistency (no cross-tenant references)
# ---------------------------------------------------------------

class TestTenantConsistency:
    def test_team_members_same_tenant(self, generator: NovaStackGenerator) -> None:
        user_map = {u.user_id: u for u in generator.users}
        for team in generator.teams:
            for mid in team.member_ids:
                member = user_map[mid]
                assert member.tenant_id == team.tenant_id, (
                    f"Team {team.team_id} has cross-tenant member {mid}"
                )

    def test_no_cross_tenant_service_ownership(
        self, generator: NovaStackGenerator
    ) -> None:
        """Covered by TestServiceOwnership.test_service_owner_same_tenant,
        but kept here as explicit tenant-isolation check."""
        team_map = {t.team_id: t for t in generator.teams}
        for svc in generator.services:
            assert svc.tenant_id == team_map[svc.owner_team_id].tenant_id

    def test_no_cross_tenant_customer_ownership(
        self, generator: NovaStackGenerator
    ) -> None:
        user_map = {u.user_id: u for u in generator.users}
        for cust in generator.customers:
            assert cust.tenant_id == user_map[cust.account_owner_id].tenant_id


# ---------------------------------------------------------------
# 8. Manager validity
# ---------------------------------------------------------------

class TestManagerValidity:
    def test_manager_exists(self, generator: NovaStackGenerator) -> None:
        user_ids = {u.user_id for u in generator.users}
        for team in generator.teams:
            assert team.manager_id, f"Team {team.team_id} has no manager"
            assert team.manager_id in user_ids, (
                f"Team {team.team_id} manager {team.manager_id} does not exist"
            )

    def test_manager_same_tenant(self, generator: NovaStackGenerator) -> None:
        user_map = {u.user_id: u for u in generator.users}
        for team in generator.teams:
            mgr = user_map[team.manager_id]
            assert mgr.tenant_id == team.tenant_id, (
                f"Team {team.team_id} manager is in a different tenant"
            )

    def test_manager_is_team_member(self, generator: NovaStackGenerator) -> None:
        for team in generator.teams:
            assert team.manager_id in team.member_ids, (
                f"Team {team.team_id} manager {team.manager_id} "
                f"is not in member_ids"
            )

    def test_every_team_has_members(self, generator: NovaStackGenerator) -> None:
        for team in generator.teams:
            assert len(team.member_ids) >= 2, (
                f"Team {team.team_id} has only {len(team.member_ids)} "
                f"member(s), minimum is 2"
            )


# ---------------------------------------------------------------
# 9. Full validation method
# ---------------------------------------------------------------

class TestValidationMethod:
    def test_validate_returns_no_errors(self, generator: NovaStackGenerator) -> None:
        errors = generator.validate()
        assert errors == [], (
            f"Validation found {len(errors)} error(s):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )


# ---------------------------------------------------------------
# 10. Data serialisation
# ---------------------------------------------------------------

class TestSerialisation:
    def test_all_entity_types_present(self, data: dict) -> None:
        expected = {
            "users", "teams", "customers", "services",
            "events", "incidents", "deployments", "pull_requests",
            "event_relationships",
        }
        assert set(data.keys()) == expected

    def test_created_at_is_iso_string(self, data: dict) -> None:
        for entity_type in ("users", "teams", "customers", "services", "pull_requests"):
            for record in data[entity_type]:
                assert isinstance(record["created_at"], str), (
                    f"{entity_type} record has non-string created_at"
                )
                from datetime import datetime
                datetime.fromisoformat(record["created_at"])


# ===============================================================
# Milestone 2 — Ground-Truth Event Layer Tests
# ===============================================================

# ---------------------------------------------------------------
# 11. Event entity counts & presence
# ---------------------------------------------------------------

class TestEventCounts:
    def test_major_events_count(self, generator: NovaStackGenerator) -> None:
        assert len(generator.events) == 12, "Must generate exactly 12 major events"

    def test_incidents_count(self, generator: NovaStackGenerator) -> None:
        assert len(generator.incidents) == 12, "Must generate 12 incidents (one per major event)"

    def test_deployments_count(self, generator: NovaStackGenerator) -> None:
        # 8 triggering deployments + 1 rollback deployment = 9
        assert len(generator.deployments) == 9

    def test_pull_requests_count(self, generator: NovaStackGenerator) -> None:
        # 11 events have fixing PRs (Event 6 database migration was a manual rollback without PR)
        assert len(generator.pull_requests) == 11

    def test_event_relationships_count(self, generator: NovaStackGenerator) -> None:
        assert len(generator.event_relationships) >= 80, "Expected at least 80 event relationships"


# ---------------------------------------------------------------
# 12. Event unique IDs
# ---------------------------------------------------------------

class TestEventUniqueIds:
    def test_event_ids_unique(self, generator: NovaStackGenerator) -> None:
        ids = [e.event_id for e in generator.events]
        assert len(ids) == len(set(ids)), "Duplicate event IDs found"

    def test_incident_ids_unique(self, generator: NovaStackGenerator) -> None:
        ids = [i.incident_id for i in generator.incidents]
        assert len(ids) == len(set(ids)), "Duplicate incident IDs found"

    def test_deployment_ids_unique(self, generator: NovaStackGenerator) -> None:
        ids = [d.deployment_id for d in generator.deployments]
        assert len(ids) == len(set(ids)), "Duplicate deployment IDs found"

    def test_pull_request_ids_unique(self, generator: NovaStackGenerator) -> None:
        ids = [p.pull_request_id for p in generator.pull_requests]
        assert len(ids) == len(set(ids)), "Duplicate pull request IDs found"

    def test_relationship_ids_unique(self, generator: NovaStackGenerator) -> None:
        ids = [r.relationship_id for r in generator.event_relationships]
        assert len(ids) == len(set(ids)), "Duplicate relationship IDs found"


# ---------------------------------------------------------------
# 13. Event entity reference integrity
# ---------------------------------------------------------------

class TestEventReferences:
    def test_all_affected_services_exist(self, generator: NovaStackGenerator) -> None:
        svc_ids = {s.service_id for s in generator.services}
        for ev in generator.events:
            assert len(ev.affected_service_ids) >= 1, f"Event {ev.event_id} has no affected services"
            for sid in ev.affected_service_ids:
                assert sid in svc_ids, f"Event {ev.event_id} references non-existent service {sid}"

    def test_all_deployment_services_exist(self, generator: NovaStackGenerator) -> None:
        svc_ids = {s.service_id for s in generator.services}
        for dep in generator.deployments:
            assert dep.service_id in svc_ids, f"Deployment {dep.deployment_id} references unknown service {dep.service_id}"

    def test_all_pull_request_services_exist(self, generator: NovaStackGenerator) -> None:
        svc_ids = {s.service_id for s in generator.services}
        for pr in generator.pull_requests:
            assert pr.service_id in svc_ids, f"PR {pr.pull_request_id} references unknown service {pr.service_id}"

    def test_all_impacted_customers_exist(self, generator: NovaStackGenerator) -> None:
        cust_ids = {c.customer_id for c in generator.customers}
        for ev in generator.events:
            for cid in ev.impacted_customer_ids:
                assert cid in cust_ids, f"Event {ev.event_id} references unknown customer {cid}"

    def test_all_responsible_teams_exist(self, generator: NovaStackGenerator) -> None:
        team_ids = {t.team_id for t in generator.teams}
        for ev in generator.events:
            assert ev.responsible_team_id in team_ids, f"Event {ev.event_id} references unknown team {ev.responsible_team_id}"

    def test_all_incident_commanders_exist(self, generator: NovaStackGenerator) -> None:
        user_ids = {u.user_id for u in generator.users}
        for inc in generator.incidents:
            assert inc.incident_commander_id in user_ids, f"Incident {inc.incident_id} references unknown commander {inc.incident_commander_id}"

    def test_all_deployment_users_and_teams_exist(self, generator: NovaStackGenerator) -> None:
        user_ids = {u.user_id for u in generator.users}
        team_ids = {t.team_id for t in generator.teams}
        for dep in generator.deployments:
            assert dep.deployed_by in user_ids, f"Deployment {dep.deployment_id} references unknown user {dep.deployed_by}"
            assert dep.team_id in team_ids, f"Deployment {dep.deployment_id} references unknown team {dep.team_id}"

    def test_all_pull_request_authors_and_teams_exist(self, generator: NovaStackGenerator) -> None:
        user_ids = {u.user_id for u in generator.users}
        team_ids = {t.team_id for t in generator.teams}
        for pr in generator.pull_requests:
            assert pr.author_id in user_ids, f"PR {pr.pull_request_id} references unknown author {pr.author_id}"
            assert pr.team_id in team_ids, f"PR {pr.pull_request_id} references unknown team {pr.team_id}"


# ---------------------------------------------------------------
# 14. Event tenant isolation
# ---------------------------------------------------------------

class TestEventTenantIsolation:
    def test_all_events_belong_to_novastack(self, generator: NovaStackGenerator) -> None:
        for ev in generator.events:
            assert ev.tenant_id == "TENANT-NOVASTACK"

    def test_all_event_sub_entities_same_tenant(self, generator: NovaStackGenerator) -> None:
        for inc in generator.incidents:
            assert inc.tenant_id == "TENANT-NOVASTACK"
        for dep in generator.deployments:
            assert dep.tenant_id == "TENANT-NOVASTACK"
        for pr in generator.pull_requests:
            assert pr.tenant_id == "TENANT-NOVASTACK"
        for rel in generator.event_relationships:
            assert rel.tenant_id == "TENANT-NOVASTACK"

    def test_no_cross_tenant_references_in_events(self, generator: NovaStackGenerator) -> None:
        svc_map = {s.service_id: s for s in generator.services}
        cust_map = {c.customer_id: c for c in generator.customers}
        team_map = {t.team_id: t for t in generator.teams}
        for ev in generator.events:
            assert team_map[ev.responsible_team_id].tenant_id == ev.tenant_id
            for sid in ev.affected_service_ids:
                assert svc_map[sid].tenant_id == ev.tenant_id
            for cid in ev.impacted_customer_ids:
                assert cust_map[cid].tenant_id == ev.tenant_id


# ---------------------------------------------------------------
# 15. Timeline coherence
# ---------------------------------------------------------------

class TestTimelineCoherence:
    def test_event_start_before_end(self, generator: NovaStackGenerator) -> None:
        for ev in generator.events:
            assert ev.start_time < ev.end_time, f"Event {ev.event_id} start_time must be before end_time"

    def test_triggering_deployment_before_event_start(self, generator: NovaStackGenerator) -> None:
        dep_map = {d.deployment_id: d for d in generator.deployments}
        for ev in generator.events:
            if ev.triggering_deployment_id:
                dep = dep_map[ev.triggering_deployment_id]
                assert dep.deployed_at < ev.start_time, (
                    f"Event {ev.event_id}: triggering deployment {dep.deployment_id} "
                    f"deployed_at ({dep.deployed_at}) must precede event start ({ev.start_time})"
                )

    def test_incident_timeline_coherent(self, generator: NovaStackGenerator) -> None:
        for inc in generator.incidents:
            assert inc.reported_at <= inc.acknowledged_at <= inc.resolved_at, (
                f"Incident {inc.incident_id} timeline incoherent: "
                f"reported={inc.reported_at}, ack={inc.acknowledged_at}, resolved={inc.resolved_at}"
            )

    def test_fixing_pr_timeline_coherent(self, generator: NovaStackGenerator) -> None:
        for pr in generator.pull_requests:
            assert pr.created_at <= pr.merged_at, (
                f"PR {pr.pull_request_id} created_at ({pr.created_at}) must precede merged_at ({pr.merged_at})"
            )

    def test_events_within_2025_2026_window(self, generator: NovaStackGenerator) -> None:
        for ev in generator.events:
            assert 2025 <= ev.start_time.year <= 2026, f"Event {ev.event_id} start year not in 2025-2026: {ev.start_time.year}"
            assert 2025 <= ev.end_time.year <= 2026, f"Event {ev.event_id} end year not in 2025-2026: {ev.end_time.year}"


# ---------------------------------------------------------------
# 16. Causal and relationship validity
# ---------------------------------------------------------------

class TestCausalRelationships:
    def test_all_relationship_entities_exist(self, generator: NovaStackGenerator) -> None:
        all_ids = set()
        all_ids.update(u.user_id for u in generator.users)
        all_ids.update(t.team_id for t in generator.teams)
        all_ids.update(s.service_id for s in generator.services)
        all_ids.update(c.customer_id for c in generator.customers)
        all_ids.update(e.event_id for e in generator.events)
        all_ids.update(i.incident_id for i in generator.incidents)
        all_ids.update(d.deployment_id for d in generator.deployments)
        all_ids.update(p.pull_request_id for p in generator.pull_requests)

        for rel in generator.event_relationships:
            assert rel.source_id in all_ids, f"Rel {rel.relationship_id} source {rel.source_id} not found"
            assert rel.target_id in all_ids, f"Rel {rel.relationship_id} target {rel.target_id} not found"

    def test_causal_relationships_match_events(self, generator: NovaStackGenerator) -> None:
        caused_by_pairs = {
            (r.source_id, r.target_id)
            for r in generator.event_relationships
            if r.relationship_type == "caused_by"
        }
        for ev in generator.events:
            if ev.triggering_deployment_id:
                assert (ev.event_id, ev.triggering_deployment_id) in caused_by_pairs, (
                    f"Event {ev.event_id} missing caused_by relationship to deployment {ev.triggering_deployment_id}"
                )

    def test_resolution_relationships_match_events(self, generator: NovaStackGenerator) -> None:
        resolved_by_pairs = {
            (r.source_id, r.target_id)
            for r in generator.event_relationships
            if r.relationship_type == "resolved_by"
        }
        for ev in generator.events:
            if ev.fixing_pull_request_id:
                assert (ev.event_id, ev.fixing_pull_request_id) in resolved_by_pairs, (
                    f"Event {ev.event_id} missing resolved_by relationship to PR {ev.fixing_pull_request_id}"
                )


# ---------------------------------------------------------------
# 17. Event structural variation
# ---------------------------------------------------------------

class TestEventVariation:
    def test_structural_variation_in_events(self, generator: NovaStackGenerator) -> None:
        with_dep = [e for e in generator.events if e.triggering_deployment_id is not None]
        without_dep = [e for e in generator.events if e.triggering_deployment_id is None]
        assert len(with_dep) > 0, "Expected some events to have triggering deployments"
        assert len(without_dep) > 0, "Expected some events to NOT have triggering deployments"

        with_pr = [e for e in generator.events if e.fixing_pull_request_id is not None]
        without_pr = [e for e in generator.events if e.fixing_pull_request_id is None]
        assert len(with_pr) > 0, "Expected some events to have fixing PRs"
        assert len(without_pr) > 0, "Expected some events to NOT have fixing PRs (e.g. manual rollback)"

        with_cust_impact = [e for e in generator.events if len(e.impacted_customer_ids) > 0]
        without_cust_impact = [e for e in generator.events if len(e.impacted_customer_ids) == 0]
        assert len(with_cust_impact) > 0, "Expected some events to impact customers"
        assert len(without_cust_impact) > 0, "Expected some events to have zero customer impact"

        multi_service = [e for e in generator.events if len(e.affected_service_ids) > 1]
        single_service = [e for e in generator.events if len(e.affected_service_ids) == 1]
        assert len(multi_service) > 0, "Expected some events to affect multiple services"
        assert len(single_service) > 0, "Expected some events to affect a single service"

    def test_rollback_event_exists(self, generator: NovaStackGenerator) -> None:
        rollback_events = [e for e in generator.events if e.event_type == "rollback"]
        assert len(rollback_events) == 1, "Expected exactly 1 rollback event"
        rollback_rels = [
            r for r in generator.event_relationships if r.relationship_type == "rolled_back_by"
        ]
        assert len(rollback_rels) == 1, "Expected exactly 1 rolled_back_by relationship"

