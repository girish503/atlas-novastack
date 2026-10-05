"""Lightweight deterministic rendering architecture for observational source records.

Transforms structured ground truth (events, incidents, deployments, PRs, services, teams)
into human-facing observational source records (incident reports, postmortems, Slack transcripts,
engineering notes, support tickets, deployment notes, etc.) with preserved provenance.

No LLMs or external dependencies — pure deterministic templates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from novastack.models import (
    AUTHORITY_LEVELS,
    CLASSIFICATION_LEVELS,
    DEFAULT_AUTHORITY_BY_SOURCE_TYPE,
    RECORD_STATUSES,
    SOURCE_TYPES,
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


@dataclass
class RenderContext:
    """Structured ground-truth context provided to a source-record renderer."""

    event: Event
    incident: Incident | None = None
    triggering_deployment: Deployment | None = None
    fixing_pull_request: PullRequest | None = None
    rollback_deployment: Deployment | None = None
    primary_service: Service | None = None
    secondary_services: list[Service] = field(default_factory=list)
    responsible_team: Team | None = None
    author: User | None = None
    customer: Customer | None = None
    doc_id: str | None = None
    created_at: datetime | None = None
    status: str = "published"
    classification: str = "internal"
    permissions: RecordPermissions | None = None
    authority_level: str | None = None
    version: str = "1.0"
    parent_id: str | None = None
    supersedes_id: str | None = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)


class BaseRecordRenderer(ABC):
    """Abstract base renderer for an observational source type."""

    source_type: str

    @abstractmethod
    def render(self, ctx: RenderContext) -> SourceRecord:
        """Render a deterministic SourceRecord from the provided ground-truth context."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 1. Incident Record Renderer
# ---------------------------------------------------------------------------

class IncidentRecordRenderer(BaseRecordRenderer):
    source_type = "incident"

    def render(self, ctx: RenderContext) -> SourceRecord:
        inc = ctx.incident
        ev = ctx.event
        svc_name = ctx.primary_service.name if ctx.primary_service else "service"
        team_name = ctx.responsible_team.name if ctx.responsible_team else "Engineering"
        author_id = ctx.author.user_id if ctx.author else (inc.incident_commander_id if inc else "system")
        dept = ctx.author.department if ctx.author else (ctx.responsible_team.department if ctx.responsible_team else "Engineering")

        inc_id = inc.incident_id if inc else "INC-UNKNOWN"
        created = inc.reported_at if inc else ev.start_time
        resolved_str = inc.resolved_at.isoformat() if (inc and inc.resolved_at) else "Ongoing"

        title = f"[INCIDENT] {inc_id}: {inc.title if inc else ev.title}"
        content = (
            f"Incident ID: {inc_id}\n"
            f"Event Reference: {ev.event_id}\n"
            f"Severity: {ev.severity.upper()}\n"
            f"Status: {inc.status.title() if inc else 'Resolved'}\n"
            f"Primary Affected Service: {svc_name}\n"
            f"Assigned Team: {team_name}\n"
            f"Reported At: {created.isoformat()}\n"
            f"Resolved At: {resolved_str}\n\n"
            f"Incident Summary:\n"
            f"{inc.title if inc else ev.title}\n\n"
            f"Initial Symptoms:\n"
            f"Automated alerts detected degraded performance on {svc_name}.\n\n"
            f"Triage & Mitigation:\n"
            f"Incident commander coordinated mitigation with {team_name}.\n"
            f"Resolution: {ev.final_resolution}"
        )

        related = [ev.event_id]
        if ctx.primary_service:
            related.append(ctx.primary_service.service_id)
        if ctx.triggering_deployment:
            related.append(ctx.triggering_deployment.deployment_id)
        if ctx.fixing_pull_request:
            related.append(ctx.fixing_pull_request.pull_request_id)
        if ctx.responsible_team:
            related.append(ctx.responsible_team.team_id)

        auth_level = ctx.authority_level or DEFAULT_AUTHORITY_BY_SOURCE_TYPE.get(self.source_type, "high")
        perms = ctx.permissions or RecordPermissions(
            allowed_roles=["engineer", "manager", "support_engineer", "sre"],
            allowed_departments=["Engineering", "DevOps", "Customer Support", "Security"],
        )

        doc_id = ctx.doc_id or f"DOC-INC-{inc_id}"

        return SourceRecord(
            document_id=doc_id,
            tenant_id=ev.tenant_id,
            source_type=self.source_type,
            title=title,
            content=content,
            author_id=author_id,
            department=dept,
            created_at=created,
            updated_at=inc.resolved_at if inc else ev.end_time,
            status=ctx.status,
            classification=ctx.classification,
            permissions=perms,
            source_entity_id=inc_id,
            source_entity_type="incident",
            related_entity_ids=related,
            authority_level=auth_level,
            version=ctx.version,
        )


