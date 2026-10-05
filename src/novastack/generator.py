"""Deterministic synthetic enterprise data generator.

Generates internally-consistent Users, Teams, Customers, and Services
for one or more tenants, plus a ground-truth event layer for
TENANT-NOVASTACK, using a seeded random.Random instance.

Generation order (avoids circular dependencies):
    1. Teams          — from per-tenant template pools
    2. Users          — assigned to teams
    3. Backfill teams — member_ids, manager_id
    4. Services       — assigned to service-owning teams
    5. Backfill teams — service_ids
    6. Customers      — account_owner drawn from Sales users
    7. Event layer    — events, incidents, deployments, PRs, relationships
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from novastack.config import (
    CUSTOMER_INDUSTRIES,
    CUSTOMER_NAMES,
    CUSTOMER_SEGMENTS,
    DATE_RANGE_DAYS,
    DATE_START_ISO,
    DEFAULT_ROLES,
    DEFAULT_TENANTS,
    FIRST_NAMES,
    GENERIC_TEAM_POOL,
    LAST_NAMES,
    RANDOM_SEED,
    ROLES_BY_DEPARTMENT,
    SERVICE_TEMPLATES,
    TENANT_TEAM_POOLS,
    TenantConfig,
)
from novastack.events import EVENT_BLUEPRINTS
from novastack.models import (
    Customer, Deployment, Event, EventRelationship,
    Incident, PullRequest, Service, Team, User,
)


class NovaStackGenerator:
    """Deterministic generator for synthetic enterprise data.

    All randomness flows through a single ``random.Random`` instance
    seeded at construction time, so identical seeds produce identical
    datasets.
    """

    # Minimum users per team so that each team has a manager + ≥1 member.
    _MIN_USERS_PER_TEAM: int = 2

    def __init__(
        self,
        seed: int = RANDOM_SEED,
        tenants: list[TenantConfig] | None = None,
    ) -> None:
        self.seed = seed
        self.tenants = tenants if tenants is not None else list(DEFAULT_TENANTS)
        self.rng = random.Random(seed)

        # Populated during generation.
        self.users: list[User] = []
        self.teams: list[Team] = []
        self.customers: list[Customer] = []
        self.services: list[Service] = []

        # Milestone 2 — ground-truth event layer.
        self.events: list[Event] = []
        self.incidents: list[Incident] = []
        self.deployments: list[Deployment] = []
        self.pull_requests: list[PullRequest] = []
        self.event_relationships: list[EventRelationship] = []

        # Internal bookkeeping — tracks which team IDs can own services.
        self._service_owning_team_ids: set[str] = set()

        # Pre-parse the date-range start once.
        self._date_start = datetime.fromisoformat(DATE_START_ISO)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self) -> dict[str, list[dict[str, Any]]]:
        """Run the full generation pipeline and return serialisable dicts."""
        self.users.clear()
        self.teams.clear()
        self.customers.clear()
        self.services.clear()
        self._service_owning_team_ids.clear()
        self.events.clear()
        self.incidents.clear()
        self.deployments.clear()
        self.pull_requests.clear()
        self.event_relationships.clear()

        for tenant in self.tenants:
            teams = self._generate_teams(tenant)
            users = self._generate_users(tenant, teams)
            self._backfill_team_membership(teams, users)
            services = self._generate_services(tenant, teams)
            self._backfill_team_services(teams, services)
            customers = self._generate_customers(tenant, users)

            self.teams.extend(teams)
            self.users.extend(users)
            self.services.extend(services)
            self.customers.extend(customers)

        # Milestone 2 — ground-truth event layer (NovaStack only).
        self._generate_event_layer()

        return self._to_dict()

    def validate(self) -> list[str]:
        """Check all cross-reference consistency rules.

        Returns a list of error strings.  An empty list means the
        dataset is fully consistent.
        """
        errors: list[str] = []

        user_idx: dict[str, User] = {u.user_id: u for u in self.users}
        team_idx: dict[str, Team] = {t.team_id: t for t in self.teams}
        svc_idx: dict[str, Service] = {s.service_id: s for s in self.services}
        cust_idx: dict[str, Customer] = {c.customer_id: c for c in self.customers}

        # --- User rules ---
        for u in self.users:
            for tid in u.team_ids:
                if tid not in team_idx:
                    errors.append(
                        f"User {u.user_id}: references non-existent team {tid}"
                    )
                else:
                    team = team_idx[tid]
                    if team.tenant_id != u.tenant_id:
                        errors.append(
                            f"User {u.user_id}: team {tid} belongs to "
                            f"different tenant ({team.tenant_id})"
                        )
                    if u.department != team.department:
                        errors.append(
                            f"User {u.user_id}: department '{u.department}' "
                            f"does not match team '{team.name}' department "
                            f"'{team.department}'"
                        )

        # --- Team rules ---
        for t in self.teams:
            # Manager validity
            if t.manager_id:
                if t.manager_id not in user_idx:
                    errors.append(
                        f"Team {t.team_id}: manager {t.manager_id} "
                        f"does not exist"
                    )
                else:
                    mgr = user_idx[t.manager_id]
                    if mgr.tenant_id != t.tenant_id:
                        errors.append(
                            f"Team {t.team_id}: manager {t.manager_id} "
                            f"is in a different tenant"
                        )
                    if t.team_id not in mgr.team_ids:
                        errors.append(
                            f"Team {t.team_id}: manager {t.manager_id} "
                            f"is not a member of this team"
                        )
            else:
                errors.append(f"Team {t.team_id}: has no manager")

            # Member validity
            for mid in t.member_ids:
                if mid not in user_idx:
                    errors.append(
                        f"Team {t.team_id}: member {mid} does not exist"
                    )
                else:
                    member = user_idx[mid]
                    if member.tenant_id != t.tenant_id:
                        errors.append(
                            f"Team {t.team_id}: member {mid} is in a "
                            f"different tenant"
                        )

        # --- Service rules ---
        for s in self.services:
            if s.owner_team_id not in team_idx:
                errors.append(
                    f"Service {s.service_id}: owner team "
                    f"{s.owner_team_id} does not exist"
                )
            else:
                owner = team_idx[s.owner_team_id]
                if owner.tenant_id != s.tenant_id:
                    errors.append(
                        f"Service {s.service_id}: owner team "
                        f"{s.owner_team_id} is in a different tenant"
                    )
                if s.owner_team_id not in self._service_owning_team_ids:
                    errors.append(
                        f"Service {s.service_id}: owner team "
                        f"'{owner.name}' is not a service-owning team"
                    )

        # --- Customer rules ---
        for c in self.customers:
            if c.account_owner_id not in user_idx:
                errors.append(
                    f"Customer {c.customer_id}: account owner "
                    f"{c.account_owner_id} does not exist"
                )
            else:
                owner = user_idx[c.account_owner_id]
                if owner.tenant_id != c.tenant_id:
                    errors.append(
                        f"Customer {c.customer_id}: account owner "
                        f"{c.account_owner_id} is in a different tenant"
                    )

        # --- Global uniqueness ---
        self._check_unique_ids(
            [u.user_id for u in self.users], "user_id", errors
        )
        self._check_unique_ids(
            [t.team_id for t in self.teams], "team_id", errors
        )
        self._check_unique_ids(
            [s.service_id for s in self.services], "service_id", errors
        )
        self._check_unique_ids(
            [c.customer_id for c in self.customers], "customer_id", errors
        )

        # ==============================================================
        # Milestone 2 — Event layer validation
        # ==============================================================

        # --- Event-layer uniqueness ---
        self._check_unique_ids(
            [e.event_id for e in self.events], "event_id", errors
        )
        self._check_unique_ids(
            [i.incident_id for i in self.incidents], "incident_id", errors
        )
        self._check_unique_ids(
            [d.deployment_id for d in self.deployments],
            "deployment_id", errors,
        )
        self._check_unique_ids(
            [p.pull_request_id for p in self.pull_requests],
            "pull_request_id", errors,
        )

        event_idx = {e.event_id: e for e in self.events}
        dep_idx = {d.deployment_id: d for d in self.deployments}
        pr_idx = {p.pull_request_id: p for p in self.pull_requests}
        inc_idx = {i.incident_id: i for i in self.incidents}

        # --- Event rules ---
        for ev in self.events:
            for sid in ev.affected_service_ids:
                if sid not in svc_idx:
                    errors.append(
                        f"Event {ev.event_id}: references non-existent "
                        f"service {sid}"
                    )
                elif svc_idx[sid].tenant_id != ev.tenant_id:
                    errors.append(
                        f"Event {ev.event_id}: service {sid} belongs "
                        f"to different tenant"
                    )

            if ev.responsible_team_id not in team_idx:
                errors.append(
                    f"Event {ev.event_id}: responsible team "
                    f"{ev.responsible_team_id} does not exist"
                )
            elif team_idx[ev.responsible_team_id].tenant_id != ev.tenant_id:
                errors.append(
                    f"Event {ev.event_id}: responsible team in "
                    f"different tenant"
                )

            for cid in ev.impacted_customer_ids:
                if cid not in cust_idx:
                    errors.append(
                        f"Event {ev.event_id}: references non-existent "
                        f"customer {cid}"
                    )
                elif cust_idx[cid].tenant_id != ev.tenant_id:
                    errors.append(
                        f"Event {ev.event_id}: customer {cid} in "
                        f"different tenant"
                    )

            if ev.triggering_deployment_id is not None:
                if ev.triggering_deployment_id not in dep_idx:
                    errors.append(
                        f"Event {ev.event_id}: triggering deployment "
                        f"{ev.triggering_deployment_id} does not exist"
                    )

            if ev.fixing_pull_request_id is not None:
                if ev.fixing_pull_request_id not in pr_idx:
                    errors.append(
                        f"Event {ev.event_id}: fixing PR "
                        f"{ev.fixing_pull_request_id} does not exist"
                    )

            # Timeline coherence
            if ev.end_time is not None and ev.start_time > ev.end_time:
                errors.append(
                    f"Event {ev.event_id}: start_time > end_time"
                )

        # --- Deployment rules ---
        for dep in self.deployments:
            if dep.service_id not in svc_idx:
                errors.append(
                    f"Deployment {dep.deployment_id}: references "
                    f"non-existent service {dep.service_id}"
                )
            elif svc_idx[dep.service_id].tenant_id != dep.tenant_id:
                errors.append(
                    f"Deployment {dep.deployment_id}: service in "
                    f"different tenant"
                )
            if dep.deployed_by not in user_idx:
                errors.append(
                    f"Deployment {dep.deployment_id}: deployed_by user "
                    f"{dep.deployed_by} does not exist"
                )
            elif user_idx[dep.deployed_by].tenant_id != dep.tenant_id:
                errors.append(
                    f"Deployment {dep.deployment_id}: deployed_by user "
                    f"in different tenant"
                )
            if dep.team_id not in team_idx:
                errors.append(
                    f"Deployment {dep.deployment_id}: team "
                    f"{dep.team_id} does not exist"
                )
            elif team_idx[dep.team_id].tenant_id != dep.tenant_id:
                errors.append(
                    f"Deployment {dep.deployment_id}: team in "
                    f"different tenant"
                )

        # --- Pull request rules ---
        for pr in self.pull_requests:
            if pr.service_id not in svc_idx:
                errors.append(
                    f"PR {pr.pull_request_id}: references non-existent "
                    f"service {pr.service_id}"
                )
            if pr.author_id not in user_idx:
                errors.append(
                    f"PR {pr.pull_request_id}: author {pr.author_id} "
                    f"does not exist"
                )
            elif user_idx[pr.author_id].tenant_id != pr.tenant_id:
                errors.append(
                    f"PR {pr.pull_request_id}: author in different tenant"
                )
            if pr.team_id not in team_idx:
                errors.append(
                    f"PR {pr.pull_request_id}: team {pr.team_id} "
                    f"does not exist"
                )
            if pr.merged_at is not None and pr.created_at > pr.merged_at:
                errors.append(
                    f"PR {pr.pull_request_id}: created_at > merged_at"
                )

        # --- Incident rules ---
        for inc in self.incidents:
            if inc.event_id not in event_idx:
                errors.append(
                    f"Incident {inc.incident_id}: references "
                    f"non-existent event {inc.event_id}"
                )
            elif event_idx[inc.event_id].tenant_id != inc.tenant_id:
                errors.append(
                    f"Incident {inc.incident_id}: event in "
                    f"different tenant"
                )
            if inc.assigned_team_id and inc.assigned_team_id not in team_idx:
                errors.append(
                    f"Incident {inc.incident_id}: assigned team "
                    f"{inc.assigned_team_id} does not exist"
                )
            if inc.incident_commander_id:
                if inc.incident_commander_id not in user_idx:
                    errors.append(
                        f"Incident {inc.incident_id}: commander "
                        f"{inc.incident_commander_id} does not exist"
                    )
                elif (user_idx[inc.incident_commander_id].tenant_id
                      != inc.tenant_id):
                    errors.append(
                        f"Incident {inc.incident_id}: commander in "
                        f"different tenant"
                    )
            # Timeline coherence
            if (inc.acknowledged_at is not None
                    and inc.reported_at > inc.acknowledged_at):
                errors.append(
                    f"Incident {inc.incident_id}: reported_at > "
                    f"acknowledged_at"
                )
            if (inc.resolved_at is not None
                    and inc.reported_at > inc.resolved_at):
                errors.append(
                    f"Incident {inc.incident_id}: reported_at > "
                    f"resolved_at"
                )

        # --- Causal timeline coherence ---
        for ev in self.events:
            if ev.triggering_deployment_id and ev.triggering_deployment_id in dep_idx:
                dep = dep_idx[ev.triggering_deployment_id]
                if dep.deployed_at > ev.start_time:
                    errors.append(
                        f"Event {ev.event_id}: triggering deployment "
                        f"deployed_at ({dep.deployed_at}) is after "
                        f"event start_time ({ev.start_time})"
                    )

        # --- Relationship rules ---
        # Build a universal entity-ID registry for reference checking.
        all_entity_ids: set[str] = set()
        all_entity_ids.update(u.user_id for u in self.users)
        all_entity_ids.update(t.team_id for t in self.teams)
        all_entity_ids.update(s.service_id for s in self.services)
        all_entity_ids.update(c.customer_id for c in self.customers)
        all_entity_ids.update(e.event_id for e in self.events)
        all_entity_ids.update(i.incident_id for i in self.incidents)
        all_entity_ids.update(d.deployment_id for d in self.deployments)
        all_entity_ids.update(p.pull_request_id for p in self.pull_requests)

        self._check_unique_ids(
            [r.relationship_id for r in self.event_relationships],
            "relationship_id", errors,
        )
        for rel in self.event_relationships:
            if rel.source_id not in all_entity_ids:
                errors.append(
                    f"Relationship {rel.relationship_id}: source "
                    f"{rel.source_id} does not exist"
                )
            if rel.target_id not in all_entity_ids:
                errors.append(
                    f"Relationship {rel.relationship_id}: target "
                    f"{rel.target_id} does not exist"
                )

        return errors

    # ------------------------------------------------------------------
    # Generation helpers
    # ------------------------------------------------------------------

    def _generate_teams(self, tenant: TenantConfig) -> list[Team]:
        pool = TENANT_TEAM_POOLS.get(
            tenant.tenant_id, GENERIC_TEAM_POOL
        )
        selected = pool[: tenant.team_count]
        if len(selected) < tenant.team_count:
            raise ValueError(
                f"Tenant {tenant.tenant_id} requests {tenant.team_count} "
                f"teams but pool only has {len(selected)} templates"
            )

        teams: list[Team] = []
        for i, (name, dept, can_own) in enumerate(selected, start=1):
            team_id = f"TEAM-{tenant.short_code}-{i:04d}"
            team = Team(
                team_id=team_id,
                tenant_id=tenant.tenant_id,
                name=name,
                department=dept,
                created_at=self._random_date(),
            )
            teams.append(team)
            if can_own:
                self._service_owning_team_ids.add(team_id)

        return teams

    def _generate_users(
        self, tenant: TenantConfig, teams: list[Team]
    ) -> list[User]:
        n_teams = len(teams)
        total = tenant.user_count
        if total < self._MIN_USERS_PER_TEAM * n_teams:
            raise ValueError(
                f"Tenant {tenant.tenant_id}: {total} users cannot fill "
                f"{n_teams} teams with minimum {self._MIN_USERS_PER_TEAM} each"
            )

        # Distribute users across teams.
        team_sizes = [self._MIN_USERS_PER_TEAM] * n_teams
        remaining = total - self._MIN_USERS_PER_TEAM * n_teams
        for _ in range(remaining):
            idx = self.rng.randint(0, n_teams - 1)
            team_sizes[idx] += 1

        users: list[User] = []
        used_emails: set[str] = set()
        counter = 1

        for team, size in zip(teams, team_sizes):
            for _ in range(size):
                first = self.rng.choice(FIRST_NAMES)
                last = self.rng.choice(LAST_NAMES)
                name = f"{first} {last}"
                email = self._unique_email(
                    first, last, tenant.email_domain, used_emails
                )
                role = self.rng.choice(
                    ROLES_BY_DEPARTMENT.get(team.department, DEFAULT_ROLES)
                )

                user = User(
                    user_id=f"USR-{tenant.short_code}-{counter:04d}",
                    tenant_id=tenant.tenant_id,
                    name=name,
                    email=email,
                    role=role,
                    department=team.department,
                    team_ids=[team.team_id],
                    status=self._weighted_choice(
                        ["active", "inactive", "on_leave"],
                        [0.90, 0.05, 0.05],
                    ),
                    created_at=self._random_date(),
                )
                users.append(user)
                counter += 1

        return users

    def _backfill_team_membership(
        self, teams: list[Team], users: list[User]
    ) -> None:
        """Set each team's member_ids and manager_id from generated users."""
        team_members: dict[str, list[str]] = defaultdict(list)
        for u in users:
            for tid in u.team_ids:
                team_members[tid].append(u.user_id)

        for team in teams:
            members = team_members.get(team.team_id, [])
            team.member_ids = list(members)
            if members:
                team.manager_id = self.rng.choice(members)

    def _generate_services(
        self, tenant: TenantConfig, teams: list[Team]
    ) -> list[Service]:
        svc_teams = [
            t for t in teams if t.team_id in self._service_owning_team_ids
        ]
        if not svc_teams:
            raise ValueError(
                f"Tenant {tenant.tenant_id} has no service-owning teams"
            )

        pool = list(SERVICE_TEMPLATES)
        self.rng.shuffle(pool)
        selected = pool[: tenant.service_count]

        services: list[Service] = []
        for i, (name, desc, crit) in enumerate(selected, start=1):
            owner = self.rng.choice(svc_teams)
            svc = Service(
                service_id=f"SVC-{tenant.short_code}-{i:04d}",
                tenant_id=tenant.tenant_id,
                name=name,
                description=desc,
                owner_team_id=owner.team_id,
                repository=f"{tenant.name.lower()}/{name}",
                environment=self._weighted_choice(
                    ["production", "staging"], [0.80, 0.20]
                ),
                criticality=crit,
                status=self._weighted_choice(
                    ["active", "deprecated", "maintenance"],
                    [0.90, 0.05, 0.05],
                ),
                created_at=self._random_date(),
            )
            services.append(svc)

        return services

    @staticmethod
    def _backfill_team_services(
        teams: list[Team], services: list[Service]
    ) -> None:
        """Populate each team's service_ids from generated services."""
        team_svcs: dict[str, list[str]] = defaultdict(list)
        for s in services:
            team_svcs[s.owner_team_id].append(s.service_id)
        for team in teams:
            team.service_ids = team_svcs.get(team.team_id, [])

    def _generate_customers(
        self, tenant: TenantConfig, users: list[User]
    ) -> list[Customer]:
        sales_users = [u for u in users if u.department == "Sales"]
        if not sales_users:
            # Fallback — use all users if tenant has no Sales department.
            sales_users = list(users)

        pool = list(CUSTOMER_NAMES)
        self.rng.shuffle(pool)

        customers: list[Customer] = []
        for i in range(tenant.customer_count):
            name = pool[i] if i < len(pool) else f"{pool[i % len(pool)]} #{i}"
            owner = self.rng.choice(sales_users)
            cust = Customer(
                customer_id=f"CUST-{tenant.short_code}-{i + 1:04d}",
                tenant_id=tenant.tenant_id,
                name=name,
                segment=self.rng.choice(CUSTOMER_SEGMENTS),
                industry=self.rng.choice(CUSTOMER_INDUSTRIES),
                account_owner_id=owner.user_id,
                status=self._weighted_choice(
                    ["active", "churned", "prospect"],
                    [0.85, 0.10, 0.05],
                ),
                created_at=self._random_date(),
            )
            customers.append(cust)

        return customers

    # ------------------------------------------------------------------
    # Milestone 2 — Event layer generation
    # ------------------------------------------------------------------

    def _generate_event_layer(self) -> None:
        """Generate ground-truth events from blueprints.

        All events target TENANT-NOVASTACK.  The method resolves service
        and team names from the blueprints to actual entity IDs generated
        in the organisational layer.
        """
        # Find the NovaStack tenant config.
        ns_tenant: TenantConfig | None = None
        for t in self.tenants:
            if t.tenant_id == "TENANT-NOVASTACK":
                ns_tenant = t
                break
        if ns_tenant is None:
            return  # No NovaStack tenant — skip event generation.

        tenant_id = ns_tenant.tenant_id
        sc = ns_tenant.short_code

        # Build lookup maps scoped to NovaStack.
        svc_by_name: dict[str, Service] = {
            s.name: s for s in self.services
            if s.tenant_id == tenant_id
        }
        team_by_name: dict[str, Team] = {
            t.name: t for t in self.teams
            if t.tenant_id == tenant_id
        }
        users_by_team: dict[str, list[User]] = defaultdict(list)
        for u in self.users:
            if u.tenant_id == tenant_id:
                for tid in u.team_ids:
                    users_by_team[tid].append(u)
        ns_services = [
            s for s in self.services if s.tenant_id == tenant_id
        ]
        ns_teams = [
            t for t in self.teams if t.tenant_id == tenant_id
        ]
        team_by_id = {t.team_id: t for t in ns_teams}
        ns_customers = [
            c for c in self.customers if c.tenant_id == tenant_id
        ]

        dep_counter = 1
        pr_counter = 1
        rel_counter = 1

        for i, bp in enumerate(EVENT_BLUEPRINTS, start=1):
            event_id = f"EVT-{sc}-{i:04d}"
            inc_id = f"INC-{sc}-{i:04d}"

            # Resolve primary + secondary services.
            primary_name = bp["primary_service"]
            primary_svc = svc_by_name.get(primary_name)
            if primary_svc is None and ns_services:
                primary_svc = ns_services[(i - 1) % len(ns_services)]

            secondary_svcs = []
            for n in bp.get("secondary_services", []):
                s = svc_by_name.get(n)
                if s is not None and s.service_id != primary_svc.service_id:
                    secondary_svcs.append(s)

            affected_svc_ids = (
                [primary_svc.service_id]
                + [s.service_id for s in secondary_svcs]
            )

            # Resolve responsible team and its users.
            team_name = bp["responsible_team"]
            team = team_by_name.get(team_name)
            if team is None:
                team = team_by_id.get(primary_svc.owner_team_id) or ns_teams[0]

            team_users = users_by_team[team.team_id]
            if not team_users:
                team_users = [u for u in self.users if u.tenant_id == tenant_id]

            # ---- Timeline ----
            month_offset = bp["timeline_month"]
            base_year = 2025 + month_offset // 12
            base_month = 1 + month_offset % 12
            day = self.rng.randint(2, 26)
            hour = self.rng.randint(6, 22)
            minute = self.rng.randint(0, 59)
            start_time = datetime(base_year, base_month, day, hour, minute)
            end_time = start_time + timedelta(hours=bp["duration_hours"])

            # ---- Triggering deployment ----
            triggering_dep_id: str | None = None
            if bp.get("triggering_deployment"):
                dep_info = bp["triggering_deployment"]
                dep_id = f"DEP-{sc}-{dep_counter:04d}"
                dep_counter += 1
                deployer = self.rng.choice(team_users)
                dep = Deployment(
                    deployment_id=dep_id,
                    tenant_id=tenant_id,
                    service_id=primary_svc.service_id,
                    deployed_by=deployer.user_id,
                    team_id=team.team_id,
                    version=dep_info["version"],
                    status=dep_info["status"],
                    deployed_at=start_time - timedelta(
                        minutes=self.rng.randint(30, 120)
                    ),
                    description=dep_info["description"],
                )
                self.deployments.append(dep)
                triggering_dep_id = dep_id

                # REL: deployment → targets → service
                rel_counter = self._add_rel(
                    rel_counter, sc, tenant_id,
                    "deployment", dep_id, "service",
                    primary_svc.service_id, "targets",
                )

            # ---- Rollback deployment (event 9 only) ----
            if bp.get("rollback_deployment"):
                rb_info = bp["rollback_deployment"]
                rb_dep_id = f"DEP-{sc}-{dep_counter:04d}"
                dep_counter += 1
                rb_deployer = self.rng.choice(team_users)
                rb_dep = Deployment(
                    deployment_id=rb_dep_id,
                    tenant_id=tenant_id,
                    service_id=primary_svc.service_id,
                    deployed_by=rb_deployer.user_id,
                    team_id=team.team_id,
                    version=rb_info["version"],
                    status=rb_info["status"],
                    deployed_at=start_time + timedelta(
                        minutes=self.rng.randint(15, 45)
                    ),
                    description=rb_info["description"],
                )
                self.deployments.append(rb_dep)

                # REL: deployment → targets → service
                rel_counter = self._add_rel(
                    rel_counter, sc, tenant_id,
                    "deployment", rb_dep_id, "service",
                    primary_svc.service_id, "targets",
                )
                # REL: event → rolled_back_by → deployment
                rel_counter = self._add_rel(
                    rel_counter, sc, tenant_id,
                    "event", event_id, "deployment",
                    rb_dep_id, "rolled_back_by",
                )

            # ---- Fixing pull request ----
            fixing_pr_id: str | None = None
            if bp.get("fixing_pr"):
                pr_info = bp["fixing_pr"]
                pr_id = f"PR-{sc}-{pr_counter:04d}"
                pr_counter += 1
                author = self.rng.choice(team_users)
                pr_created = end_time + timedelta(
                    hours=self.rng.randint(1, 12)
                )
                pr_merged = pr_created + timedelta(
                    hours=self.rng.randint(2, 24)
                )
                pr = PullRequest(
                    pull_request_id=pr_id,
                    tenant_id=tenant_id,
                    service_id=primary_svc.service_id,
                    repository=primary_svc.repository,
                    title=pr_info["title"],
                    author_id=author.user_id,
                    team_id=team.team_id,
                    status="merged",
                    created_at=pr_created,
                    merged_at=pr_merged,
                    description=pr_info["description"],
                )
                self.pull_requests.append(pr)
                fixing_pr_id = pr_id

            # ---- Customer impact ----
            n_impacted = round(
                len(ns_customers) * bp["customer_impact_pct"]
            )
            impacted = self.rng.sample(
                ns_customers, min(n_impacted, len(ns_customers))
            )
            impacted_ids = [c.customer_id for c in impacted]

            # ---- Event ----
            event = Event(
                event_id=event_id,
                tenant_id=tenant_id,
                title=bp["title"],
                event_type=bp["event_type"],
                severity=bp["severity"],
                start_time=start_time,
                end_time=end_time,
                affected_service_ids=affected_svc_ids,
                root_cause=bp["root_cause"],
                triggering_deployment_id=triggering_dep_id,
                fixing_pull_request_id=fixing_pr_id,
                impacted_customer_ids=impacted_ids,
                responsible_team_id=team.team_id,
                final_resolution=bp["final_resolution"],
                status="resolved",
            )
            self.events.append(event)

            # ---- Incident ----
            reported_at = start_time + timedelta(
                minutes=self.rng.randint(5, 30)
            )
            acknowledged_at = reported_at + timedelta(
                minutes=self.rng.randint(3, 15)
            )
            commander = self.rng.choice(team_users)
            incident = Incident(
                incident_id=inc_id,
                tenant_id=tenant_id,
                event_id=event_id,
                title=bp["incident_title"],
                severity=bp["severity"],
                status="resolved",
                reported_at=reported_at,
                acknowledged_at=acknowledged_at,
                resolved_at=end_time,
                assigned_team_id=team.team_id,
                incident_commander_id=commander.user_id,
            )
            self.incidents.append(incident)

            # ---- Relationships ----
            # event → caused_by → deployment
            if triggering_dep_id:
                rel_counter = self._add_rel(
                    rel_counter, sc, tenant_id,
                    "event", event_id, "deployment",
                    triggering_dep_id, "caused_by",
                )
            # event → resolved_by → pull_request
            if fixing_pr_id:
                rel_counter = self._add_rel(
                    rel_counter, sc, tenant_id,
                    "event", event_id, "pull_request",
                    fixing_pr_id, "resolved_by",
                )
            # event → affects → service (each)
            for sid in affected_svc_ids:
                rel_counter = self._add_rel(
                    rel_counter, sc, tenant_id,
                    "event", event_id, "service",
                    sid, "affects",
                )
            # event → owned_by → team
            rel_counter = self._add_rel(
                rel_counter, sc, tenant_id,
                "event", event_id, "team",
                team.team_id, "owned_by",
            )
            # incident → reports → event
            rel_counter = self._add_rel(
                rel_counter, sc, tenant_id,
                "incident", inc_id, "event",
                event_id, "reports",
            )
            # incident → assigned_to → team
            rel_counter = self._add_rel(
                rel_counter, sc, tenant_id,
                "incident", inc_id, "team",
                team.team_id, "assigned_to",
            )
            # pull_request → fixes → event
            if fixing_pr_id:
                rel_counter = self._add_rel(
                    rel_counter, sc, tenant_id,
                    "pull_request", fixing_pr_id, "event",
                    event_id, "fixes",
                )

    def _add_rel(
        self,
        counter: int,
        short_code: str,
        tenant_id: str,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        rel_type: str,
    ) -> int:
        """Create an EventRelationship and return the incremented counter."""
        self.event_relationships.append(
            EventRelationship(
                relationship_id=f"REL-{short_code}-{counter:04d}",
                tenant_id=tenant_id,
                source_type=source_type,
                source_id=source_id,
                target_type=target_type,
                target_id=target_id,
                relationship_type=rel_type,
            )
        )
        return counter + 1

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _random_date(self) -> datetime:
        days = self.rng.randint(0, DATE_RANGE_DAYS)
        return self._date_start + timedelta(days=days)

    def _weighted_choice(
        self, options: list[str], weights: list[float]
    ) -> str:
        """Weighted random selection using cumulative distribution."""
        r = self.rng.random()
        cumulative = 0.0
        for option, w in zip(options, weights):
            cumulative += w
            if r < cumulative:
                return option
        return options[-1]

    @staticmethod
    def _unique_email(
        first: str,
        last: str,
        domain: str,
        used: set[str],
    ) -> str:
        """Generate a unique email, appending a numeric suffix on collision."""
        # Normalise: lowercase, strip apostrophes/hyphens for email local part
        local = f"{first.lower()}.{last.lower()}".replace("'", "")
        base = f"{local}@{domain}"
        if base not in used:
            used.add(base)
            return base
        suffix = 2
        while True:
            candidate = f"{local}{suffix}@{domain}"
            if candidate not in used:
                used.add(candidate)
                return candidate
            suffix += 1

    @staticmethod
    def _check_unique_ids(
        ids: list[str], label: str, errors: list[str]
    ) -> None:
        seen: set[str] = set()
        for eid in ids:
            if eid in seen:
                errors.append(f"Duplicate {label}: {eid}")
            seen.add(eid)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def _to_dict(self) -> dict[str, list[dict[str, Any]]]:
        """Convert all entities to JSON-serialisable dicts."""
        def _serialise(obj: Any) -> dict[str, Any]:
            d = asdict(obj)
            for k, v in d.items():
                if isinstance(v, datetime):
                    d[k] = v.isoformat()
            return d

        return {
            "users": [_serialise(u) for u in self.users],
            "teams": [_serialise(t) for t in self.teams],
            "customers": [_serialise(c) for c in self.customers],
            "services": [_serialise(s) for s in self.services],
            "events": [_serialise(e) for e in self.events],
            "incidents": [_serialise(i) for i in self.incidents],
            "deployments": [_serialise(d) for d in self.deployments],
            "pull_requests": [_serialise(p) for p in self.pull_requests],
            "event_relationships": [_serialise(r) for r in self.event_relationships],
        }
