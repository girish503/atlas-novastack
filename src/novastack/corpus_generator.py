"""Corpus generator for observational enterprise source records — Milestone 4A.

Transforms NovaStack ground-truth events, incidents, deployments, and PRs into
a realistic observational natural-language corpus (~400–600 records) across 10
controlled source types, strictly preserving provenance and security metadata.

Does NOT call an LLM or external API — pure deterministic template generation.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from novastack.config import RANDOM_SEED
from novastack.models import (
    DEFAULT_AUTHORITY_BY_SOURCE_TYPE,
    Customer,
    Deployment,
    Event,
    Incident,
    PullRequest,
    RecordPermissions,
    Service,
    SourceRecord,
    Team,
    User,
)


class EventSourceCorpusGenerator:
    """Generates observational source records for the 12 canonical enterprise events."""

    def __init__(self, generator: Any, seed: int = RANDOM_SEED) -> None:
        self.gen = generator
        self.seed = seed
        self.rng = random.Random(seed)
        self.records: list[SourceRecord] = []

    def generate_corpus(self) -> list[SourceRecord]:
        """Generate the full event-related observational source corpus."""
        self.records.clear()

        # Build lookups
        tenant_id = "TENANT-NOVASTACK"
        svc_map: dict[str, Service] = {
            s.service_id: s for s in self.gen.services if s.tenant_id == tenant_id
        }
        team_map: dict[str, Team] = {
            t.team_id: t for t in self.gen.teams if t.tenant_id == tenant_id
        }
        user_map: dict[str, User] = {
            u.user_id: u for u in self.gen.users if u.tenant_id == tenant_id
        }
        cust_map: dict[str, Customer] = {
            c.customer_id: c for c in self.gen.customers if c.tenant_id == tenant_id
        }
        dep_map: dict[str, Deployment] = {
            d.deployment_id: d for d in self.gen.deployments if d.tenant_id == tenant_id
        }
        pr_map: dict[str, PullRequest] = {
            p.pull_request_id: p for p in self.gen.pull_requests if p.tenant_id == tenant_id
        }
        inc_by_event: dict[str, Incident] = {
            i.event_id: i for i in self.gen.incidents if i.tenant_id == tenant_id
        }

        # Users by team for realistic authors
        users_by_team: dict[str, list[User]] = {}
        for t in self.gen.teams:
            if t.tenant_id == tenant_id:
                members = [user_map[uid] for uid in t.member_ids if uid in user_map]
                users_by_team[t.team_id] = members or list(user_map.values())

        # 1. Generate event-specific observational records for each of the 12 events
        for event_idx, event in enumerate(self.gen.events, start=1):
            inc = inc_by_event.get(event.event_id)
            primary_svc = svc_map.get(event.affected_service_ids[0]) if event.affected_service_ids else None
            secondary_svcs = [
                svc_map[sid] for sid in event.affected_service_ids[1:] if sid in svc_map
            ]
            team = team_map.get(event.responsible_team_id)
            team_users = users_by_team.get(event.responsible_team_id, list(user_map.values()))
            commander = user_map.get(inc.incident_commander_id) if inc and inc.incident_commander_id in user_map else team_users[0]
            dep = dep_map.get(event.triggering_deployment_id) if event.triggering_deployment_id else None
            pr = pr_map.get(event.fixing_pull_request_id) if event.fixing_pull_request_id else None

            # Find rollback deployment if applicable (Event 9)
            rollback_dep = None
            if event.event_type == "rollback":
                for d in self.gen.deployments:
                    if d.tenant_id == tenant_id and d.service_id == (primary_svc.service_id if primary_svc else ""):
                        if d.deployment_id != event.triggering_deployment_id:
                            rollback_dep = d
                            break

            # --- A. Incident Records (3 per event: initial alert, triage update, resolution report) ---
            self._generate_incident_records(event, inc, primary_svc, team, commander)

            # --- B. Conversations (5 per event: triage channel, debug thread, support liaison, exec brief, retro chat) ---
            self._generate_conversations(event, inc, primary_svc, secondary_svcs, team, team_users, commander, dep, pr)

            # --- C. Engineering Notes (4-5 per event: triage diagnostic, telemetry analysis, RCA deep-dive, runbook log) ---
            self._generate_engineering_notes(event, inc, primary_svc, team, team_users, dep, pr)

            # --- D. Postmortems (2 per event: comprehensive report, action item review) ---
            self._generate_postmortems(event, inc, primary_svc, secondary_svcs, team, commander, dep, pr)

            # --- E. Support Tickets (for each impacted customer: customer complaint & resolution) ---
            self._generate_support_tickets(event, inc, primary_svc, cust_map, team_users)

            # --- F. Deployment Notes (for events with triggering/rollback deployments: release changelog & verification) ---
            if dep:
                self._generate_deployment_notes(event, dep, primary_svc, team_users)
            if rollback_dep:
                self._generate_rollback_deployment_notes(event, rollback_dep, primary_svc, team_users)

            # --- G. Pull Request Notes (for events with fixing PR: review, implementation details, test report) ---
            if pr:
                self._generate_pull_request_notes(event, pr, primary_svc, team_users)

            # --- H. Meeting Notes (2-3 per event: live triage sync, retrospective meeting, architectural follow-up) ---
            self._generate_meeting_notes(event, inc, primary_svc, team, team_users, commander)

            # --- I. Documentation (2 per event: updated operational runbook, troubleshooting guide) ---
            self._generate_documentation_records(event, primary_svc, team, commander)

        # 2. Generate Global Enterprise Policies (10 records)
        self._generate_enterprise_policies(user_map)

        return self.records

    # ------------------------------------------------------------------
    # A. Incident Records
    # ------------------------------------------------------------------
    def _generate_incident_records(
        self, event: Event, inc: Incident | None, svc: Service | None, team: Team | None, commander: User
    ) -> None:
        svc_name = svc.name if svc else "service"
        team_name = team.name if team else "Engineering"
        inc_id = inc.incident_id if inc else f"INC-AUTO-{event.event_id[-4:]}"
        t_start = event.start_time
        t_end = event.end_time or (t_start + timedelta(hours=2))
        t_mid = t_start + (t_end - t_start) / 2

        base_related = [event.event_id, inc_id]
        if svc:
            base_related.append(svc.service_id)
        if team:
            base_related.append(team.team_id)

        perms = RecordPermissions(
            allowed_roles=["engineer", "manager", "support_engineer", "sre", "director"],
            allowed_departments=["Engineering", "DevOps", "Customer Support", "Security"],
        )

        # 1. Initial Automated Declaration
        self.records.append(
            SourceRecord(
                document_id=f"DOC-INC-{inc_id}-01",
                tenant_id=event.tenant_id,
                source_type="incident",
                title=f"[SEV-{event.severity.upper()}] Initial Alert: {inc.title if inc else event.title}",
                content=(
                    f"INCIDENT DECLARATION — {inc_id}\n"
                    f"Severity: {event.severity.upper()} | Status: Active / Investigating\n"
                    f"Reported Time: {t_start.isoformat()}\n"
                    f"Affected Service: {svc_name}\n"
                    f"Incident Commander: {commander.name} ({commander.user_id})\n"
                    f"Assigned Team: {team_name}\n\n"
                    f"Trigger Alert:\n"
                    f"Automated health checks reported severe degradation on {svc_name}.\n"
                    f"Initial Symptoms: Error rate exceeded critical threshold; latency spike detected.\n"
                    f"Response: Incident war room provisioned. On-call engineers paged."
                ),
                author_id=commander.user_id,
                department=commander.department,
                created_at=t_start + timedelta(minutes=5),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=inc_id,
                source_entity_type="incident",
                related_entity_ids=base_related,
                authority_level="high",
            )
        )

        # 2. Triage & Mitigation Status Update
        self.records.append(
            SourceRecord(
                document_id=f"DOC-INC-{inc_id}-02",
                tenant_id=event.tenant_id,
                source_type="incident",
                title=f"[UPDATE] Triage & Mitigation Progress for {inc_id}",
                content=(
                    f"INCIDENT STATUS UPDATE — {inc_id}\n"
                    f"Timestamp: {t_mid.isoformat()}\n"
                    f"Status: Mitigating\n\n"
                    f"Investigation Progress:\n"
                    f"Engineering team isolated the source of failure on {svc_name}.\n"
                    f"Preliminary Finding: {event.root_cause[:140]}...\n\n"
                    f"Mitigation Actions:\n"
                    f"Remediation steps underway coordinated by {commander.name}.\n"
                    f"Traffic shaping and configuration rollouts in progress."
                ),
                author_id=commander.user_id,
                department=commander.department,
                created_at=t_mid,
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=inc_id,
                source_entity_type="incident",
                related_entity_ids=base_related,
                authority_level="high",
            )
        )

        # 3. Final All-Clear Resolution Report
        self.records.append(
            SourceRecord(
                document_id=f"DOC-INC-{inc_id}-03",
                tenant_id=event.tenant_id,
                source_type="incident",
                title=f"[RESOLVED] Final Incident Resolution: {inc_id} ({svc_name})",
                content=(
                    f"INCIDENT RESOLUTION REPORT — {inc_id}\n"
                    f"Resolved At: {t_end.isoformat()}\n"
                    f"Total Duration: {((t_end - t_start).total_seconds() / 3600):.1f} hours\n"
                    f"Status: Resolved\n\n"
                    f"Resolution Summary:\n"
                    f"{event.final_resolution}\n\n"
                    f"Impact Assessment:\n"
                    f"Service telemetry confirmed healthy. All-clear issued across monitoring dashboards.\n"
                    f"Formal postmortem scheduled within 48 hours."
                ),
                author_id=commander.user_id,
                department=commander.department,
                created_at=t_end + timedelta(minutes=10),
                updated_at=t_end + timedelta(minutes=20),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=inc_id,
                source_entity_type="incident",
                related_entity_ids=base_related,
                authority_level="high",
            )
        )

    # ------------------------------------------------------------------
    # B. Conversations (Slack / Teams Transcripts)
    # ------------------------------------------------------------------
    def _generate_conversations(
        self,
        event: Event,
        inc: Incident | None,
        svc: Service | None,
        sec_svcs: list[Service],
        team: Team | None,
        team_users: list[User],
        commander: User,
        dep: Deployment | None,
        pr: PullRequest | None,
    ) -> None:
        svc_name = svc.name if svc else "service"
        inc_id = inc.incident_id if inc else "INC-ACTIVE"
        t0 = event.start_time
        t_end = event.end_time or (t0 + timedelta(hours=2))

        base_related = [event.event_id, inc_id]
        if svc:
            base_related.append(svc.service_id)
        if dep:
            base_related.append(dep.deployment_id)
        if pr:
            base_related.append(pr.pull_request_id)

        eng1 = team_users[0]
        eng2 = team_users[1] if len(team_users) > 1 else team_users[0]
        perms = RecordPermissions(
            allowed_roles=["engineer", "sre", "manager", "support_engineer"],
            allowed_departments=["Engineering", "DevOps"],
        )

        # 1. War Room Main Channel Transcript
        channel = f"#incident-{svc_name}-{t0.strftime('%Y%m%d')}"
        self.records.append(
            SourceRecord(
                document_id=f"DOC-CHAT-{event.event_id}-01",
                tenant_id=event.tenant_id,
                source_type="conversation",
                title=f"Slack Transcript: {channel} (Incident War Room)",
                content=(
                    f"Channel: {channel}\n"
                    f"Topic: Real-time triage for {event.title}\n"
                    f"Date: {t0.strftime('%Y-%m-%d')}\n\n"
                    f"[{t0.strftime('%H:%M')}] bot-alerts: ALERT - Sev-{event.severity} threshold breached on {svc_name}.\n"
                    f"[{(t0 + timedelta(minutes=4)).strftime('%H:%M')}] {eng1.name}: Seeing elevated error rates and customer failures.\n"
                    f"[{(t0 + timedelta(minutes=8)).strftime('%H:%M')}] {commander.name}: Acknowledged. Taking incident commander. Open war room.\n"
                    f"[{(t0 + timedelta(minutes=18)).strftime('%H:%M')}] {eng2.name}: Investigating recent changes. "
                    + (f"Checking deployment {dep.deployment_id} ({dep.version}).\n" if dep else "Checking upstream network and infrastructure.\n")
                    + f"[{(t0 + timedelta(minutes=35)).strftime('%H:%M')}] {eng1.name}: Root cause confirmed: {event.root_cause[:120]}...\n"
                    f"[{(t0 + timedelta(minutes=50)).strftime('%H:%M')}] {eng2.name}: "
                    + (f"Preparing fix PR {pr.pull_request_id}.\n" if pr else "Executing operational rollback runbook.\n")
                    + f"[{t_end.strftime('%H:%M')}] {commander.name}: Verification passed. Metrics back to baseline. Resolving incident."
                ),
                author_id=commander.user_id,
                department="Engineering",
                created_at=t0 + timedelta(minutes=10),
                updated_at=t_end + timedelta(minutes=15),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="low",
            )
        )

        # 2. Engineering Investigation Debug Thread
        self.records.append(
            SourceRecord(
                document_id=f"DOC-CHAT-{event.event_id}-02",
                tenant_id=event.tenant_id,
                source_type="conversation",
                title=f"Slack Thread: {svc_name} telemetry deep-dive and log inspection",
                content=(
                    f"Thread in #eng-backend: Diagnosing {event.title}\n\n"
                    f"[{t0.strftime('%H:%M')}] {eng1.name}: Pulling metrics from Prometheus. Active thread count is abnormally elevated.\n"
                    f"[{(t0 + timedelta(minutes=12)).strftime('%H:%M')}] {eng2.name}: Correlating with latency graphs. Requests stall before dropping.\n"
                    f"[{(t0 + timedelta(minutes=22)).strftime('%H:%M')}] {eng1.name}: Look at this trace: {event.root_cause[:100]}...\n"
                    f"[{(t0 + timedelta(minutes=28)).strftime('%H:%M')}] {eng2.name}: That explains why requests queue up indefinitely.\n"
                    f"[{(t0 + timedelta(minutes=42)).strftime('%H:%M')}] {eng1.name}: We need to apply: {event.final_resolution[:120]}."
                ),
                author_id=eng1.user_id,
                department="Engineering",
                created_at=t0 + timedelta(minutes=15),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="low",
            )
        )

        # 3. Customer Support Liaison Channel
        self.records.append(
            SourceRecord(
                document_id=f"DOC-CHAT-{event.event_id}-03",
                tenant_id=event.tenant_id,
                source_type="conversation",
                title=f"Slack Transcript: #support-incident-updates ({event.title})",
                content=(
                    f"Channel: #support-incident-updates\n"
                    f"Topic: Customer communication and ticket guidance for {inc_id}\n\n"
                    f"[{(t0 + timedelta(minutes=10)).strftime('%H:%M')}] support_lead: Are we aware of issues on {svc_name}? Tickets rolling in.\n"
                    f"[{(t0 + timedelta(minutes=14)).strftime('%H:%M')}] {commander.name}: Yes, Sev-{event.severity} active under {inc_id}. Do not instruct customers to retry repeatedly.\n"
                    f"[{(t0 + timedelta(minutes=25)).strftime('%H:%M')}] support_lead: Understood. Macro applied to incoming tickets acknowledging investigation.\n"
                    f"[{t_end.strftime('%H:%M')}] {commander.name}: Issue resolved. Please inform customers service is restored."
                ),
                author_id=commander.user_id,
                department="Customer Support",
                created_at=t0 + timedelta(minutes=20),
                updated_at=t_end + timedelta(minutes=10),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="low",
            )
        )

        # 4. Executive & Cross-Department Status Briefing
        self.records.append(
            SourceRecord(
                document_id=f"DOC-CHAT-{event.event_id}-04",
                tenant_id=event.tenant_id,
                source_type="conversation",
                title=f"Slack Transcript: #incident-exec-briefing ({inc_id})",
                content=(
                    f"Channel: #incident-exec-briefing\n"
                    f"Audience: Leadership, Product, Operations\n\n"
                    f"[{(t0 + timedelta(minutes=30)).strftime('%H:%M')}] {commander.name}: Executive Brief: Sev-{event.severity} ongoing for {svc_name}.\n"
                    f"Impact: {len(event.impacted_customer_ids)} customer accounts currently affected.\n"
                    f"Technical Assessment: {event.root_cause[:130]}...\n"
                    f"Target Resolution: Fix in validation stage. Estimated restoration within 60 minutes.\n"
                    f"[{t_end.strftime('%H:%M')}] {commander.name}: Full resolution achieved. Postmortem will follow within 48h."
                ),
                author_id=commander.user_id,
                department="Engineering",
                created_at=t0 + timedelta(minutes=30),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="low",
            )
        )

        # 5. Post-Incident Retrospective Chat Thread
        self.records.append(
            SourceRecord(
                document_id=f"DOC-CHAT-{event.event_id}-05",
                tenant_id=event.tenant_id,
                source_type="conversation",
                title=f"Slack Thread: #eng-retrospectives ({event.title} follow-up)",
                content=(
                    f"Thread in #eng-retrospectives\n"
                    f"Date: {(t_end + timedelta(days=1)).strftime('%Y-%m-%d')}\n\n"
                    f"[10:15] {eng1.name}: Great work on the fast triage yesterday team.\n"
                    f"[10:18] {eng2.name}: Action items drafted in the postmortem draft. Key takeaway: automated config assertions in CI.\n"
                    f"[10:25] {commander.name}: Agreed. Let's make sure we review this in the sprint planning retro."
                ),
                author_id=eng1.user_id,
                department="Engineering",
                created_at=t_end + timedelta(days=1),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="low",
            )
        )

    # ------------------------------------------------------------------
    # C. Engineering Notes
    # ------------------------------------------------------------------
    def _generate_engineering_notes(
        self,
        event: Event,
        inc: Incident | None,
        svc: Service | None,
        team: Team | None,
        team_users: list[User],
        dep: Deployment | None,
        pr: PullRequest | None,
    ) -> None:
        svc_name = svc.name if svc else "service"
        t0 = event.start_time
        base_related = [event.event_id]
        if svc:
            base_related.append(svc.service_id)
        if dep:
            base_related.append(dep.deployment_id)
        if pr:
            base_related.append(pr.pull_request_id)

        author1 = team_users[0]
        author2 = team_users[1] if len(team_users) > 1 else team_users[0]
        perms = RecordPermissions(
            allowed_roles=["engineer", "tech_lead", "sre", "engineering_manager"],
            allowed_departments=["Engineering", "DevOps"],
        )

        # 1. Initial Triage Diagnostic Note
        self.records.append(
            SourceRecord(
                document_id=f"DOC-NOTE-{event.event_id}-01",
                tenant_id=event.tenant_id,
                source_type="engineering_note",
                title=f"Diagnostic Note: Initial error analysis for {svc_name}",
                content=(
                    f"Engineering Diagnostic Log\n"
                    f"Service: {svc_name}\n"
                    f"Author: {author1.name} ({author1.user_id})\n"
                    f"Timestamp: {(t0 + timedelta(minutes=20)).isoformat()}\n\n"
                    f"Observed Behavioral Anomalies:\n"
                    f"- Service request latency spiked 8x above 99th percentile baseline.\n"
                    f"- Downstream dependents experienced timeout errors.\n"
                    f"- Preliminary error dump reveals resource starvation: {event.root_cause[:120]}...\n\n"
                    f"Hypothesis:\n"
                    f"Recent configuration change or workload burst saturated service limits."
                ),
                author_id=author1.user_id,
                department=author1.department,
                created_at=t0 + timedelta(minutes=20),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="medium",
            )
        )

        # 2. Telemetry and Deep-Dive Metrics Analysis
        self.records.append(
            SourceRecord(
                document_id=f"DOC-NOTE-{event.event_id}-02",
                tenant_id=event.tenant_id,
                source_type="engineering_note",
                title=f"Telemetry Deep-Dive: Resource utilization and trace logs ({svc_name})",
                content=(
                    f"Telemetry Analysis: {event.title}\n"
                    f"Author: {author2.name} ({author2.user_id})\n"
                    f"Timestamp: {(t0 + timedelta(minutes=45)).isoformat()}\n\n"
                    f"Metrics Correlation:\n"
                    f"1. Memory/Connection metrics show sudden exhaustion following state change.\n"
                    f"2. Error rate correlation: 99.2% of failed requests terminate in 5xx timeouts.\n"
                    f"3. Root cause mechanism: {event.root_cause}.\n\n"
                    f"Recommended Action:\n"
                    f"Immediate remediation required: {event.final_resolution[:120]}."
                ),
                author_id=author2.user_id,
                department=author2.department,
                created_at=t0 + timedelta(minutes=45),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="medium",
            )
        )

        # 3. Architectural Mitigation & Recovery Note
        self.records.append(
            SourceRecord(
                document_id=f"DOC-NOTE-{event.event_id}-03",
                tenant_id=event.tenant_id,
                source_type="engineering_note",
                title=f"Remediation Plan: Corrective changes for {svc_name}",
                content=(
                    f"Technical Remediation Blueprint\n"
                    f"Target Service: {svc_name}\n"
                    f"Author: {author1.name}\n\n"
                    f"Remediation Specification:\n"
                    f"{event.final_resolution}\n\n"
                    f"Implementation Checklist:\n"
                    f"- Apply configuration/code update.\n"
                    f"- Validate metrics in staging environment.\n"
                    f"- Deploy patch to production under canary surveillance.\n"
                    f"- Verify zero customer-facing error resurgence."
                ),
                author_id=author1.user_id,
                department=author1.department,
                created_at=t0 + timedelta(hours=1, minutes=15),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="medium",
            )
        )

        # 4. Operational Runbook Execution Record
        self.records.append(
            SourceRecord(
                document_id=f"DOC-NOTE-{event.event_id}-04",
                tenant_id=event.tenant_id,
                source_type="engineering_note",
                title=f"Runbook Execution Log: {svc_name} mitigation runbook",
                content=(
                    f"Runbook Execution Protocol: Production Emergency Response\n"
                    f"Service: {svc_name} | Incident: {inc.incident_id if inc else 'N/A'}\n"
                    f"Operator: {author2.name}\n\n"
                    f"Execution Steps Completed:\n"
                    f"1. Verified cluster health and node availability.\n"
                    f"2. Applied mitigation parameters in accordance with emergency SOP.\n"
                    f"3. Monitored response latency: dropped back to p95 < 80ms.\n"
                    f"4. Runbook execution completed successfully without side effects."
                ),
                author_id=author2.user_id,
                department=author2.department,
                created_at=event.end_time or (t0 + timedelta(hours=2)),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="medium",
            )
        )

    # ------------------------------------------------------------------
    # D. Postmortems
    # ------------------------------------------------------------------
    def _generate_postmortems(
        self,
        event: Event,
        inc: Incident | None,
        svc: Service | None,
        sec_svcs: list[Service],
        team: Team | None,
        commander: User,
        dep: Deployment | None,
        pr: PullRequest | None,
    ) -> None:
        svc_name = svc.name if svc else "service"
        team_name = team.name if team else "Engineering"
        t0 = event.start_time
        t_end = event.end_time or (t0 + timedelta(hours=2))
        duration_h = (t_end - t0).total_seconds() / 3600

        base_related = [event.event_id]
        if inc:
            base_related.append(inc.incident_id)
        if svc:
            base_related.append(svc.service_id)
        for s in sec_svcs:
            base_related.append(s.service_id)
        if dep:
            base_related.append(dep.deployment_id)
        if pr:
            base_related.append(pr.pull_request_id)
        if team:
            base_related.append(team.team_id)

        perms = RecordPermissions(
            allowed_roles=["engineer", "manager", "director", "executive", "sre"],
            allowed_departments=["Engineering", "DevOps", "Security", "Product"],
        )
        postmortem_time = t_end + timedelta(days=2)

        # 1. Comprehensive Postmortem Report
        self.records.append(
            SourceRecord(
                document_id=f"DOC-PM-{event.event_id}-01",
                tenant_id=event.tenant_id,
                source_type="postmortem",
                title=f"Postmortem: {event.title} ({event.event_id})",
                content=(
                    f"# Postmortem Report — {event.title}\n\n"
                    f"**Event ID**: {event.event_id}\n"
                    f"**Incident ID**: {inc.incident_id if inc else 'N/A'}\n"
                    f"**Severity**: {event.severity.upper()}\n"
                    f"**Date**: {postmortem_time.strftime('%Y-%m-%d')}\n"
                    f"**Author**: {commander.name} (Incident Commander)\n"
                    f"**Responsible Team**: {team_name}\n"
                    f"**Duration**: {duration_h:.1f} hours\n"
                    f"**Impact**: {len(event.impacted_customer_ids)} customer accounts affected\n\n"
                    f"## Executive Summary\n"
                    f"On {t0.strftime('%Y-%m-%d')} between {t0.strftime('%H:%M')} and {t_end.strftime('%H:%M')} UTC, "
                    f"a {event.severity} severity event disrupted operations on {svc_name}.\n\n"
                    f"## Root Cause Analysis\n"
                    f"{event.root_cause}.\n\n"
                    f"## Triggering Factor\n"
                    + (f"Triggered by deployment {dep.deployment_id} (version {dep.version}).\n\n" if dep else "No deployment trigger; operational/environmental anomaly.\n\n")
                    + f"## Resolution & Mitigation\n"
                    f"{event.final_resolution}.\n"
                    + (f"Fixed via pull request {pr.pull_request_id}.\n\n" if pr else "Mitigated via direct operational adjustment.\n\n")
                    + f"## Lessons Learned\n"
                    f"1. Detection: Need tighter automated boundary alerts prior to total saturation.\n"
                    f"2. Prevention: Validate all configuration schemas automatically in CI before promotion."
                ),
                author_id=commander.user_id,
                department=commander.department,
                created_at=postmortem_time,
                updated_at=postmortem_time + timedelta(days=1),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="high",
            )
        )

        # 2. Action Items & Remediation Tracking Document
        self.records.append(
            SourceRecord(
                document_id=f"DOC-PM-{event.event_id}-02",
                tenant_id=event.tenant_id,
                source_type="postmortem",
                title=f"Action Items & Follow-Up Tracking: {event.title}",
                content=(
                    f"Postmortem Remediation Action Items\n"
                    f"Event Reference: {event.event_id}\n"
                    f"Tracking Owner: {team_name}\n\n"
                    f"Remediation Item Log:\n"
                    f"- AI-1: Architectural guardrails added to {svc_name} configuration.\n"
                    f"- AI-2: Alert threshold review completed; proactive alerts deployed at 80% capacity.\n"
                    f"- AI-3: Emergency runbook updated and tested in staging drill.\n"
                    f"- AI-4: Quarterly failure-mode review scheduled with architecture board."
                ),
                author_id=commander.user_id,
                department=commander.department,
                created_at=postmortem_time + timedelta(days=3),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="high",
            )
        )

    # ------------------------------------------------------------------
    # E. Support Tickets
    # ------------------------------------------------------------------
    def _generate_support_tickets(
        self, event: Event, inc: Incident | None, svc: Service | None, cust_map: dict[str, Customer], team_users: list[User]
    ) -> None:
        if not event.impacted_customer_ids:
            return  # No customer impact -> no fabricated tickets

        svc_name = svc.name if svc else "service"
        inc_id = inc.incident_id if inc else "INC-REF"
        t0 = event.start_time
        t_end = event.end_time or (t0 + timedelta(hours=2))

        perms = RecordPermissions(
            allowed_roles=["support_engineer", "support_manager", "account_executive", "customer_success"],
            allowed_departments=["Customer Support", "Sales"],
        )

        for cust_id in event.impacted_customer_ids:
            customer = cust_map.get(cust_id)
            cust_name = customer.name if customer else "Enterprise Customer"
            agent = self.rng.choice(team_users)

            # Stagger ticket submission time during outage
            offset_min = self.rng.randint(10, max(15, int((t_end - t0).total_seconds() / 60) - 10))
            ticket_created = t0 + timedelta(minutes=offset_min)
            ticket_resolved = t_end + timedelta(minutes=self.rng.randint(15, 60))

            ticket_id = f"TKT-{event.event_id[-4:]}-{cust_id[-4:]}"
            related = [event.event_id, inc_id, cust_id]
            if svc:
                related.append(svc.service_id)

            self.records.append(
                SourceRecord(
                    document_id=f"DOC-TKT-{event.event_id}-{cust_id}",
                    tenant_id=event.tenant_id,
                    source_type="support_ticket",
                    title=f"Support Ticket #{ticket_id}: Outage on {svc_name} reported by {cust_name}",
                    content=(
                        f"Ticket ID: {ticket_id}\n"
                        f"Customer: {cust_name} ({cust_id})\n"
                        f"Status: Closed | Priority: High\n"
                        f"Created: {ticket_created.isoformat()}\n"
                        f"Closed: {ticket_resolved.isoformat()}\n"
                        f"Assigned Agent: {agent.name}\n\n"
                        f"Customer Description:\n"
                        f"We are unable to complete operations on {svc_name}. "
                        f"Our systems receive error responses or requests timeout. "
                        f"This is blocking our production users.\n\n"
                        f"Agent Response:\n"
                        f"Thank you for contacting NovaStack Support. Engineering is actively mitigating "
                        f"an incident affecting {svc_name} under reference {inc_id}.\n\n"
                        f"Resolution Note:\n"
                        f"Engineering confirmed resolution. Verified with customer that service has returned to normal."
                    ),
                    author_id=agent.user_id,
                    department="Customer Support",
                    created_at=ticket_created,
                    updated_at=ticket_resolved,
                    status="published",
                    classification="internal",
                    permissions=perms,
                    source_entity_id=inc_id,
                    source_entity_type="incident",
                    related_entity_ids=related,
                    authority_level="low",
                )
            )

    # ------------------------------------------------------------------
    # F. Deployment Notes
    # ------------------------------------------------------------------
    def _generate_deployment_notes(
        self, event: Event, dep: Deployment, svc: Service | None, team_users: list[User]
    ) -> None:
        svc_name = svc.name if svc else "service"
        related = [event.event_id, dep.deployment_id]
        if svc:
            related.append(svc.service_id)

        perms = RecordPermissions(
            allowed_roles=["engineer", "devops_engineer", "sre"],
            allowed_departments=["Engineering", "DevOps"],
        )

        # 1. Release Changelog & Promotion Record
        self.records.append(
            SourceRecord(
                document_id=f"DOC-DEP-{dep.deployment_id}-01",
                tenant_id=event.tenant_id,
                source_type="deployment_note",
                title=f"Deployment Changelog: {svc_name} v{dep.version} ({dep.deployment_id})",
                content=(
                    f"Production Deployment Log — {dep.deployment_id}\n"
                    f"Service: {svc_name}\n"
                    f"Release Version: {dep.version}\n"
                    f"Deployer: {dep.deployed_by}\n"
                    f"Execution Time: {dep.deployed_at.isoformat()}\n"
                    f"Status: {dep.status}\n\n"
                    f"Changelog Summary:\n"
                    f"{dep.description}\n\n"
                    f"Verification Checklist:\n"
                    f"- CI test run passed: True\n"
                    f"- Canary rollout strategy applied: Staged (25%, 50%, 100%)\n"
                    f"- Health check status: Promoted to production"
                ),
                author_id=dep.deployed_by,
                department="DevOps",
                created_at=dep.deployed_at,
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=dep.deployment_id,
                source_entity_type="deployment",
                related_entity_ids=related,
                authority_level="high",
            )
        )

        # 2. Post-Deployment Monitoring Observation
        self.records.append(
            SourceRecord(
                document_id=f"DOC-DEP-{dep.deployment_id}-02",
                tenant_id=event.tenant_id,
                source_type="deployment_note",
                title=f"Deployment Telemetry Audit: Post-rollout health of {svc_name} v{dep.version}",
                content=(
                    f"Deployment Telemetry Audit\n"
                    f"Deployment ID: {dep.deployment_id}\n"
                    f"Service: {svc_name} v{dep.version}\n"
                    f"Timestamp: {(dep.deployed_at + timedelta(minutes=25)).isoformat()}\n\n"
                    f"Observations:\n"
                    f"Telemetry following deployment {dep.deployment_id} showed metrics shift leading up to {event.event_id}.\n"
                    f"Audit note: Parameters deployed in this release were identified as the catalyst for the subsequent event."
                ),
                author_id=dep.deployed_by,
                department="DevOps",
                created_at=dep.deployed_at + timedelta(minutes=25),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=dep.deployment_id,
                source_entity_type="deployment",
                related_entity_ids=related,
                authority_level="high",
            )
        )

    def _generate_rollback_deployment_notes(
        self, event: Event, rb_dep: Deployment, svc: Service | None, team_users: list[User]
    ) -> None:
        svc_name = svc.name if svc else "service"
        related = [event.event_id, rb_dep.deployment_id]
        if svc:
            related.append(svc.service_id)

        perms = RecordPermissions(
            allowed_roles=["engineer", "devops_engineer", "sre"],
            allowed_departments=["Engineering", "DevOps"],
        )

        self.records.append(
            SourceRecord(
                document_id=f"DOC-DEP-{rb_dep.deployment_id}-ROLLBACK",
                tenant_id=event.tenant_id,
                source_type="deployment_note",
                title=f"Emergency Rollback Log: {svc_name} to v{rb_dep.version} ({rb_dep.deployment_id})",
                content=(
                    f"EMERGENCY ROLLBACK DEPLOYMENT — {rb_dep.deployment_id}\n"
                    f"Service: {svc_name}\n"
                    f"Target Version: {rb_dep.version}\n"
                    f"Authorized By: {rb_dep.deployed_by}\n"
                    f"Executed At: {rb_dep.deployed_at.isoformat()}\n"
                    f"Status: {rb_dep.status}\n\n"
                    f"Rollback Rationale:\n"
                    f"{rb_dep.description}\n\n"
                    f"Rollback Verification:\n"
                    f"Traffic reverted to previous stable artifact. Endpoint stability restored."
                ),
                author_id=rb_dep.deployed_by,
                department="DevOps",
                created_at=rb_dep.deployed_at,
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=rb_dep.deployment_id,
                source_entity_type="deployment",
                related_entity_ids=related,
                authority_level="high",
            )
        )

    # ------------------------------------------------------------------
    # G. Pull Request Notes
    # ------------------------------------------------------------------
    def _generate_pull_request_notes(
        self, event: Event, pr: PullRequest, svc: Service | None, team_users: list[User]
    ) -> None:
        svc_name = svc.name if svc else "service"
        related = [event.event_id, pr.pull_request_id]
        if svc:
            related.append(svc.service_id)

        reviewer = team_users[1] if len(team_users) > 1 else team_users[0]
        perms = RecordPermissions(
            allowed_roles=["engineer", "tech_lead"],
            allowed_departments=["Engineering"],
        )

        # 1. PR Description & Implementation
        self.records.append(
            SourceRecord(
                document_id=f"DOC-PR-{pr.pull_request_id}-01",
                tenant_id=event.tenant_id,
                source_type="pull_request_note",
                title=f"PR Description: {pr.title} ({pr.pull_request_id})",
                content=(
                    f"Pull Request {pr.pull_request_id} — {pr.title}\n"
                    f"Repository: {pr.repository}\n"
                    f"Author: {pr.author_id}\n"
                    f"Status: {pr.status}\n"
                    f"Created: {pr.created_at.isoformat()}\n\n"
                    f"Problem Statement:\n"
                    f"Fixes operational outage observed under {event.event_id}.\n\n"
                    f"Technical Implementation Details:\n"
                    f"{pr.description}\n\n"
                    f"Root Cause Mitigation:\n"
                    f"{event.final_resolution}"
                ),
                author_id=pr.author_id,
                department="Engineering",
                created_at=pr.created_at,
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=pr.pull_request_id,
                source_entity_type="pull_request",
                related_entity_ids=related,
                authority_level="medium",
            )
        )

        # 2. PR Code Review & Approval Discussion
        self.records.append(
            SourceRecord(
                document_id=f"DOC-PR-{pr.pull_request_id}-02",
                tenant_id=event.tenant_id,
                source_type="pull_request_note",
                title=f"PR Code Review: Peer review and sign-off on {pr.pull_request_id}",
                content=(
                    f"Code Review Thread for PR {pr.pull_request_id}\n"
                    f"Reviewer: {reviewer.name} ({reviewer.user_id})\n\n"
                    f"Review Comments:\n"
                    f"- LGTM. Verified connection/configuration bounds prevent regression.\n"
                    f"- Checked integration test coverage for failure edge cases.\n"
                    f"- Approved for immediate merge into main branch."
                ),
                author_id=reviewer.user_id,
                department="Engineering",
                created_at=pr.created_at + timedelta(hours=1),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=pr.pull_request_id,
                source_entity_type="pull_request",
                related_entity_ids=related,
                authority_level="medium",
            )
        )

        # 3. Automated Test Verification Report
        self.records.append(
            SourceRecord(
                document_id=f"DOC-PR-{pr.pull_request_id}-03",
                tenant_id=event.tenant_id,
                source_type="pull_request_note",
                title=f"CI/CD Verification Report for {pr.pull_request_id}",
                content=(
                    f"CI/CD Pipeline Build Report\n"
                    f"PR ID: {pr.pull_request_id}\n"
                    f"Target Repo: {pr.repository}\n"
                    f"Completed At: {pr.merged_at.isoformat() if pr.merged_at else pr.created_at.isoformat()}\n\n"
                    f"Test Results:\n"
                    f"- Unit Test Suite: 100% Passed\n"
                    f"- Boundary Condition Assertions: Passed\n"
                    f"- SonarQube Quality Gate: Passed (0 Vulnerabilities, 0 Bugs)\n"
                    f"- Artifact built and signed for release."
                ),
                author_id="system",
                department="DevOps",
                created_at=pr.merged_at if pr.merged_at else pr.created_at,
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=pr.pull_request_id,
                source_entity_type="pull_request",
                related_entity_ids=related,
                authority_level="medium",
            )
        )

    # ------------------------------------------------------------------
    # H. Meeting Notes
    # ------------------------------------------------------------------
    def _generate_meeting_notes(
        self,
        event: Event,
        inc: Incident | None,
        svc: Service | None,
        team: Team | None,
        team_users: list[User],
        commander: User,
    ) -> None:
        svc_name = svc.name if svc else "service"
        team_name = team.name if team else "Engineering"
        t0 = event.start_time
        t_end = event.end_time or (t0 + timedelta(hours=2))

        base_related = [event.event_id]
        if inc:
            base_related.append(inc.incident_id)
        if svc:
            base_related.append(svc.service_id)
        if team:
            base_related.append(team.team_id)

        perms = RecordPermissions(
            allowed_roles=["engineer", "manager", "tech_lead"],
            allowed_departments=["Engineering", "DevOps"],
        )

        # 1. Live Triage Call Sync
        self.records.append(
            SourceRecord(
                document_id=f"DOC-MTG-{event.event_id}-01",
                tenant_id=event.tenant_id,
                source_type="meeting",
                title=f"Meeting Minutes: Incident Triage Sync for {event.title}",
                content=(
                    f"Meeting Minutes: Emergency Triage Call\n"
                    f"Incident Reference: {inc.incident_id if inc else event.event_id}\n"
                    f"Time: {(t0 + timedelta(minutes=30)).isoformat()}\n"
                    f"Lead: {commander.name}\n\n"
                    f"Attendees: Team Leads from {team_name}, SRE on-call, Customer Support lead.\n\n"
                    f"Key Discussion Points:\n"
                    f"1. Current service health: {svc_name} under heavy degradation.\n"
                    f"2. Agreed hypothesis: {event.root_cause[:120]}.\n"
                    f"3. Delegation: Engineering focuses on patch deployment; Support handles customer communications."
                ),
                author_id=commander.user_id,
                department="Engineering",
                created_at=t0 + timedelta(minutes=30),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="medium",
            )
        )

        # 2. Post-Incident Retrospective Sync
        self.records.append(
            SourceRecord(
                document_id=f"DOC-MTG-{event.event_id}-02",
                tenant_id=event.tenant_id,
                source_type="meeting",
                title=f"Meeting Minutes: Post-Incident Retrospective ({event.title})",
                content=(
                    f"Meeting Minutes: Operational Retrospective\n"
                    f"Event: {event.event_id} — {event.title}\n"
                    f"Held: {(t_end + timedelta(days=2)).strftime('%Y-%m-%d')}\n"
                    f"Host: {team_name}\n\n"
                    f"Agenda:\n"
                    f"- Chronology review from alert to resolution.\n"
                    f"- What went well: Rapid declaration and team coordination.\n"
                    f"- Where we failed: Lack of early telemetry detection.\n"
                    f"- Action Item Assignees: Engineering assigned to complete CI guardrails."
                ),
                author_id=commander.user_id,
                department="Engineering",
                created_at=t_end + timedelta(days=2),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=event.event_id,
                source_entity_type="event",
                related_entity_ids=base_related,
                authority_level="medium",
            )
        )

    # ------------------------------------------------------------------
    # I. Documentation Records
    # ------------------------------------------------------------------
    def _generate_documentation_records(
        self, event: Event, svc: Service | None, team: Team | None, commander: User
    ) -> None:
        svc_name = svc.name if svc else "service"
        related = [event.event_id]
        if svc:
            related.append(svc.service_id)
        if team:
            related.append(team.team_id)

        perms = RecordPermissions(
            allowed_roles=["engineer", "support_engineer", "sre", "product_manager"],
            allowed_departments=["Engineering", "DevOps", "Customer Support", "Product"],
        )
        doc_date = (event.end_time or event.start_time) + timedelta(days=5)

        # 1. Operational Service Runbook
        self.records.append(
            SourceRecord(
                document_id=f"DOC-DOC-{event.event_id}-01",
                tenant_id=event.tenant_id,
                source_type="documentation",
                title=f"Runbook: {svc_name} Operational Runbook & Recovery Procedures",
                content=(
                    f"# Operational Runbook — {svc_name}\n\n"
                    f"## Service Overview\n"
                    f"{svc.description if svc else 'Core platform service.'}\n\n"
                    f"## Known Failure Signatures (Updated post-{event.event_id})\n"
                    f"- **Symptom**: High request latency and 504 gateway timeouts.\n"
                    f"- **Underlying Cause**: {event.root_cause}.\n\n"
                    f"## Standard Operating Procedures\n"
                    f"1. Check connection pool & resource utilization alarms.\n"
                    f"2. Mitigation procedure: {event.final_resolution}.\n"
                    f"3. Escalate to team if recovery does not settle within 15 minutes."
                ),
                author_id=commander.user_id,
                department="Engineering",
                created_at=doc_date,
                updated_at=doc_date + timedelta(days=10),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=svc.service_id if svc else event.event_id,
                source_entity_type="service" if svc else "event",
                related_entity_ids=related,
                authority_level="high",
            )
        )

        # 2. Architecture FAQ & Troubleshooting Guide
        self.records.append(
            SourceRecord(
                document_id=f"DOC-DOC-{event.event_id}-02",
                tenant_id=event.tenant_id,
                source_type="documentation",
                title=f"Troubleshooting Guide: {svc_name} failure modes and diagnostics",
                content=(
                    f"# Troubleshooting Guide — {svc_name}\n\n"
                    f"## Common Diagnostic Workflows\n"
                    f"When experiencing anomalies similar to {event.title} ({event.event_id}):\n\n"
                    f"### Diagnostic Steps\n"
                    f"1. Inspect error rate logs for pattern matching.\n"
                    f"2. Confirm whether recent releases or config changes were pushed.\n"
                    f"3. Validate parameter limits against documented maximum capacities.\n\n"
                    f"### Key Safeguards\n"
                    f"Ensure limits comply with enterprise resilience benchmarks."
                ),
                author_id=commander.user_id,
                department="Engineering",
                created_at=doc_date + timedelta(days=2),
                status="published",
                classification="internal",
                permissions=perms,
                source_entity_id=svc.service_id if svc else event.event_id,
                source_entity_type="service" if svc else "event",
                related_entity_ids=related,
                authority_level="high",
            )
        )

    # ------------------------------------------------------------------
    # J. Global Enterprise Policies (10 policies)
    # ------------------------------------------------------------------
    def _generate_enterprise_policies(self, user_map: dict[str, User]) -> None:
        policy_author = next(iter(user_map.values()))
        policy_perms = RecordPermissions()  # Company-wide

        policies_def = [
            (
                "DOC-POL-0001",
                "Production Incident Severity Classification & Escalation Policy",
                (
                    "# NovaStack Policy: Incident Severity Classification\n\n"
                    "## Severity Matrix\n"
                    "- **Sev-1 (Critical)**: Outages directly impacting customer transactions or critical path services.\n"
                    "- **Sev-2 (High)**: Major degradation with significant customer impact or partial system unavailability.\n"
                    "- **Sev-3 (Medium)**: Non-critical feature degradation with workaround available.\n"
                    "- **Sev-4 (Low)**: Minor cosmetic or internal administrative issues.\n\n"
                    "## Escalation Mandate\n"
                    "Sev-1 and Sev-2 incidents require immediate war room convening and leadership notification within 15 minutes."
                ),
                "Security",
            ),
            (
                "DOC-POL-0002",
                "Production Deployment & Canary Verification Policy",
                (
                    "# NovaStack Policy: Production Deployments\n\n"
                    "## Staged Rollout Guidelines\n"
                    "1. All production deployments must undergo canary phase at 10% traffic for minimum 30 minutes.\n"
                    "2. Automated rollback must trigger if error rate exceeds 0.5% during canary phase.\n"
                    "3. Deployments must be documented with changelog references prior to promotion."
                ),
                "DevOps",
            ),
            (
                "DOC-POL-0003",
                "PCI-DSS Credential Rotation & Secret Management Policy",
                (
                    "# NovaStack Policy: Credential Rotation & Secret Hygiene\n\n"
                    "## Standards\n"
                    "1. Production payment credentials and API tokens must rotate every 90 days.\n"
                    "2. Endpoint configurations must be validated against production schemas before rotation.\n"
                    "3. Plaintext credentials are strictly prohibited in code repositories and environment dumps."
                ),
                "Security",
            ),
            (
                "DOC-POL-0004",
                "Multi-Region Disaster Recovery & Regional Failover Policy",
                (
                    "# NovaStack Policy: Disaster Recovery & Continuity\n\n"
                    "## Regional Availability\n"
                    "1. Tier-1 services must maintain standby failover capability in an alternate cloud region.\n"
                    "2. Regional failover drills must be executed bi-annually.\n"
                    "3. RPO (Recovery Point Objective) is 1 minute; RTO (Recovery Time Objective) is 15 minutes."
                ),
                "Infrastructure",
            ),
            (
                "DOC-POL-0005",
                "Customer SLA Commitments & Outage Communication Policy",
                (
                    "# NovaStack Policy: Customer SLA & Incident Transparency\n\n"
                    "## SLA Tiers\n"
                    "- Enterprise Tier: 99.95% monthly uptime.\n"
                    "- Growth Tier: 99.9% monthly uptime.\n\n"
                    "## Communication Rules\n"
                    "In the event of customer-impacting outages, public status updates must be posted within 20 minutes."
                ),
                "Customer Support",
            ),
            (
                "DOC-POL-0006",
                "Database Migration & Schema Change Governance",
                (
                    "# NovaStack Policy: Database Schema Evolution\n\n"
                    "## Rules for Zero-Downtime Migrations\n"
                    "1. Adding NOT NULL columns without default values on large tables is strictly forbidden.\n"
                    "2. DDL migrations must execute with lock timeouts under 5 seconds.\n"
                    "3. Large backfills must run in asynchronous background batches."
                ),
                "Engineering",
            ),
            (
                "DOC-POL-0007",
                "API Rate Limiting & Tiered Quota Policy",
                (
                    "# NovaStack Policy: API Quotas & Throttling\n\n"
                    "## Scoping Standard\n"
                    "1. Rate limits must always be evaluated on a per-customer token basis, never globally.\n"
                    "2. Rate limit thresholds must provide HTTP 429 headers with Retry-After directives.\n"
                    "3. Configuration updates must validate scoping parameters in staging."
                ),
                "Platform",
            ),
            (
                "DOC-POL-0008",
                "Distributed Caching, Namespaces & Invalidation Policy",
                (
                    "# NovaStack Policy: Caching Architecture Guidelines\n\n"
                    "## Requirements\n"
                    "1. Shared cache clusters must enforce distinct, isolated key prefixes per domain.\n"
                    "2. Eviction policies must distinguish volatile session tokens from permanent feature flags.\n"
                    "3. Bulk invalidations must be rate-limited to avoid thundering herd origin storms."
                ),
                "Platform",
            ),
            (
                "DOC-POL-0009",
                "Enterprise Data Retention & Audit Logging Policy",
                (
                    "# NovaStack Policy: Data Retention & Compliance Logging\n\n"
                    "## Retention Schedules\n"
                    "1. Audit trails and access logs must be retained for 365 days in immutable storage.\n"
                    "2. Incident records and postmortems must be retained indefinitely.\n"
                    "3. Temporary debug logs must be pruned after 30 days."
                ),
                "Security",
            ),
            (
                "DOC-POL-0010",
                "Information Security Incident Response Plan (IRP)",
                (
                    "# NovaStack Policy: Information Security Incident Response\n\n"
                    "## Protocol\n"
                    "1. Any potential unauthorized access, misconfigured relay, or leak requires Security team engagement within 10 minutes.\n"
                    "2. Forensic logs must be preserved immediately before applying configuration changes.\n"
                    "3. Breach assessment must conclude within 72 hours per regulatory requirements."
                ),
                "Security",
            ),
        ]

        for doc_id, title, content, dept in policies_def:
            self.records.append(
                SourceRecord(
                    document_id=doc_id,
                    tenant_id="TENANT-NOVASTACK",
                    source_type="policy",
                    title=title,
                    content=content,
                    author_id=policy_author.user_id,
                    department=dept,
                    created_at=datetime(2024, 6, 1),
                    updated_at=datetime(2025, 1, 15),
                    valid_from=datetime(2024, 6, 1),
                    valid_until=datetime(2027, 1, 1),
                    version="2.0",
                    status="published",
                    classification="internal",
                    permissions=policy_perms,
                    source_entity_id=None,
                    source_entity_type="policy",
                    related_entity_ids=[],
                    authority_level="authoritative",
                )
            )


def generate_combined_corpus(
    generator: Any,
    seed: int = RANDOM_SEED,
    include_noise: bool = True,
    include_security: bool = False,
    include_adversarial: bool = False,
) -> list[SourceRecord]:
    """Generate the full combined observational corpus (Milestones 4A, 4B, 4C, 4D-1, and 4D-2).

    Combines:
    - 433 event-related records from the 12 canonical events (Milestone 4A)
    - 470 background enterprise records across 10 departments (Milestone 4B)
    - 290 temporal noise, duplicate, version, and conflict records (Milestone 4C, if include_noise=True)
    - 110 security and authorization test records (Milestone 4D-1, if include_security=True)
    - 90 adversarial and prompt injection records (Milestone 4D-2, if include_adversarial=True)

    Returns up to 1,393 total records with all components enabled.
    """
    from novastack.adversarial_corpus import AdversarialCorpusGenerator
    from novastack.background_corpus import BackgroundCorpusGenerator
    from novastack.noise_generator import TemporalNoiseGenerator
    from novastack.security_corpus import SecurityCorpusGenerator

    event_gen = EventSourceCorpusGenerator(generator, seed=seed)
    event_records = event_gen.generate_corpus()

    bkg_gen = BackgroundCorpusGenerator(generator, seed=seed)
    bkg_records = bkg_gen.generate_corpus()

    combined = event_records + bkg_records
    if include_noise:
        noise_gen = TemporalNoiseGenerator(generator, combined, seed=seed)
        noise_records = noise_gen.generate_noise_records()
        combined = combined + noise_records

    if include_security:
        sec_gen = SecurityCorpusGenerator(generator, seed=seed)
        sec_records = sec_gen.generate_corpus()
        combined = combined + sec_records

    if include_adversarial:
        adv_gen = AdversarialCorpusGenerator(generator, seed=seed)
        adv_records = adv_gen.generate_corpus()
        combined = combined + adv_records

    return combined