# ---------------------------------------------------------------------------
# 2. Postmortem Record Renderer
# ---------------------------------------------------------------------------

class PostmortemRecordRenderer(BaseRecordRenderer):
    source_type = "postmortem"

    def render(self, ctx: RenderContext) -> SourceRecord:
        ev = ctx.event
        inc = ctx.incident
        svc_name = ctx.primary_service.name if ctx.primary_service else "service"
        team_name = ctx.responsible_team.name if ctx.responsible_team else "Engineering"
        author_id = ctx.author.user_id if ctx.author else (inc.incident_commander_id if inc else "USR-UNKNOWN")
        dept = ctx.author.department if ctx.author else (ctx.responsible_team.department if ctx.responsible_team else "Engineering")

        postmortem_date = (ev.end_time or ev.start_time) + timedelta(days=2)
        title = f"Postmortem: {ev.title} ({ev.event_id})"

        dep_str = f"Triggering Deployment: {ctx.triggering_deployment.deployment_id} (version {ctx.triggering_deployment.version})" if ctx.triggering_deployment else "Triggering Deployment: None (operational/infrastructure event)"
        pr_str = f"Fixing Pull Request: {ctx.fixing_pull_request.pull_request_id} ({ctx.fixing_pull_request.title})" if ctx.fixing_pull_request else "Fixing Pull Request: None (resolved via operational intervention)"
        duration_hours = (ev.end_time - ev.start_time).total_seconds() / 3600 if ev.end_time else 0.0

        content = (
            f"# Postmortem Report — {ev.title}\n\n"
            f"**Event ID**: {ev.event_id}\n"
            f"**Incident ID**: {inc.incident_id if inc else 'N/A'}\n"
            f"**Responsible Team**: {team_name}\n"
            f"**Date**: {postmortem_date.strftime('%Y-%m-%d')}\n"
            f"**Severity**: {ev.severity.upper()}\n"
            f"**Duration**: {duration_hours:.1f} hours\n\n"
            f"## Executive Summary\n"
            f"Between {ev.start_time.isoformat()} and {(ev.end_time or ev.start_time).isoformat()} UTC, "
            f"{svc_name} experienced a {ev.severity} {ev.event_type}. "
            f"A total of {len(ev.impacted_customer_ids)} customer accounts were impacted.\n\n"
            f"## Root Cause Analysis\n"
            f"{ev.root_cause}.\n\n"
            f"## Contributing Factors\n"
            f"- {dep_str}\n"
            f"- Inadequate monitoring thresholds prior to incident\n\n"
            f"## Resolution & Recovery\n"
            f"{ev.final_resolution}.\n"
            f"- {pr_str}\n\n"
            f"## Action Items\n"
            f"1. [PREVENT] Add automated regression validation in CI pipeline.\n"
            f"2. [DETECT] Configure proactive alerting before complete failure occurs.\n"
            f"3. [RESPOND] Update service runbook with fast-recovery steps."
        )

        related = [ev.event_id]
        if inc:
            related.append(inc.incident_id)
        if ctx.primary_service:
            related.append(ctx.primary_service.service_id)
        for s in ctx.secondary_services:
            related.append(s.service_id)
        if ctx.triggering_deployment:
            related.append(ctx.triggering_deployment.deployment_id)
        if ctx.fixing_pull_request:
            related.append(ctx.fixing_pull_request.pull_request_id)
        if ctx.responsible_team:
            related.append(ctx.responsible_team.team_id)

        auth_level = ctx.authority_level or DEFAULT_AUTHORITY_BY_SOURCE_TYPE.get(self.source_type, "high")
        perms = ctx.permissions or RecordPermissions(
            allowed_roles=["engineer", "manager", "director", "executive", "sre"],
            allowed_departments=["Engineering", "DevOps", "Security", "Product"],
        )

        doc_id = ctx.doc_id or f"DOC-POSTMORTEM-{ev.event_id}"

        return SourceRecord(
            document_id=doc_id,
            tenant_id=ev.tenant_id,
            source_type=self.source_type,
            title=title,
            content=content,
            author_id=author_id,
            department=dept,
            created_at=postmortem_date,
            updated_at=postmortem_date + timedelta(days=1),
            status=ctx.status,
            classification=ctx.classification,
            permissions=perms,
            source_entity_id=ev.event_id,
            source_entity_type="event",
            related_entity_ids=related,
            authority_level=auth_level,
            version=ctx.version,
        )


