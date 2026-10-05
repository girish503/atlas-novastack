"""Temporal noise, duplicates, versions & conflicting evidence generator — Milestone 4C.

Introduces realistic enterprise information quality problems into the observational corpus:
1. Near duplicates (~55 records): Copied runbooks, repeated summaries, reworded support tickets.
2. Stale records (~40 records): Outdated runbooks, expired policies with valid_from/valid_until.
3. Version chains (~60 records): Multi-version document evolutions (v1 -> v2 -> v3) with supersedes_id.
4. Superseded records (~30 records): Standalone superseded documents with status="superseded".
5. Draft records (~40 records): Work-in-progress, unreviewed designs and policies with status="draft".
6. Conflicting observations (~35 records): Misattributed early hypotheses and competing observations during incident triage.
7. Corrected observations (~30 records): Formal corrections and retractions referencing earlier mistaken observations.

Total: ~290 noise records, bringing total combined corpus to ~1,193 records.
Strict boundary:
- Does NOT mutate or touch ground-truth layer.
- Does NOT contain prompt injection, retrieval poisoning, or security attacks.
- Preserves tenant isolation (TENANT-NOVASTACK only).
- Deterministic reproducibility with RANDOM_SEED = 20260909.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from novastack.config import RANDOM_SEED
from novastack.models import (
    RecordPermissions,
    SourceRecord,
    User,
)


class TemporalNoiseGenerator:
    """Generates controlled enterprise noise and information quality problems."""

    def __init__(
        self,
        generator: Any,
        base_records: list[SourceRecord],
        seed: int = RANDOM_SEED,
    ) -> None:
        self.gen = generator
        self.base_records = base_records
        self.seed = seed
        self.rng = random.Random(seed)
        self.records: list[SourceRecord] = []

        # Category counters for reporting
        self.near_duplicate_count = 0
        self.stale_record_count = 0
        self.version_chain_count = 0
        self.superseded_count = 0
        self.draft_count = 0
        self.conflicting_count = 0
        self.correction_count = 0

    def generate_noise_records(self) -> list[SourceRecord]:
        """Generate all noise records across the 7 quality problem categories."""
        self.records.clear()
        self.near_duplicate_count = 0
        self.stale_record_count = 0
        self.version_chain_count = 0
        self.superseded_count = 0
        self.draft_count = 0
        self.conflicting_count = 0
        self.correction_count = 0

        tenant_id = "TENANT-NOVASTACK"
        ns_users = [u for u in self.gen.users if u.tenant_id == tenant_id]

        # 1. Near duplicates (55 records)
        self._generate_near_duplicates(ns_users)

        # 2. Stale records (40 records)
        self._generate_stale_records(ns_users)

        # 3. Version chains (60 records across 20 chains)
        self._generate_version_chains(ns_users)

        # 4. Superseded records (30 records)
        self._generate_superseded_records(ns_users)

        # 5. Drafts (40 records)
        self._generate_drafts(ns_users)

        # 6. Conflicting observations (35 records)
        self._generate_conflicting_observations(ns_users)

        # 7. Corrected observations (30 records)
        self._generate_corrections(ns_users)

        return self.records

    # ------------------------------------------------------------------
    # 1. Near Duplicates (55 records)
    # ------------------------------------------------------------------
    def _generate_near_duplicates(self, users: list[User]) -> None:
        # Sample base records to mirror or copy
        candidates = [
            r for r in self.base_records
            if r.source_type in ("documentation", "support_ticket", "engineering_note", "meeting")
        ]
        if not candidates:
            candidates = self.base_records

        for i in range(55):
            base = candidates[i % len(candidates)]
            author = self.rng.choice(users)
            doc_id = f"DOC-NOISE-DUP-{i+1:04d}"

            # Semantic variation types
            variant_type = i % 4
            if variant_type == 0:
                title = f"[Team Mirror] {base.title}"
                header = f"[Cross-Posted to Team Internal Wiki — Synchronized copy]\nOriginal Reference: {base.document_id}\n\n"
                content = header + base.content.replace("## ", "### ").replace("SOP", "Standard Operating Procedure")
            elif variant_type == 1:
                title = f"{base.title} (Local Reference Copy)"
                header = f"# Engineering Mirror: {base.title}\n*Archival team copy stored for offline runbook reference.*\n\n"
                content = header + base.content.replace("Protocol", "Execution Protocol").replace("Overview", "Summary")
            elif variant_type == 2:
                title = f"Fwd: {base.title}"
                header = f"---------- Forwarded Knowledge Base Article ----------\nFrom: Internal Documentation Bot\n\n"
                content = header + base.content
            else:
                title = f"{base.title} — Operational Summary"
                content = f"Operational Digest:\n{base.content}\n\n[Note: Content synchronized from central documentation repository]"

            created_at = base.created_at + timedelta(hours=self.rng.randint(4, 72))

            dup_rec = SourceRecord(
                document_id=doc_id,
                tenant_id=base.tenant_id,
                source_type=base.source_type,
                title=title,
                content=content,
                author_id=author.user_id,
                department=base.department,
                created_at=created_at,
                updated_at=created_at + timedelta(days=self.rng.randint(1, 10)),
                status=base.status,
                classification=base.classification,
                permissions=base.permissions,
                source_entity_id=base.source_entity_id,
                source_entity_type=base.source_entity_type,
                related_entity_ids=list(base.related_entity_ids),
                authority_level=base.authority_level,
                parent_id=base.document_id,
            )
            self.records.append(dup_rec)
            self.near_duplicate_count += 1

    # ------------------------------------------------------------------
    # 2. Stale Records (40 records)
    # ------------------------------------------------------------------
    def _generate_stale_records(self, users: list[User]) -> None:
        stale_templates = [
            (
                "documentation",
                "Engineering",
                "[Deprecated] Pre-2025 REST API v1 Authentication & Token Endpoint",
                (
                    "# [DEPRECATED] API v1 Authentication Guide\n\n"
                    "## Warning\n"
                    "This endpoint specification was deprecated on 2025-06-30. Use API v3 Bearer token authentication.\n\n"
                    "## Legacy Protocol\n"
                    "- `POST /v1/auth/tokens`: Consumes basic auth credentials and returns 60-minute JWT.\n"
                    "- Signature algorithm: HS256 (Superseded by RS256 asymmetric keys in 2025)."
                ),
                datetime(2024, 6, 1),
                datetime(2024, 6, 1),
                datetime(2025, 6, 30),
            ),
            (
                "documentation",
                "DevOps",
                "[Archived] Legacy Virtual Machine Deployment Runbook & Ansible Playbooks",
                (
                    "# [ARCHIVED] Bare-Metal & EC2 Deployment SOP\n\n"
                    "## Notice\n"
                    "Production workloads migrated completely to EKS Kubernetes clusters in Q1 2025. "
                    "This runbook is retained strictly for historical compliance audits.\n\n"
                    "## Legacy Deploy Steps\n"
                    "1. SSH into deployment bastion host `bastion-prod-01`.\n"
                    "2. Run `ansible-playbook -i hosts deploy_services.yml`."
                ),
                datetime(2024, 3, 1),
                datetime(2024, 3, 1),
                datetime(2025, 3, 31),
            ),
            (
                "policy",
                "Finance",
                "[Superseded] 2024 Corporate Travel & Expense Policy (Old Per Diem Limits)",
                (
                    "# NovaStack Policy: Corporate Travel (2024 Edition)\n\n"
                    "## Status: Superseded by 2025 Policy Update\n"
                    "Historical maximum lodging benchmark was $200/night and daily meals per diem was $55. "
                    "Employees should consult the current 2025/2026 travel policy for current reimbursement rates."
                ),
                datetime(2024, 1, 1),
                datetime(2024, 1, 1),
                datetime(2025, 1, 15),
            ),
            (
                "documentation",
                "Security",
                "[Deprecated] Legacy VPN Access Protocol & Shared Secret Guidelines",
                (
                    "# Security Procedure: OpenVPN Client Setup (Pre-Zero-Trust)\n\n"
                    "## Deprecation Notice\n"
                    "Direct OpenVPN client access was permanently decommissioned in favor of Teleport Zero-Trust access.\n\n"
                    "Historical configuration files (.ovpn) are no longer recognized by network identity providers."
                ),
                datetime(2024, 5, 10),
                datetime(2024, 5, 10),
                datetime(2025, 8, 1),
            ),
        ]

        for i in range(40):
            author = self.rng.choice(users)
            st_type, dept, title_base, body, t_created, valid_start, valid_end = stale_templates[i % len(stale_templates)]
            variant = (i // len(stale_templates)) + 1
            title = f"{title_base} (Revision {variant})" if variant > 1 else title_base

            doc_id = f"DOC-NOISE-STALE-{i+1:04d}"
            # Ensure valid_from <= valid_until
            valid_from = valid_start + timedelta(days=i * 5)
            valid_until = valid_end + timedelta(days=i * 5)
            created_at = t_created + timedelta(days=i * 5)
            updated_at = valid_until

            stale_rec = SourceRecord(
                document_id=doc_id,
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=title,
                content=body + f"\n\n---\nArchive Status: Retired\nAudit Record ID: AUD-STALE-{i+100:04d}",
                author_id=author.user_id,
                department=dept,
                created_at=created_at,
                updated_at=updated_at,
                valid_from=valid_from,
                valid_until=valid_until,
                version=f"0.{variant}",
                status="deprecated" if i % 2 == 0 else "superseded",
                classification="internal",
                permissions=RecordPermissions(allowed_departments=[dept]),
                source_entity_id=None,
                source_entity_type=None,
                related_entity_ids=[],
                authority_level="low",
            )
            self.records.append(stale_rec)
            self.stale_record_count += 1

    # ------------------------------------------------------------------
    # 3. Version Chains (60 records across 20 chains)
    # ------------------------------------------------------------------
    def _generate_version_chains(self, users: list[User]) -> None:
        chain_topics = [
            ("documentation", "Engineering", "Checkout Microservice API Contract", "API endpoints for checkout session creation and payment submission."),
            ("documentation", "DevOps", "Database Read-Replica Failover SOP", "Standard operating procedures for primary database failover and read replica promotion."),
            ("policy", "Security", "Enterprise Data Retention & Audit Log Policy", "Corporate rules governing log archiving, customer PII purging, and audit records."),
            ("documentation", "Product", "Express Checkout Specification & UX Guidelines", "Product requirements for one-click payment flows and wallet integrations."),
            ("documentation", "DevOps", "Kubernetes Ingress Controller & SSL Termination Guide", "Configuration runbook for NGINX ingress routing and Let's Encrypt certificates."),
            ("policy", "HR", "Remote Work & Hybrid Schedule Guidelines", "Corporate standards on core collaboration hours, home office equipment, and travel."),
            ("documentation", "Security", "Third-Party SaaS Vendor Security Assessment Protocol", "Evaluation criteria and questionnaires for security assessment of vendors."),
            ("documentation", "Engineering", "Distributed Tracing & W3C Baggage Header Standards", "Standardized OpenTelemetry tracing headers and latency metrics across microservices."),
            ("policy", "Finance", "Capital Expenditure & Cloud Infrastructure Authorization", "Approval workflows for cloud compute capacity commitments and database spend."),
            ("documentation", "Customer Support", "Tier-1 Customer Triage & Escalation Protocol", "Standard responses and routing criteria for high-priority support tickets."),
        ]

        # Generate 20 chains of 3 versions each = 60 records
        for k in range(20):
            topic_idx = k % len(chain_topics)
            st_type, dept, topic_title, topic_desc = chain_topics[topic_idx]
            author = self.rng.choice(users)
            root_id = f"DOC-NOISE-VER-{k+1:02d}-V1"
            v2_id = f"DOC-NOISE-VER-{k+1:02d}-V2"
            v3_id = f"DOC-NOISE-VER-{k+1:02d}-V3"

            t1 = datetime(2025, 1, 15) + timedelta(days=k * 10)
            t2 = t1 + timedelta(days=120)
            t3 = t2 + timedelta(days=140)

            # Version 1 (Superseded)
            v1_rec = SourceRecord(
                document_id=root_id,
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=f"{topic_title} (v1.0)",
                content=(
                    f"# {topic_title} — Version 1.0\n\n"
                    f"## Initial Baseline ({t1.strftime('%B %Y')})\n"
                    f"{topic_desc}\n\n"
                    f"### Key Parameters (v1):\n"
                    f"- Initial baseline implementation.\n"
                    f"- Monolithic configuration; standard timeout set to 10 seconds.\n"
                    f"- Manual monitoring and review schedule."
                ),
                author_id=author.user_id,
                department=dept,
                created_at=t1,
                updated_at=t1 + timedelta(days=5),
                valid_from=t1,
                valid_until=t2,
                version="1.0",
                status="superseded",
                classification="internal",
                permissions=RecordPermissions(allowed_departments=[dept]),
                source_entity_id=None,
                source_entity_type=None,
                related_entity_ids=[],
                authority_level="low",
                parent_id=None,
                supersedes_id=None,
            )

            # Version 2 (Superseded)
            v2_rec = SourceRecord(
                document_id=v2_id,
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=f"{topic_title} (v2.0)",
                content=(
                    f"# {topic_title} — Version 2.0\n\n"
                    f"## Revised Specification ({t2.strftime('%B %Y')})\n"
                    f"**Notice: Supersedes {root_id}**\n\n"
                    f"{topic_desc}\n\n"
                    f"### Key Updates (v2):\n"
                    f"- Migrated configuration to microservice architecture.\n"
                    f"- Standard timeout reduced from 10s to 3,000ms.\n"
                    f"- Automated health checks introduced via Prometheus."
                ),
                author_id=author.user_id,
                department=dept,
                created_at=t2,
                updated_at=t2 + timedelta(days=10),
                valid_from=t2,
                valid_until=t3,
                version="2.0",
                status="superseded",
                classification="internal",
                permissions=RecordPermissions(allowed_departments=[dept]),
                source_entity_id=None,
                source_entity_type=None,
                related_entity_ids=[],
                authority_level="medium",
                parent_id=root_id,
                supersedes_id=root_id,
            )

            # Version 3 (Current / Published)
            v3_rec = SourceRecord(
                document_id=v3_id,
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=f"{topic_title} (v3.0)",
                content=(
                    f"# {topic_title} — Version 3.0 [CURRENT]\n\n"
                    f"## Active Production Specification ({t3.strftime('%B %Y')})\n"
                    f"**Notice: Supersedes {v2_id}**\n\n"
                    f"{topic_desc}\n\n"
                    f"### Current Standards (v3):\n"
                    f"- Full zero-trust authentication and mutual TLS (mTLS) enforcement.\n"
                    f"- Exponential backoff retry circuit breakers with adaptive timeouts.\n"
                    f"- Real-time telemetry dashboard with SLO automated alerting."
                ),
                author_id=author.user_id,
                department=dept,
                created_at=t3,
                updated_at=t3 + timedelta(days=15),
                valid_from=t3,
                valid_until=None,
                version="3.0",
                status="published",
                classification="internal",
                permissions=RecordPermissions(allowed_departments=[dept]),
                source_entity_id=None,
                source_entity_type=None,
                related_entity_ids=[],
                authority_level="high" if st_type != "policy" else "authoritative",
                parent_id=root_id,
                supersedes_id=v2_id,
            )

            self.records.extend([v1_rec, v2_rec, v3_rec])
            self.version_chain_count += 3

    # ------------------------------------------------------------------
    # 4. Superseded Standalone Records (30 records)
    # ------------------------------------------------------------------
    def _generate_superseded_records(self, users: list[User]) -> None:
        for i in range(30):
            author = self.rng.choice(users)
            doc_id = f"DOC-NOISE-SUP-{i+1:04d}"
            created_at = datetime(2025, 2, 1) + timedelta(days=i * 15)
            valid_until = created_at + timedelta(days=180)

            title = f"[Superseded] Operational Workflow Note #{i+101} — Standard Procedures"
            content = (
                f"# [SUPERSEDED] Operational Note #{i+101}\n\n"
                f"## Notice\n"
                f"This document was formally superseded on {valid_until.strftime('%Y-%m-%d')} following "
                f"the quarterly architecture committee review. It remains cataloged for audit traceability.\n\n"
                f"## Historical Content\n"
                f"Standard batch synchronization intervals were configured for 60-minute cycles. "
                f"Refer to the updated real-time streaming pipeline guidelines for current operations."
            )

            rec = SourceRecord(
                document_id=doc_id,
                tenant_id="TENANT-NOVASTACK",
                source_type="engineering_note" if i % 2 == 0 else "documentation",
                title=title,
                content=content,
                author_id=author.user_id,
                department="Engineering",
                created_at=created_at,
                updated_at=valid_until,
                valid_from=created_at,
                valid_until=valid_until,
                version="1.0-superseded",
                status="superseded",
                classification="internal",
                permissions=RecordPermissions(allowed_departments=["Engineering"]),
                source_entity_id=None,
                source_entity_type=None,
                related_entity_ids=[],
                authority_level="low",
            )
            self.records.append(rec)
            self.superseded_count += 1

    # ------------------------------------------------------------------
    # 5. Drafts (40 records)
    # ------------------------------------------------------------------
    def _generate_drafts(self, users: list[User]) -> None:
        draft_topics = [
            (
                "postmortem",
                "Engineering",
                "[DRAFT] Preliminary Postmortem: Service Latency Spike Investigation",
                (
                    "# [DRAFT] Preliminary Post-Incident Review\n\n"
                    "## Incident Status: Under Investigation\n"
                    "**DRAFT NOTE**: This postmortem is incomplete. The RCA review meeting has not yet taken place.\n\n"
                    "## Initial Timeline\n"
                    "- 14:10 UTC: Initial alert triggered on p95 latency.\n"
                    "- 14:25 UTC: [TODO: Confirm who restarted pods].\n"
                    "- 14:40 UTC: Latency normalized.\n\n"
                    "## Preliminary Root Cause\n"
                    "[TODO: Fill in whether root cause was database lock or GC pause].\n\n"
                    "## Action Items (Pending Approval)\n"
                    "- [ ] AI-1: [Draft] Add alert for container memory headroom.\n"
                    "- [ ] AI-2: [Draft] Investigate JVM heap allocation parameters."
                ),
            ),
            (
                "documentation",
                "Engineering",
                "[DRAFT] RFC: Proposed Migration to GraphQL Federation Architecture",
                (
                    "# Request for Comments (RFC): GraphQL Federation Architecture [DRAFT]\n\n"
                    "## Status: Draft / Open for Feedback\n"
                    "Author: Engineering Working Group\n\n"
                    "## Abstract\n"
                    "This draft RFC outlines a proposal to unify frontend API calls through Apollo Federation.\n\n"
                    "## Open Questions\n"
                    "1. What is the expected latency overhead of the gateway subgraph router?\n"
                    "2. How will we enforce field-level authorization across multiple microservices?\n"
                    "3. [TODO: Benchmark router performance under 5,000 req/sec]."
                ),
            ),
            (
                "policy",
                "Security",
                "[DRAFT] Enterprise Guidelines for Generative AI & Developer Tooling",
                (
                    "# Corporate Security Policy: Developer AI Tools [DRAFT - NOT APPROVED]\n\n"
                    "## Notice\n"
                    "This is an unapproved policy draft circulated for internal Security & Legal review only.\n\n"
                    "## Draft Provisions\n"
                    "1. Code completion extensions must have telemetry logging disabled.\n"
                    "2. Customer PII and production database credentials must never be submitted to external models.\n"
                    "3. All third-party AI vendor agreements must include zero-data-retention clauses."
                ),
            ),
            (
                "documentation",
                "DevOps",
                "[DRAFT] Disaster Recovery Runbook: Multi-Region Kafka Cluster Failover",
                (
                    "# Operational Runbook: Kafka Disaster Recovery [DRAFT - WORK IN PROGRESS]\n\n"
                    "## CAUTION: DO NOT EXECUTE IN PRODUCTION\n"
                    "This runbook has not been validated in staging drills.\n\n"
                    "## Draft Steps\n"
                    "1. Switch MirrorMaker active replication direction.\n"
                    "2. [TODO: Validate consumer group offset translation].\n"
                    "3. Point application producers to secondary bootstrap brokers."
                ),
            ),
        ]

        for i in range(40):
            author = self.rng.choice(users)
            st_type, dept, title_base, body = draft_topics[i % len(draft_topics)]
            variant = (i // len(draft_topics)) + 1
            title = f"{title_base} (Draft #{variant})" if variant > 1 else title_base
            created_at = datetime(2025, 3, 1) + timedelta(days=i * 12)

            rec = SourceRecord(
                document_id=f"DOC-NOISE-DFT-{i+1:04d}" ,
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=title,
                content=body + f"\n\n---\nReview Status: In Progress\nDocument Owner: {author.name}",
                author_id=author.user_id,
                department=dept,
                created_at=created_at,
                updated_at=created_at + timedelta(days=self.rng.randint(1, 7)),
                version=f"0.{variant}-draft",
                status="draft",
                classification="internal",
                permissions=RecordPermissions(allowed_departments=[dept]),
                source_entity_id=None,
                source_entity_type=None,
                related_entity_ids=[],
                authority_level="draft",
            )
            self.records.append(rec)
            self.draft_count += 1

    # ------------------------------------------------------------------
    # 6. Conflicting Observations (35 records)
    # ------------------------------------------------------------------
    def _generate_conflicting_observations(self, users: list[User]) -> None:
        conflict_scenarios = [
            (
                "EVT-NS-0001",
                "SVC-NS-0005",
                "conversation",
                "Incident Triage Channel: Initial False Hypothesis Regarding CDN Ingress",
                (
                    "Triage Slack Log — Channel #incident-checkout-outage (Early Phase):\n\n"
                    "14:12 UTC — Alex Vance: Customers are seeing 504 timeouts at checkout. Could this be Cloudflare dropping requests at the CDN layer?\n"
                    "14:15 UTC — David Chen: Cloudflare status page reports normal operations, but our edge proxy ingress error rate is spiking. Suspecting network perimeter bottleneck.\n"
                    "14:18 UTC — Maya Lin: Looking into edge firewall rules deployed this morning.\n"
                    "[Note: Initial triage team actively investigated edge CDN and firewall before database connection starvation was identified.]"
                ),
            ),
            (
                "EVT-NS-0003",
                "SVC-NS-0005",
                "support_ticket",
                "Urgent Support Ticket: Suspected Bank Card Network Downtime",
                (
                    "Support Escalation Report:\n\n"
                    "Customer Ticket: #TKT-ESC-3041\n"
                    "Agent Observation: Multiple enterprise merchants reporting payment declined errors.\n"
                    "Agent Hypothesis: Customer support suspected widespread Visa/Mastercard bank interchange outage based on error strings.\n"
                    "Actual Technical Finding: Later telemetry confirmed the payment gateway provider was healthy; NovaStack had deployed an erroneous API endpoint URL."
                ),
            ),
            (
                "EVT-NS-0006",
                "SVC-NS-0003",
                "engineering_note",
                "Early Diagnostic Log: Suspected Unoptimized Analytics Query",
                (
                    "DBA Investigation Note (10:30 UTC):\n\n"
                    "Observed high IOPS saturation on primary analytics database cluster.\n"
                    "Initial Hypothesis: Suspected monthly finance data export query was missing index and locking tables.\n"
                    "Follow-up Resolution: Forensic query log analysis later disproved this; root cause was an unindexed foreign key in migration schema DDL."
                ),
            ),
            (
                "EVT-NS-0007",
                "SVC-NS-0010",
                "conversation",
                "Security Slack: Suspected Distributed Botnet DDoS Attack",
                (
                    "Security Operations Slack #sec-ops (08:45 UTC):\n\n"
                    "Marcus Reed: Rate limiter is blocking 90% of incoming API requests. Are we experiencing a layer-7 volumetric DDoS attack?\n"
                    "Rachel Vance: Checking IP distribution... traffic origin is globally distributed, looks like automated scraping botnet.\n"
                    "Conclusion (Post-Incident): Not a DDoS attack. Legitimate traffic was being rejected due to a configuration rollback that reduced token bucket limits by 100x."
                ),
            ),
            (
                "EVT-NS-0011",
                "SVC-NS-0003",
                "meeting",
                "Operations Sync: Suspected Warehouse Scanner Hardware Malfunction",
                (
                    "Operational Incident Sync Notes:\n\n"
                    "Field Operations Report: Fulfillment centers reported that inventory updates were not appearing on merchant consoles. Initial theory attributed failure to handheld barcode scanner WiFi disconnects.\n"
                    "Engineering Analysis: Hardware was functional; root cause was Kafka inventory sync partition rebalancing lag."
                ),
            ),
        ]

        for i in range(35):
            author = self.rng.choice(users)
            ev_id, svc_id, st_type, title_base, body = conflict_scenarios[i % len(conflict_scenarios)]
            variant = (i // len(conflict_scenarios)) + 1
            title = f"{title_base} (Perspective {variant})" if variant > 1 else title_base

            # Temporal alignment with event:
            # Look up event start time
            matching_events = [e for e in self.gen.events if e.event_id == ev_id]
            ev = matching_events[0] if matching_events else self.gen.events[0]
            created_at = ev.start_time + timedelta(minutes=self.rng.randint(5, 45))

            rec = SourceRecord(
                document_id=f"DOC-NOISE-CONF-{i+1:04d}",
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=title,
                content=body,
                author_id=author.user_id,
                department=author.department,
                created_at=created_at,
                updated_at=created_at + timedelta(hours=1),
                status="published",
                classification="internal",
                permissions=RecordPermissions(allowed_departments=["Engineering", "Customer Support", "Security"]),
                source_entity_id=None,
                source_entity_type=None,
                related_entity_ids=[ev.event_id, svc_id],
                authority_level="low",
            )
            self.records.append(rec)
            self.conflicting_count += 1

    # ------------------------------------------------------------------
    # 7. Corrected Observations (30 records)
    # ------------------------------------------------------------------
    def _generate_corrections(self, users: list[User]) -> None:
        correction_templates = [
            (
                "EVT-NS-0001",
                "SVC-NS-0005",
                "engineering_note",
                "Correction: Clarification Regarding Checkout Service vs Payment Gateway",
                (
                    "# Official Technical Correction & Bulletin\n\n"
                    "## Context\n"
                    "An early operational bulletin issued at 14:15 UTC stated that checkout failures were attributable "
                    "to external payment gateway provider downtime.\n\n"
                    "## Rectification\n"
                    "Telemetry analysis completed at 15:30 UTC proved that external payment gateway systems were completely operational. "
                    "The failure was entirely internal: checkout-service connection pool saturation prevented requests from reaching "
                    "the payment gateway. All teams should disregard earlier statements referencing external gateway downtime."
                ),
            ),
            (
                "EVT-NS-0002",
                "SVC-NS-0007",
                "documentation",
                "Incident Addendum: Correcting Token Expiration Root Cause Analysis",
                (
                    "# Incident Follow-Up: Errata and Technical Correction\n\n"
                    "## Summary of Correction\n"
                    "Initial incident status updates reported Redis cache evictions as the primary cause of user logouts.\n"
                    "Subsequent forensic audit confirmed Redis cache was healthy; token invalidation was triggered by an unsynchronized "
                    "signing secret deployment across authentication cluster nodes."
                ),
            ),
            (
                "EVT-NS-0007",
                "SVC-NS-0010",
                "incident",
                "Status Bulletin Correction: False Alarm Regarding External Attack",
                (
                    "# Incident Status Update — Correction Notice\n\n"
                    "## Correction\n"
                    "Security triage earlier announced a suspected distributed denial of service attack.\n"
                    "Forensic inspection of access telemetry confirmed NO malicious attack occurred. "
                    "The elevated HTTP 429 rate was caused by an internal configuration deployment that set token refill rates "
                    "to 10 req/s instead of 1,000 req/s. Configuration has been corrected."
                ),
            ),
        ]

        for i in range(30):
            author = self.rng.choice(users)
            ev_id, svc_id, st_type, title_base, body = correction_templates[i % len(correction_templates)]
            variant = (i // len(correction_templates)) + 1
            title = f"{title_base} (Follow-Up {variant})" if variant > 1 else title_base

            matching_events = [e for e in self.gen.events if e.event_id == ev_id]
            ev = matching_events[0] if matching_events else self.gen.events[0]
            # Correction occurs strictly after the initial incident
            created_at = (ev.end_time or ev.start_time) + timedelta(hours=self.rng.randint(2, 24))

            rec = SourceRecord(
                document_id=f"DOC-NOISE-CORR-{i+1:04d}",
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=title,
                content=body,
                author_id=author.user_id,
                department=author.department,
                created_at=created_at,
                updated_at=created_at + timedelta(hours=2),
                status="published",
                classification="internal",
                permissions=RecordPermissions(allowed_departments=["Engineering", "Customer Support", "Security"]),
                source_entity_id=None,
                source_entity_type=None,
                related_entity_ids=[ev.event_id, svc_id],
                authority_level="high",
            )
            self.records.append(rec)
            self.correction_count += 1