# ---------------------------------------------------------------------------
# 3. Conversation (Slack / Chat Transcript) Renderer
# ---------------------------------------------------------------------------

class ConversationRecordRenderer(BaseRecordRenderer):
    source_type = "conversation"

    def render(self, ctx: RenderContext) -> SourceRecord:
        ev = ctx.event
        inc = ctx.incident
        svc_name = ctx.primary_service.name if ctx.primary_service else "service"
        author_id = ctx.author.user_id if ctx.author else "USR-UNKNOWN"
        dept = ctx.author.department if ctx.author else "Engineering"

        t0 = ev.start_time
        t1 = t0 + timedelta(minutes=5)
        t2 = t0 + timedelta(minutes=15)
        t3 = t0 + timedelta(minutes=35)
        t4 = ev.end_time or (t0 + timedelta(hours=1))

        channel = f"#incident-{svc_name}-{t0.strftime('%Y%m%d')}"
        title = f"Slack Transcript: {channel}"

        dep_mention = f"checking deployment {ctx.triggering_deployment.deployment_id}" if ctx.triggering_deployment else "checking cloud infrastructure health"
        fix_mention = f"deploying fix in PR {ctx.fixing_pull_request.pull_request_id}" if ctx.fixing_pull_request else "applying mitigation commands"

        content = (
            f"Channel: {channel}\n"
            f"Topic: Real-time triage for {ev.title}\n"
            f"Date: {t0.strftime('%Y-%m-%d')}\n\n"
            f"[{t0.strftime('%H:%M')}] bot-alerts: ALERT - High error rate detected on {svc_name}.\n"
            f"[{t1.strftime('%H:%M')}] oncall_eng: Seeing errors spike on {svc_name}. Looking into it now.\n"
            f"[{t2.strftime('%H:%M')}] inc_commander: Incident declared ({inc.incident_id if inc else ev.event_id}). We are {dep_mention}.\n"
            f"[{t3.strftime('%H:%M')}] oncall_eng: Root cause looks like: {ev.root_cause[:120]}...\n"
            f"[{t3.strftime('%H:%M')}] lead_eng: Confirmed. Working on {fix_mention}.\n"
            f"[{t4.strftime('%H:%M')}] inc_commander: Fix is live. Metrics back to baseline. All clear."
        )

        related = [ev.event_id]
        if inc:
            related.append(inc.incident_id)
        if ctx.primary_service:
            related.append(ctx.primary_service.service_id)
        if ctx.triggering_deployment:
            related.append(ctx.triggering_deployment.deployment_id)
        if ctx.fixing_pull_request:
            related.append(ctx.fixing_pull_request.pull_request_id)

        auth_level = ctx.authority_level or DEFAULT_AUTHORITY_BY_SOURCE_TYPE.get(self.source_type, "low")
        perms = ctx.permissions or RecordPermissions(
            allowed_roles=["engineer", "sre", "manager", "support_engineer"],
            allowed_departments=["Engineering", "DevOps"],
        )

        doc_id = ctx.doc_id or f"DOC-CHAT-{ev.event_id}"

        return SourceRecord(
            document_id=doc_id,
            tenant_id=ev.tenant_id,
            source_type=self.source_type,
            title=title,
            content=content,
            author_id=author_id,
            department=dept,
            created_at=t0,
            updated_at=t4,
            status=ctx.status,
            classification=ctx.classification,
            permissions=perms,
            source_entity_id=ev.event_id,
            source_entity_type="event",
            related_entity_ids=related,
            authority_level=auth_level,
            version=ctx.version,
        )


# ---------------------------------------------------------------------------
# 4. Engineering Note Renderer
# ---------------------------------------------------------------------------

class EngineeringNoteRenderer(BaseRecordRenderer):
    source_type = "engineering_note"

    def render(self, ctx: RenderContext) -> SourceRecord:
        ev = ctx.event
        svc_name = ctx.primary_service.name if ctx.primary_service else "service"
        author_id = ctx.author.user_id if ctx.author else "USR-UNKNOWN"
        dept = ctx.author.department if ctx.author else "Engineering"

        title = f"Engineering Investigation Notes: {svc_name} incident"
        content = (
            f"Document: Engineering Investigation Note\n"
            f"Service: {svc_name}\n"
            f"Author: {author_id}\n"
            f"Timestamp: {ev.start_time.isoformat()}\n\n"
            f"Problem Description:\n"
            f"During peak traffic, {svc_name} exhibited unexpected behavior under load.\n\n"
            f"Technical Hypothesis & Diagnostics:\n"
            f"- Analyzed metrics and stack traces.\n"
            f"- Identified symptom: {ev.root_cause}.\n\n"
            f"Remediation Plan:\n"
            f"{ev.final_resolution}\n\n"
            f"Follow-up Notes:\n"
            f"Ensure team reviews configuration best practices."
        )

        related = [ev.event_id]
        if ctx.primary_service:
            related.append(ctx.primary_service.service_id)
        if ctx.fixing_pull_request:
            related.append(ctx.fixing_pull_request.pull_request_id)

        auth_level = ctx.authority_level or DEFAULT_AUTHORITY_BY_SOURCE_TYPE.get(self.source_type, "medium")
        perms = ctx.permissions or RecordPermissions(
            allowed_roles=["engineer", "tech_lead", "engineering_manager"],
            allowed_departments=["Engineering"],
        )

        doc_id = ctx.doc_id or f"DOC-ENGNOTE-{ev.event_id}"

        return SourceRecord(
            document_id=doc_id,
            tenant_id=ev.tenant_id,
            source_type=self.source_type,
            title=title,
            content=content,
            author_id=author_id,
            department=dept,
            created_at=ev.start_time + timedelta(hours=1),
            status=ctx.status,
            classification=ctx.classification,
            permissions=perms,
            source_entity_id=ev.event_id,
            source_entity_type="event",
            related_entity_ids=related,
            authority_level=auth_level,
            version=ctx.version,
        )


# ---------------------------------------------------------------------------
# 5. Support Ticket Renderer
# ---------------------------------------------------------------------------

class SupportTicketRenderer(BaseRecordRenderer):
    source_type = "support_ticket"

    def render(self, ctx: RenderContext) -> SourceRecord:
        ev = ctx.event
        inc = ctx.incident
        cust_name = ctx.customer.name if ctx.customer else "Valued Customer"
        cust_id = ctx.customer.customer_id if ctx.customer else "CUST-UNKNOWN"
        author_id = ctx.author.user_id if ctx.author else "USR-UNKNOWN"
        dept = ctx.author.department if ctx.author else "Customer Support"

        ticket_time = ev.start_time + timedelta(minutes=25)
        title = f"Support Ticket: Issue with {ev.title} reported by {cust_name}"
        content = (
            f"Ticket ID: TICKET-{ev.event_id[-4:]}\n"
            f"Customer: {cust_name} ({cust_id})\n"
            f"Submitted At: {ticket_time.isoformat()}\n"
            f"Priority: High\n"
            f"Status: Closed\n\n"
            f"Customer Issue Description:\n"
            f"Our team is experiencing errors when accessing services. Transactions fail or time out.\n\n"
            f"Support Agent Notes:\n"
            f"Correlated with active incident {inc.incident_id if inc else ev.event_id}. Engineering deployed a fix.\n\n"
            f"Resolution Confirmation:\n"
            f"Verified with customer that normal operations have resumed."
        )

        related = [ev.event_id, cust_id]
        if inc:
            related.append(inc.incident_id)
        if ctx.primary_service:
            related.append(ctx.primary_service.service_id)

        auth_level = ctx.authority_level or DEFAULT_AUTHORITY_BY_SOURCE_TYPE.get(self.source_type, "low")
        perms = ctx.permissions or RecordPermissions(
            allowed_roles=["support_engineer", "support_manager", "account_executive"],
            allowed_departments=["Customer Support", "Sales"],
        )

        doc_id = ctx.doc_id or f"DOC-TICKET-{ev.event_id}-{cust_id}"

        return SourceRecord(
            document_id=doc_id,
            tenant_id=ev.tenant_id,
            source_type=self.source_type,
            title=title,
            content=content,
            author_id=author_id,
            department=dept,
            created_at=ticket_time,
            updated_at=ev.end_time,
            status=ctx.status,
            classification=ctx.classification,
            permissions=perms,
            source_entity_id=inc.incident_id if inc else ev.event_id,
            source_entity_type="incident" if inc else "event",
            related_entity_ids=related,
            authority_level=auth_level,
            version=ctx.version,
        )


# ---------------------------------------------------------------------------
# 6. Deployment Note Renderer
# ---------------------------------------------------------------------------

class DeploymentNoteRenderer(BaseRecordRenderer):
    source_type = "deployment_note"

    def render(self, ctx: RenderContext) -> SourceRecord:
        dep = ctx.triggering_deployment or ctx.rollback_deployment
        ev = ctx.event
        dep_id = dep.deployment_id if dep else "DEP-UNKNOWN"
        dep_ver = dep.version if dep else "1.0.0"
        author_id = dep.deployed_by if dep else (ctx.author.user_id if ctx.author else "USR-UNKNOWN")
        dept = ctx.author.department if ctx.author else "DevOps"
        svc_name = ctx.primary_service.name if ctx.primary_service else "service"

        title = f"Deployment Changelog: {svc_name} v{dep_ver} ({dep_id})"
        content = (
            f"Deployment ID: {dep_id}\n"
            f"Service: {svc_name}\n"
            f"Version: {dep_ver}\n"
            f"Deployer: {author_id}\n"
            f"Status: {dep.status if dep else 'succeeded'}\n"
            f"Timestamp: {dep.deployed_at.isoformat() if dep else ev.start_time.isoformat()}\n\n"
            f"Release Notes:\n"
            f"{dep.description if dep else 'Scheduled release deployment.'}\n\n"
            f"Deployment Pipeline Checks:\n"
            f"- Automated test suite: Passed\n"
            f"- Canary verification: Completed"
        )

        related = [ev.event_id]
        if dep:
            related.append(dep.deployment_id)
        if ctx.primary_service:
            related.append(ctx.primary_service.service_id)

        auth_level = ctx.authority_level or DEFAULT_AUTHORITY_BY_SOURCE_TYPE.get(self.source_type, "high")
        perms = ctx.permissions or RecordPermissions(
            allowed_roles=["engineer", "devops_engineer", "sre"],
            allowed_departments=["Engineering", "DevOps"],
        )

        doc_id = ctx.doc_id or f"DOC-DEP-{dep_id}"

        return SourceRecord(
            document_id=doc_id,
            tenant_id=ev.tenant_id,
            source_type=self.source_type,
            title=title,
            content=content,
            author_id=author_id,
            department=dept,
            created_at=dep.deployed_at if dep else ev.start_time,
            status=ctx.status,
            classification=ctx.classification,
            permissions=perms,
            source_entity_id=dep_id,
            source_entity_type="deployment",
            related_entity_ids=related,
            authority_level=auth_level,
            version=ctx.version,
        )


# ---------------------------------------------------------------------------
# 7. Pull Request Note Renderer
# ---------------------------------------------------------------------------

class PullRequestNoteRenderer(BaseRecordRenderer):
    source_type = "pull_request_note"

    def render(self, ctx: RenderContext) -> SourceRecord:
        pr = ctx.fixing_pull_request
        ev = ctx.event
        pr_id = pr.pull_request_id if pr else "PR-UNKNOWN"
        author_id = pr.author_id if pr else (ctx.author.user_id if ctx.author else "USR-UNKNOWN")
        dept = ctx.author.department if ctx.author else "Engineering"
        svc_name = ctx.primary_service.name if ctx.primary_service else "service"

        title = f"Pull Request Review: {pr.title if pr else 'Bug fix'}"
        content = (
            f"PR ID: {pr_id}\n"
            f"Repository: {pr.repository if pr else 'novastack/service'}\n"
            f"Author: {author_id}\n"
            f"Status: {pr.status if pr else 'merged'}\n"
            f"Created At: {pr.created_at.isoformat() if pr else ev.start_time.isoformat()}\n"
            f"Merged At: {pr.merged_at.isoformat() if (pr and pr.merged_at) else 'N/A'}\n\n"
            f"PR Description:\n"
            f"{pr.description if pr else 'Bug fix resolving operational issue.'}\n\n"
            f"Review Summary:\n"
            f"Code changes reviewed and approved by team peer reviewers. Unit tests added."
        )

        related = [ev.event_id]
        if pr:
            related.append(pr.pull_request_id)
        if ctx.primary_service:
            related.append(ctx.primary_service.service_id)

        auth_level = ctx.authority_level or DEFAULT_AUTHORITY_BY_SOURCE_TYPE.get(self.source_type, "medium")
        perms = ctx.permissions or RecordPermissions(
            allowed_roles=["engineer", "tech_lead"],
            allowed_departments=["Engineering"],
        )

        doc_id = ctx.doc_id or f"DOC-PR-{pr_id}"

        return SourceRecord(
            document_id=doc_id,
            tenant_id=ev.tenant_id,
            source_type=self.source_type,
            title=title,
            content=content,
            author_id=author_id,
            department=dept,
            created_at=pr.created_at if pr else ev.start_time,
            updated_at=pr.merged_at if pr else None,
            status=ctx.status,
            classification=ctx.classification,
            permissions=perms,
            source_entity_id=pr_id,
            source_entity_type="pull_request",
            related_entity_ids=related,
            authority_level=auth_level,
            version=ctx.version,
        )


# ---------------------------------------------------------------------------
# 8. Documentation Renderer
# ---------------------------------------------------------------------------

class DocumentationRenderer(BaseRecordRenderer):
    source_type = "documentation"

    def render(self, ctx: RenderContext) -> SourceRecord:
        ev = ctx.event
        svc_name = ctx.primary_service.name if ctx.primary_service else "service"
        author_id = ctx.author.user_id if ctx.author else "USR-UNKNOWN"
        dept = ctx.author.department if ctx.author else "Engineering"

        title = f"Service Architecture & Runbook: {svc_name}"
        content = (
            f"# {svc_name} — Service Runbook & Architecture\n\n"
            f"## Overview\n"
            f"{ctx.primary_service.description if ctx.primary_service else 'Core enterprise service.'}\n\n"
            f"## Operational Runbook\n"
            f"- Normal operating procedures and configuration parameters.\n"
            f"- Incident response: Refer to resolution procedures established after {ev.event_id}.\n\n"
            f"## Known Failure Modes\n"
            f"- Root cause pattern: {ev.root_cause[:100]}...\n"
            f"- Remediation: {ev.final_resolution}"
        )

        related = [ev.event_id]
        if ctx.primary_service:
            related.append(ctx.primary_service.service_id)

        auth_level = ctx.authority_level or DEFAULT_AUTHORITY_BY_SOURCE_TYPE.get(self.source_type, "high")
        perms = ctx.permissions or RecordPermissions(
            allowed_roles=["engineer", "support_engineer", "sre", "product_manager"],
            allowed_departments=["Engineering", "DevOps", "Customer Support", "Product"],
        )

        doc_id = ctx.doc_id or f"DOC-GUIDE-{svc_name}"

        return SourceRecord(
            document_id=doc_id,
            tenant_id=ev.tenant_id,
            source_type=self.source_type,
            title=title,
            content=content,
            author_id=author_id,
            department=dept,
            created_at=ev.start_time - timedelta(days=60),
            updated_at=ev.end_time or ev.start_time,
            status=ctx.status,
            classification=ctx.classification,
            permissions=perms,
            source_entity_id=ctx.primary_service.service_id if ctx.primary_service else ev.event_id,
            source_entity_type="service" if ctx.primary_service else "event",
            related_entity_ids=related,
            authority_level=auth_level,
            version=ctx.version,
        )


# ---------------------------------------------------------------------------
# 9. Policy Renderer
# ---------------------------------------------------------------------------

class PolicyRenderer(BaseRecordRenderer):
    source_type = "policy"

    def render(self, ctx: RenderContext) -> SourceRecord:
        ev = ctx.event
        author_id = ctx.author.user_id if ctx.author else "USR-UNKNOWN"
        dept = ctx.author.department if ctx.author else "Security"

        title = "Enterprise Incident Management & Deployment Policy"
        content = (
            f"# NovaStack Enterprise Policy: Production Incident Management\n\n"
            f"**Policy Version**: 2.0\n"
            f"**Authority**: Authoritative Organizational Governance\n\n"
            f"## Mandatory Requirements\n"
            f"1. All production deployments must undergo canary validation before full rollout.\n"
            f"2. Any Sev-1 or Sev-2 incident requires a formal postmortem within 72 hours.\n"
            f"3. Root cause mitigations must be tracked via fixing pull requests with peer review.\n"
            f"4. Customer notifications must adhere to established SLA response windows."
        )

        related = [ev.event_id]

        auth_level = ctx.authority_level or DEFAULT_AUTHORITY_BY_SOURCE_TYPE.get(self.source_type, "authoritative")
        perms = ctx.permissions or RecordPermissions(
            allowed_roles=[],
            allowed_departments=[],
            allowed_teams=[],
        )  # Empty means company-wide policy

        doc_id = ctx.doc_id or "DOC-POLICY-INCIDENT-MGMT"

        return SourceRecord(
            document_id=doc_id,
            tenant_id=ev.tenant_id,
            source_type=self.source_type,
            title=title,
            content=content,
            author_id=author_id,
            department=dept,
            created_at=datetime(2024, 1, 1),
            updated_at=datetime(2025, 1, 1),
            status=ctx.status,
            classification="internal",
            permissions=perms,
            source_entity_id=None,
            source_entity_type=None,
            related_entity_ids=related,
            authority_level=auth_level,
            version=ctx.version,
        )


# ---------------------------------------------------------------------------
# 10. Meeting Note Renderer
# ---------------------------------------------------------------------------

class MeetingNoteRenderer(BaseRecordRenderer):
    source_type = "meeting"

    def render(self, ctx: RenderContext) -> SourceRecord:
        ev = ctx.event
        team_name = ctx.responsible_team.name if ctx.responsible_team else "Engineering"
        author_id = ctx.author.user_id if ctx.author else "USR-UNKNOWN"
        dept = ctx.author.department if ctx.author else "Engineering"

        meeting_time = (ev.end_time or ev.start_time) + timedelta(days=1)
        title = f"Meeting Notes: Weekly Reliability Sync — {ev.title} Review"
        content = (
            f"Meeting: Weekly Reliability Sync\n"
            f"Team: {team_name}\n"
            f"Date: {meeting_time.strftime('%Y-%m-%d')}\n\n"
            f"Agenda Items:\n"
            f"1. Review recent event {ev.event_id} ({ev.title})\n"
            f"2. Discuss follow-up fixes and architectural improvements\n\n"
            f"Discussion & Notes:\n"
            f"- Team reviewed root cause: {ev.root_cause}.\n"
            f"- Action item assigned to complete postmortem and follow-up PR."
        )

        related = [ev.event_id]
        if ctx.responsible_team:
            related.append(ctx.responsible_team.team_id)

        auth_level = ctx.authority_level or DEFAULT_AUTHORITY_BY_SOURCE_TYPE.get(self.source_type, "medium")
        perms = ctx.permissions or RecordPermissions(
            allowed_roles=["engineer", "manager", "tech_lead"],
            allowed_departments=["Engineering", "DevOps"],
        )

        doc_id = ctx.doc_id or f"DOC-MEETING-{ev.event_id}"

        return SourceRecord(
            document_id=doc_id,
            tenant_id=ev.tenant_id,
            source_type=self.source_type,
            title=title,
            content=content,
            author_id=author_id,
            department=dept,
            created_at=meeting_time,
            status=ctx.status,
            classification=ctx.classification,
            permissions=perms,
            source_entity_id=ev.event_id,
            source_entity_type="event",
            related_entity_ids=related,
            authority_level=auth_level,
            version=ctx.version,
        )


# ---------------------------------------------------------------------------
# Renderer Registry
# ---------------------------------------------------------------------------

RENDERERS: dict[str, BaseRecordRenderer] = {
    "incident": IncidentRecordRenderer(),
    "postmortem": PostmortemRecordRenderer(),
    "conversation": ConversationRecordRenderer(),
    "engineering_note": EngineeringNoteRenderer(),
    "support_ticket": SupportTicketRenderer(),
    "deployment_note": DeploymentNoteRenderer(),
    "pull_request_note": PullRequestNoteRenderer(),
    "documentation": DocumentationRenderer(),
    "policy": PolicyRenderer(),
    "meeting": MeetingNoteRenderer(),
}


def render_source_record(source_type: str, ctx: RenderContext) -> SourceRecord:
    """Render a source record for a specific source type using the registered renderer."""
    renderer = RENDERERS.get(source_type)
    if renderer is None:
        raise ValueError(f"Unknown source type: {source_type}. Supported types: {sorted(RENDERERS.keys())}")
    return renderer.render(ctx)


# ---------------------------------------------------------------------------
# Validation Helper
# ---------------------------------------------------------------------------

def validate_source_records(
    records: list[SourceRecord],
    valid_entity_ids: set[str],
    valid_tenants: set[str],
) -> list[str]:
    """Validate a list of SourceRecord objects against consistency rules."""
    errors: list[str] = []
    seen_doc_ids: set[str] = set()

    for r in records:
        # Document ID uniqueness
        if r.document_id in seen_doc_ids:
            errors.append(f"Duplicate document_id: {r.document_id}")
        seen_doc_ids.add(r.document_id)

        # Tenant validity
        if r.tenant_id not in valid_tenants:
            errors.append(f"Record {r.document_id}: invalid tenant_id '{r.tenant_id}'")

        # Controlled source type
        if r.source_type not in SOURCE_TYPES:
            errors.append(f"Record {r.document_id}: invalid source_type '{r.source_type}'")

        # Controlled authority level
        if r.authority_level not in AUTHORITY_LEVELS:
            errors.append(f"Record {r.document_id}: invalid authority_level '{r.authority_level}'")

        # Controlled classification
        if r.classification not in CLASSIFICATION_LEVELS:
            errors.append(f"Record {r.document_id}: invalid classification '{r.classification}'")

        # Controlled status
        if r.status not in RECORD_STATUSES:
            errors.append(f"Record {r.document_id}: invalid status '{r.status}'")

        # Author ID existence
        if r.author_id not in valid_entity_ids and r.author_id != "system":
            errors.append(f"Record {r.document_id}: author_id '{r.author_id}' does not exist")

        # Provenance source_entity_id existence
        if r.source_entity_id and r.source_entity_id not in valid_entity_ids:
            errors.append(f"Record {r.document_id}: source_entity_id '{r.source_entity_id}' does not exist")

        # Related entity IDs existence
        for rel_id in r.related_entity_ids:
            if rel_id not in valid_entity_ids:
                errors.append(f"Record {r.document_id}: related entity '{rel_id}' does not exist")

        # Temporal validity
        if r.valid_from and r.valid_until and r.valid_from > r.valid_until:
            errors.append(f"Record {r.document_id}: valid_from > valid_until")
        if r.updated_at and r.created_at > r.updated_at:
            errors.append(f"Record {r.document_id}: created_at > updated_at")

        # Document permission allowed_user_ids existence
        if r.permissions and r.permissions.allowed_user_ids:
            for uid in r.permissions.allowed_user_ids:
                if uid not in valid_entity_ids:
                    errors.append(f"Record {r.document_id}: allowed_user_id '{uid}' does not exist")

    # Second pass for cross-record references (supersedes_id, parent_id)
    doc_map: dict[str, SourceRecord] = {r.document_id: r for r in records}
    for r in records:
        if r.supersedes_id:
            if r.supersedes_id not in doc_map:
                errors.append(f"Record {r.document_id}: supersedes_id '{r.supersedes_id}' does not exist")
            elif r.created_at < doc_map[r.supersedes_id].created_at:
                errors.append(
                    f"Record {r.document_id}: created_at ({r.created_at}) precedes "
                    f"superseded record {r.supersedes_id} ({doc_map[r.supersedes_id].created_at})"
                )

        if r.parent_id and r.parent_id not in doc_map and r.parent_id not in valid_entity_ids:
            errors.append(f"Record {r.document_id}: parent_id '{r.parent_id}' does not exist")

    return errors


# ---------------------------------------------------------------------------
# Demonstration Fixture Generator
# ---------------------------------------------------------------------------

def render_demonstration_fixture(generator: Any) -> list[SourceRecord]:
    """Generate a small proof-of-concept observational fixture from Event 1.

    Demonstrates how multiple distinct observational source records with varied
    perspectives, authority levels, and tones are deterministically rendered from
    a single underlying ground-truth event.
    """
    if not generator.events:
        generator.generate()

    # Target Event 1: Checkout timeout outage
    event = generator.events[0]
    incident = generator.incidents[0] if generator.incidents else None

    # Resolve related entities
    svc_map = {s.service_id: s for s in generator.services}
    primary_svc = svc_map.get(event.affected_service_ids[0]) if event.affected_service_ids else None

    team_map = {t.team_id: t for t in generator.teams}
    team = team_map.get(event.responsible_team_id)

    dep_map = {d.deployment_id: d for d in generator.deployments}
    dep = dep_map.get(event.triggering_deployment_id) if event.triggering_deployment_id else None

    pr_map = {p.pull_request_id: p for p in generator.pull_requests}
    pr = pr_map.get(event.fixing_pull_request_id) if event.fixing_pull_request_id else None

    user_map = {u.user_id: u for u in generator.users}
    author = user_map.get(incident.incident_commander_id) if incident else None

    cust_map = {c.customer_id: c for c in generator.customers}
    customer = cust_map.get(event.impacted_customer_ids[0]) if event.impacted_customer_ids else None

    ctx = RenderContext(
        event=event,
        incident=incident,
        triggering_deployment=dep,
        fixing_pull_request=pr,
        primary_service=primary_svc,
        responsible_team=team,
        author=author,
        customer=customer,
    )

    records: list[SourceRecord] = []
    # Render diverse records from this single event
    demo_types = [
        "incident",
        "postmortem",
        "conversation",
        "engineering_note",
        "support_ticket",
        "deployment_note",
        "pull_request_note",
    ]

    for st in demo_types:
        records.append(render_source_record(st, ctx))

    return records
