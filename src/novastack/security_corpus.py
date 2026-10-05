"""Security metadata & authorization test corpus generator — Milestone 4D-1.

Constructs a controlled security and authorization test layer to evaluate
retrieval authorization boundaries before the retrieval engine and RAG pipelines
are built:
1. Cross-tenant isolation (NovaStack vs Orbital vs Pinecone with overlapping terminology)
2. Role-based access control (employee, engineer, support, manager, finance, hr, security_admin)
3. Classification boundaries (public, internal, confidential, restricted)
4. Department restrictions (Finance, HR, Security, Legal, Engineering, Customer Support)
5. Document-level permission restrictions (user-specific access, tight role overrides)
6. Version-specific permissions (version chains where permissions escalate over time)
7. Duplicate documents with different permissions (near-duplicates with contrasting ACLs)
8. Historical/superseded restricted documents (superseded documents that remain sensitive)

Strict boundary:
- Does NOT implement authorization enforcement or security middleware.
- Does NOT mutate canonical ground truth.
- Does NOT contain prompt injections, retrieval poison attacks, or malicious payloads.
- Fully deterministic with RANDOM_SEED = 20260909.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from novastack.config import RANDOM_SEED
from novastack.models import (
    RecordPermissions,
    SecurityFixture,
    SourceRecord,
    User,
)


class SecurityCorpusGenerator:
    """Generates a controlled security/authorization test corpus and ground-truth fixtures."""

    def __init__(self, generator: Any, seed: int = RANDOM_SEED) -> None:
        self.gen = generator
        self.seed = seed
        self.rng = random.Random(seed)
        self.records: list[SourceRecord] = []
        self.fixtures: list[SecurityFixture] = []

        # Scenario record counters
        self.cross_tenant_count = 0
        self.role_based_count = 0
        self.classification_count = 0
        self.department_count = 0
        self.document_permission_count = 0
        self.version_specific_count = 0
        self.duplicate_acl_count = 0
        self.superseded_restricted_count = 0

    def generate_corpus(self) -> list[SourceRecord]:
        """Generate all 100 security-focused source records."""
        self.records.clear()
        self.fixtures.clear()

        # Group users by tenant and department/role
        users_by_tenant: dict[str, list[User]] = {}
        for u in self.gen.users:
            users_by_tenant.setdefault(u.tenant_id, []).append(u)

        ns_users = users_by_tenant.get("TENANT-NOVASTACK", [])
        or_users = users_by_tenant.get("TENANT-ORBITAL", [])
        pc_users = users_by_tenant.get("TENANT-PINECONE", [])

        # 1. Cross-Tenant Isolation (18 records)
        self._generate_cross_tenant_records(ns_users, or_users, pc_users)

        # 2. Role-Based Access Control (14 records)
        self._generate_role_based_records(ns_users)

        # 3. Classification Boundaries (16 records)
        self._generate_classification_records(ns_users)

        # 4. Department Restrictions (18 records)
        self._generate_department_records(ns_users)

        # 5. Document-Level Permission Restrictions (12 records)
        self._generate_document_permission_records(ns_users)

        # 6. Version-Specific Permissions (12 records across 4 chains)
        self._generate_version_permission_records(ns_users)

        # 7. Duplicate Documents with Different Permissions (10 records across 5 pairs)
        self._generate_duplicate_acl_records(ns_users)

        # 8. Superseded Restricted Documents (10 records)
        self._generate_superseded_restricted_records(ns_users)

        # Generate Ground-Truth Security Test Fixtures
        self._generate_security_fixtures(users_by_tenant)

        return self.records

    def generate_fixtures(self) -> list[SecurityFixture]:
        """Return the generated security test fixtures (generates corpus if not already done)."""
        if not self.records or not self.fixtures:
            self.generate_corpus()
        return self.fixtures

    # ------------------------------------------------------------------
    # 1. Cross-Tenant Isolation (18 records: 6 topics x 3 tenants)
    # ------------------------------------------------------------------
    def _generate_cross_tenant_records(
        self, ns_users: list[User], or_users: list[User], pc_users: list[User]
    ) -> None:
        topics = [
            (
                "API Gateway & Perimeter Ingress Routing Architecture",
                "documentation",
                "Internal architecture specification for ingress traffic routing, TLS termination, and rate-limiting quotas at the cloud API gateway.",
            ),
            (
                "Production Database Connection Pooling & Failover Config",
                "engineering_note",
                "Technical runbook detailing PostgreSQL connection pool limits, PgBouncer pool sizing, and automated replica failover thresholds.",
            ),
            (
                "Payment Gateway Webhook Signature Verification Keys",
                "documentation",
                "Standard operating procedure for rotating merchant webhook HMAC SHA-256 signing secrets and replay prevention timestamps.",
            ),
            (
                "Incident Escalation On-Call Rotation & Paging Schedules",
                "policy",
                "Authoritative policy defining tier-1 through tier-3 on-call paging escalation SLAs, secondary engineer response windows, and severity triggers.",
            ),
            (
                "Enterprise Customer Billing Dispute & Reconciliation SLA",
                "documentation",
                "Operational guidelines for merchant dispute reconciliation, fee clawback procedures, and commercial arbitration thresholds.",
            ),
            (
                "Zero-Trust Teleport Bastion Access & SSH Session Recording",
                "documentation",
                "Security compliance guide for accessing production infrastructure via identity-aware bastions with automated audit logging.",
            ),
        ]

        tenants_info = [
            ("TENANT-NOVASTACK", "NovaStack", ns_users, "SVC-NS-0001"),
            ("TENANT-ORBITAL", "Orbital", or_users, "SVC-OR-0001"),
            ("TENANT-PINECONE", "Pinecone", pc_users, "SVC-PC-0001"),
        ]

        for topic_idx, (title_base, st_type, desc) in enumerate(topics):
            for t_idx, (tenant_id, tenant_name, users, svc_id) in enumerate(tenants_info):
                author = users[topic_idx % len(users)] if users else ns_users[0]
                rec_idx = topic_idx * 3 + t_idx + 1
                doc_id = f"DOC-SEC-TENT-{rec_idx:04d}"

                content = (
                    f"# {tenant_name} Technical Specification: {title_base}\n\n"
                    f"## Tenant Context\n"
                    f"Organization: {tenant_name} ({tenant_id})\n"
                    f"Confidentiality: Internal — Strictly proprietary to {tenant_name}.\n\n"
                    f"## Overview\n{desc}\n\n"
                    f"## System Details ({tenant_name})\n"
                    f"- Primary Service Identifier: {svc_id}\n"
                    f"- Cluster Environment: prod-{tenant_name.lower()}-cluster-us-east\n"
                    f"- Operational Owner: {author.name} ({author.email})\n"
                    f"- Secret Vault Namespace: vault/{tenant_name.lower()}/production\n\n"
                    f"Security Notice: Unauthorized access or cross-tenant dissemination is strictly prohibited."
                )

                created_at = datetime(2025, 2, 10, 9, 30) + timedelta(days=rec_idx * 4)

                self.records.append(
                    SourceRecord(
                        document_id=doc_id,
                        tenant_id=tenant_id,
                        source_type=st_type,
                        title=f"[{tenant_name}] {title_base}",
                        content=content,
                        author_id=author.user_id,
                        department="Engineering" if st_type != "policy" else "DevOps",
                        created_at=created_at,
                        updated_at=created_at + timedelta(days=2),
                        version="1.0",
                        status="published",
                        classification="internal",
                        permissions=RecordPermissions(
                            allowed_roles=["engineer", "employee"],
                            allowed_departments=["Engineering", "DevOps"],
                        ),
                        related_entity_ids=[svc_id],
                        authority_level="high" if st_type != "policy" else "authoritative",
                    )
                )
                self.cross_tenant_count += 1

    # ------------------------------------------------------------------
    # 2. Role-Based Access Control (14 records: 7 roles x 2)
    # ------------------------------------------------------------------
    def _generate_role_based_records(self, ns_users: list[User]) -> None:
        role_specs = [
            (
                "employee",
                "documentation",
                "Company-Wide Strategic Objectives & OKR Roadmap",
                "Internal enterprise briefing outlining company milestones, quarterly target performance metrics, and annual corporate strategy.",
                "internal",
            ),
            (
                "employee",
                "policy",
                "NovaStack Employee Workplace Standards & Code of Conduct",
                "Authoritative workplace policy governing ethical conduct, acceptable hardware use, communication etiquette, and reporting mechanisms.",
                "internal",
            ),
            (
                "engineer",
                "engineering_note",
                "Kubernetes Ingress Controller & Network Mesh Optimization",
                "Technical configuration notes detailing Envoy proxy connection tuning, HTTP/2 multiplexing, and MTU packet size optimizations.",
                "internal",
            ),
            (
                "engineer",
                "documentation",
                "Core Microservice Distributed Tracing & W3C Header Standards",
                "Engineering implementation guide for propagating traceparent context headers across payment and checkout service boundaries.",
                "internal",
            ),
            (
                "support",
                "documentation",
                "Tier-2 Customer Escalation & Merchant Ticket Triage SOP",
                "Operational guide for support engineers investigating transaction timeouts, processing refunds, and routing tier-3 bug reports.",
                "internal",
            ),
            (
                "support",
                "documentation",
                "Merchant Account Recovery & 2FA Reset Validation Protocol",
                "Standard operating procedure for verifying customer identity before resetting multi-factor authentication devices on business portals.",
                "internal",
            ),
            (
                "manager",
                "documentation",
                "Engineering Leadership Performance Calibration & Promotion Criteria",
                "Management guidance detailing leveling rubric definitions, peer review calibration workflows, and promotion committee requirements.",
                "confidential",
            ),
            (
                "manager",
                "meeting",
                "Management Sync: FY26 Headcount Allocation & Organizational Design",
                "Executive sync minutes discussing departmental headcount prioritization, budget constraints, and hiring targets across teams.",
                "confidential",
            ),
            (
                "finance",
                "documentation",
                "FY26 Cloud Infrastructure Capacity & Spend Model",
                "Financial forecasting model estimating AWS compute, RDS database reserve instances, and multi-region egress costs.",
                "confidential",
            ),
            (
                "finance",
                "policy",
                "Payment Interchange Fee Concessions & Tiered Volume Rebates",
                "Confidential commercial policy detailing margin thresholds, interchange fee schedules, and negotiated merchant volume discounts.",
                "confidential",
            ),
            (
                "hr",
                "documentation",
                "Confidential Employee Grievance Investigation Playbook",
                "People Operations guidelines for conducting workplace investigations, documenting witness testimonies, and resolving disputes.",
                "confidential",
            ),
            (
                "hr",
                "documentation",
                "Workplace Disability Accommodations & Medical Leave Guidelines",
                "Internal HR procedure for evaluating medical accommodation requests, FMLA tracking, and sensitive employee health records.",
                "confidential",
            ),
            (
                "security_admin",
                "documentation",
                "Production PKI Root Certificate Authority Key Rollover SOP",
                "Restricted security manual describing offline HSM ceremonies, root CA private key rotation, and mutual TLS trust bundle propagation.",
                "restricted",
            ),
            (
                "security_admin",
                "documentation",
                "Break-Glass Production Access & Emergency Credential Vault",
                "Restricted operational procedure for accessing one-time root database credentials during critical SEV-1 infrastructure incidents.",
                "restricted",
            ),
        ]

        for i, (role, st_type, title, desc, classification) in enumerate(role_specs):
            author = self.rng.choice(ns_users)
            doc_id = f"DOC-SEC-ROLE-{i+1:04d}"
            created_at = datetime(2025, 3, 1, 10, 0) + timedelta(days=i * 6)

            content = (
                f"# NovaStack Security Specification: {title}\n\n"
                f"## Authorization Constraint\n"
                f"- Required Role: {role}\n"
                f"- Classification: {classification.capitalize()}\n\n"
                f"## Document Purpose\n{desc}\n\n"
                f"## Operational Guidelines\n"
                f"Access to this operational procedure is strictly restricted to personnel holding the **{role}** credential role. "
                f"Automated access control proxies verify valid JWT role claims prior to granting document retrieval."
            )

            self.records.append(
                SourceRecord(
                    document_id=doc_id,
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=f"[Role: {role}] {title}",
                    content=content,
                    author_id=author.user_id,
                    department=author.department,
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=3),
                    version="1.0",
                    status="published",
                    classification=classification,
                    permissions=RecordPermissions(
                        allowed_roles=[role],
                        allowed_departments=[],
                    ),
                    authority_level="high" if st_type != "policy" else "authoritative",
                )
            )
            self.role_based_count += 1

    # ------------------------------------------------------------------
    # 3. Classification Boundaries (16 records: 4 levels x 4)
    # ------------------------------------------------------------------
    def _generate_classification_records(self, ns_users: list[User]) -> None:
        specs = [
            # Public (4 records)
            (
                "public",
                "documentation",
                "NovaStack Public Developer API Documentation & Getting Started",
                "Publicly accessible developer reference explaining REST API endpoints, JSON request schemas, and pagination parameters.",
                [],
                [],
            ),
            (
                "public",
                "policy",
                "NovaStack Public Acceptable Use Policy & Open API Terms",
                "Published customer terms defining acceptable API request volume, security scanning rules, and merchant service guidelines.",
                [],
                [],
            ),
            (
                "public",
                "documentation",
                "Open-Source Software License Notices & Attribution Directory",
                "Public compliance disclosure of third-party open-source components, Apache 2.0, and MIT licenses utilized by NovaStack.",
                [],
                [],
            ),
            (
                "public",
                "documentation",
                "NovaStack Security Whitepaper: Architecture & Compliance Overview",
                "Publicly sharable security whitepaper describing data encryption standards (AES-256), SOC 2 compliance, and cloud hosting architecture.",
                [],
                [],
            ),
            # Internal (4 records)
            (
                "internal",
                "documentation",
                "Internal Microservice Naming Conventions & Service Catalog Guidelines",
                "Guidelines for registering new services in NovaStack service catalog, DNS domain naming, and Git repo standards.",
                ["employee", "engineer"],
                ["Engineering", "DevOps", "Product"],
            ),
            (
                "internal",
                "documentation",
                "Corporate VPN Client & Teleport Zero-Trust Onboarding Guide",
                "Internal guide for newly onboarded staff configuring work laptops, SSO credentials, and development environment proxies.",
                ["employee"],
                [],
            ),
            (
                "internal",
                "meeting",
                "All-Hands Quarterly Town Hall Q&A & Product Milestone Review",
                "Internal transcript and action items from quarterly town hall meeting covering product roadmap progress and customer growth.",
                ["employee"],
                [],
            ),
            (
                "internal",
                "policy",
                "Corporate Laptop Equipment Refresh & Remote Hardware Policy",
                "Internal policy detailing eligibility windows for laptop hardware refreshes, peripheral reimbursements, and asset return procedures.",
                ["employee"],
                [],
            ),
            # Confidential (4 records)
            (
                "confidential",
                "documentation",
                "Q3 Enterprise Customer Renewal Projections & Pipeline Forecast",
                "Confidential commercial revenue analysis forecasting churn probability, pipeline renewals, and ARR expansions for top-50 accounts.",
                ["sales_manager", "finance", "manager"],
                ["Sales", "Finance"],
            ),
            (
                "confidential",
                "documentation",
                "Enterprise Customer Master Service Agreement (MSA) Legal Fallback Clauses",
                "Confidential legal playbook outlining non-standard indemnification caps, warranty concessions, and payment terms for Fortune 500 deals.",
                ["legal_counsel", "manager"],
                ["Legal", "Sales"],
            ),
            (
                "confidential",
                "documentation",
                "Annual Employee Salary Band Benchmarks & Equity Compensation Model",
                "Confidential compensation grids detailing base salary percentiles, target bonus structures, and equity grant ranges across levels.",
                ["hr", "manager"],
                ["HR", "Finance"],
            ),
            (
                "confidential",
                "engineering_note",
                "Pre-Release Vulnerability Disclosure: Third-Party Parser Advisory",
                "Confidential technical vulnerability assessment analyzing CVE risk in an external JSON parser before public patch release.",
                ["security_engineer", "security_admin", "senior_engineer"],
                ["Security", "Engineering"],
            ),
            # Restricted (4 records)
            (
                "restricted",
                "documentation",
                "Production Database Master Encryption Key Management & Backup Secrets",
                "Highly restricted cryptographic manual covering master KMS key hierarchies, envelope encryption keys, and cold-storage secrets.",
                ["security_admin"],
                ["Security"],
            ),
            (
                "restricted",
                "documentation",
                "Executive Board Meeting Minutes: M&A Strategic Acquisition Evaluation",
                "Restricted board meeting minutes deliberating the potential acquisition of a competing cloud observability analytics platform.",
                ["security_admin"],
                ["Legal"],
            ),
            (
                "restricted",
                "documentation",
                "Internal Security Audit: High-Privilege Insider Threat Investigation",
                "Restricted security investigative dossier regarding unauthorized database credential exfiltration attempts by a compromised contractor account.",
                ["security_admin"],
                ["Security"],
            ),
            (
                "restricted",
                "documentation",
                "Executive Officer Compensation Schedules & Severance Agreements",
                "Restricted C-suite employment contracts, retention covenants, golden parachute clauses, and board-approved equity vesting schedules.",
                ["security_admin"],
                ["HR", "Legal"],
            ),
        ]

        for i, (classification, st_type, title, desc, roles, depts) in enumerate(specs):
            author = self.rng.choice(ns_users)
            doc_id = f"DOC-SEC-CLS-{i+1:04d}"
            created_at = datetime(2025, 3, 15, 11, 0) + timedelta(days=i * 5)

            content = (
                f"# NovaStack Information Asset: {title}\n\n"
                f"## Security Classification: {classification.upper()}\n"
                f"- Allowed Roles: {', '.join(roles) if roles else 'All / Unrestricted'}\n"
                f"- Allowed Departments: {', '.join(depts) if depts else 'All / Unrestricted'}\n\n"
                f"## Content Abstract\n{desc}\n\n"
                f"## Data Handling Requirements\n"
                f"This document is classified as **{classification}**. "
                f"Dissemination is governed by NovaStack Information Security Policy SEC-POL-010. "
                f"Access requests require formal authorization review."
            )

            self.records.append(
                SourceRecord(
                    document_id=doc_id,
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=f"[{classification.capitalize()}] {title}",
                    content=content,
                    author_id=author.user_id,
                    department=author.department,
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=2),
                    version="1.0",
                    status="published",
                    classification=classification,
                    permissions=RecordPermissions(
                        allowed_roles=roles,
                        allowed_departments=depts,
                    ),
                    authority_level="authoritative" if st_type == "policy" else ("high" if classification in ("confidential", "restricted") else "medium"),
                )
            )
            self.classification_count += 1

    # ------------------------------------------------------------------
    # 4. Department Restrictions (18 records: 6 departments x 3)
    # ------------------------------------------------------------------
    def _generate_department_records(self, ns_users: list[User]) -> None:
        dept_specs = [
            # Finance (3 records)
            (
                "Finance",
                "documentation",
                "Quarterly Corporate Tax Provision & Jurisdiction Allocation",
                "Detailed computation of state and federal corporate tax provisions, deferred tax assets, and transfer pricing allocations.",
                "confidential",
            ),
            (
                "Finance",
                "documentation",
                "Enterprise Merchant Settlement Reconciliation & Escrow Ledger",
                "Audit documentation verifying daily settlement wire transfers, merchant reserve accounts, and payment processor interchange fees.",
                "confidential",
            ),
            (
                "Finance",
                "policy",
                "Corporate Capital Expenditure & Cloud Compute Authorization Limits",
                "Authoritative policy defining financial sign-off thresholds for multi-year cloud compute commitments and hardware purchases.",
                "confidential",
            ),
            # HR (3 records)
            (
                "HR",
                "documentation",
                "Performance Improvement Plan (PIP) Framework & Documentation Guide",
                "People Operations standard operating procedure for initiating performance counseling, setting 60-day milestones, and legal sign-off.",
                "confidential",
            ),
            (
                "HR",
                "documentation",
                "Confidential Employee Exit Interview Trends & Retention Analysis",
                "Internal HR analysis summarizing recurring feedback from departing employees regarding compensation, management, and tooling.",
                "confidential",
            ),
            (
                "HR",
                "policy",
                "Workplace Harassment Reporting & Independent Investigation Policy",
                "Mandatory HR policy establishing reporting channels, whistleblower protections, and external investigator protocols.",
                "confidential",
            ),
            # Security (3 records)
            (
                "Security",
                "documentation",
                "Annual Penetration Testing Report: Web Application Vulnerabilities",
                "Detailed technical findings from third-party penetration testing, analyzing SQL injection surface, CSRF tokens, and IDOR vectors.",
                "restricted",
            ),
            (
                "Security",
                "documentation",
                "Security Operations Center (SOC) Automated Incident Playbook",
                "Operational playbooks for investigating anomalous AWS CloudTrail API spikes, credential stuffing attacks, and bastion session hijack alerts.",
                "confidential",
            ),
            (
                "Security",
                "policy",
                "Zero-Trust Mandatory MFA & Hardware Security Key Mandate",
                "Authoritative cybersecurity policy requiring FIDO2 WebAuthn hardware keys for all production database and cloud console access.",
                "confidential",
            ),
            # Legal (3 records)
            (
                "Legal",
                "documentation",
                "Litigation Hold Notice: Document Preservation for Commercial Dispute",
                "Mandatory legal hold directive requiring preservation of email communications, Slack messages, and contract logs regarding deal dispute.",
                "confidential",
            ),
            (
                "Legal",
                "documentation",
                "Patent Portfolio Strategy Brief: Distributed Payment Processing",
                "Proprietary legal analysis detailing NovaStack patent application claims regarding low-latency distributed transaction ordering.",
                "confidential",
            ),
            (
                "Legal",
                "documentation",
                "Data Protection Addendum (DPA) & International Transfer Risk Assessment",
                "Legal compliance documentation analyzing cross-border customer data transfers under EU GDPR and UK adequacy regulations.",
                "confidential",
            ),
            # Engineering (3 records)
            (
                "Engineering",
                "documentation",
                "Core Transaction Routing Algorithm & Distributed Consensus Spec",
                "Proprietary technical design specification detailing NovaStack's distributed transaction commit log and deadlock resolution heuristics.",
                "internal",
            ),
            (
                "Engineering",
                "engineering_note",
                "Proprietary Real-Time Fraud Scoring Heuristics & Machine Learning Pipeline",
                "Architecture note describing low-latency feature stores, inference latency benchmarks, and threshold tuning for fraud detection models.",
                "internal",
            ),
            (
                "Engineering",
                "documentation",
                "Internal Microservice Service-Mesh Mutual TLS Topology",
                "Implementation details of Istio service mesh mTLS certificates, sidecar proxy CPU overhead, and inter-service routing policies.",
                "internal",
            ),
            # Customer Support (3 records)
            (
                "Customer Support",
                "documentation",
                "VIP Merchant Escalation Routing Rules & White-Glove Support SLA",
                "Tier-1 support routing criteria, expedited engineering paging triggers, and account manager notification rules for tier-1 merchants.",
                "internal",
            ),
            (
                "Customer Support",
                "documentation",
                "Merchant Chargeback Dispute Evidence Compilation SOP",
                "Standard operating procedure for gathering proof-of-delivery receipts, IP logs, and merchant transaction records to contest chargebacks.",
                "internal",
            ),
            (
                "Customer Support",
                "documentation",
                "Customer Churn Risk Signals & Proactive Support Intervention Playbook",
                "Support playbook outlining automated warning indicators (declining API calls, recurring timeout errors) and outreach protocols.",
                "internal",
            ),
        ]

        for i, (dept, st_type, title, desc, classification) in enumerate(dept_specs):
            author = self.rng.choice(ns_users)
            doc_id = f"DOC-SEC-DPT-{i+1:04d}"
            created_at = datetime(2025, 4, 1, 9, 0) + timedelta(days=i * 4)

            content = (
                f"# NovaStack Department Record: {title}\n\n"
                f"## Department Access Restriction\n"
                f"- Owning Department: {dept}\n"
                f"- Access Requirement: Active membership in the **{dept}** department\n"
                f"- Security Classification: {classification.capitalize()}\n\n"
                f"## Overview\n{desc}\n\n"
                f"## Departmental Policy Notice\n"
                f"This document is proprietary to the {dept} department. Personnel outside of {dept} "
                f"are not authorized to view or distribute this content without written sign-off from the department head."
            )

            self.records.append(
                SourceRecord(
                    document_id=doc_id,
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=f"[{dept}] {title}",
                    content=content,
                    author_id=author.user_id,
                    department=dept,
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=2),
                    version="1.0",
                    status="published",
                    classification=classification,
                    permissions=RecordPermissions(
                        allowed_roles=[],
                        allowed_departments=[dept],
                    ),
                    authority_level="authoritative" if st_type == "policy" else ("high" if classification in ("confidential", "restricted") else "medium"),
                )
            )
            self.department_count += 1

    # ------------------------------------------------------------------
    # 5. Document-Level Permission Restrictions (12 records)
    # ------------------------------------------------------------------
    def _generate_document_permission_records(self, ns_users: list[User]) -> None:
        # 6 user-specific records
        user_specific_topics = [
            ("Project Phoenix: Confidential Tiger Team Technical Review", "documentation", "Engineering", "confidential"),
            ("Special Investigation: Sensitive Patent Prior-Art Analysis", "documentation", "Legal", "confidential"),
            ("Private 1:1 Executive Leadership Feedback & Succession Notes", "meeting", "HR", "restricted"),
            ("Cross-Functional Security Incident Taskforce Findings", "incident", "Security", "restricted"),
            ("Custom Enterprise Deal Concession & Pricing Approval Memorandum", "documentation", "Sales", "confidential"),
            ("Confidential Infrastructure Disaster Recovery Failover Audit", "engineering_note", "DevOps", "confidential"),
        ]

        for i, (title, st_type, dept, classification) in enumerate(user_specific_topics):
            # Select 2 designated users
            user_pair = self.rng.sample(ns_users, 2)
            doc_id = f"DOC-SEC-USR-{i+1:04d}"
            created_at = datetime(2025, 4, 20, 14, 0) + timedelta(days=i * 6)

            allowed_ids = [u.user_id for u in user_pair]
            allowed_names = [f"{u.name} ({u.user_id})" for u in user_pair]

            content = (
                f"# User-Restricted Document: {title}\n\n"
                f"## Strict User-Level Permissions\n"
                f"Explicitly Authorized Personnel:\n"
                + "\n".join(f"- {name}" for name in allowed_names)
                + f"\n\nClassification: {classification.capitalize()}\n"
                f"Department: {dept}\n\n"
                f"## Content Details\n"
                f"This document contains privileged, project-specific sensitive material. "
                f"Regardless of general organizational role or department clearance, access is limited "
                f"strictly to the named individuals listed above."
            )

            self.records.append(
                SourceRecord(
                    document_id=doc_id,
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=f"[User-Restricted] {title}",
                    content=content,
                    author_id=user_pair[0].user_id,
                    department=dept,
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=1),
                    version="1.0",
                    status="published",
                    classification=classification,
                    permissions=RecordPermissions(
                        allowed_user_ids=allowed_ids,
                    ),
                    authority_level="high" if classification in ("confidential", "restricted") else "medium",
                )
            )
            self.document_permission_count += 1

        # 6 records where classification=internal, but permissions restrict to security_admin or specific team
        tight_internal_topics = [
            ("Internal Incident Triage: Production Key Access Log Analysis", "incident", ["security_admin"], "Security"),
            ("Production Gateway WAF Rule Bypass Investigation", "engineering_note", ["security_admin"], "Security"),
            ("Database Administrator Master Audit Trail & Privilege Elevation Log", "documentation", ["security_admin"], "DevOps"),
            ("Teleport Bastion Session Audit: Root Command History", "documentation", ["security_admin"], "Security"),
            ("Vulnerability Triage: Candidate Exploit Verification Notes", "engineering_note", ["security_admin"], "Security"),
            ("Automated Secrets Scanner False Positive Override Registry", "documentation", ["security_admin"], "Security"),
        ]

        for i, (title, st_type, roles, dept) in enumerate(tight_internal_topics):
            author = self.rng.choice(ns_users)
            doc_id = f"DOC-SEC-ACL-{i+1:04d}"
            created_at = datetime(2025, 5, 1, 10, 0) + timedelta(days=i * 6)

            content = (
                f"# Restricted Role Document: {title}\n\n"
                f"## Authorization Constraint\n"
                f"- General Classification: Internal (Standard Company Asset)\n"
                f"- Explicit Permission Override: Restricted to role **{roles[0]}**\n\n"
                f"## Note on Access Control Evaluation\n"
                f"Although this document is cataloged under general internal infrastructure, "
                f"document-level permissions enforce strict role verification. "
                f"Users possessing general internal clearance must be DENIED access unless they hold the **{roles[0]}** role."
            )

            self.records.append(
                SourceRecord(
                    document_id=doc_id,
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=f"[Strict Role] {title}",
                    content=content,
                    author_id=author.user_id,
                    department=dept,
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=2),
                    version="1.0",
                    status="published",
                    classification="internal",
                    permissions=RecordPermissions(
                        allowed_roles=roles,
                    ),
                    authority_level="high",
                )
            )
            self.document_permission_count += 1

    # ------------------------------------------------------------------
    # 6. Version-Specific Permissions (12 records: 4 chains x 3 versions)
    # ------------------------------------------------------------------
    def _generate_version_permission_records(self, ns_users: list[User]) -> None:
        chains = [
            (
                "Payment Microservice Security Architecture & Cryptographic Standards",
                "documentation",
                "Engineering",
                [
                    ("1.0", "internal", ["engineer"], [], "Monolithic baseline with standard encryption."),
                    ("2.0", "confidential", ["engineer", "security_admin"], [], "Microservice transition with dedicated HSM signing module."),
                    ("3.0", "restricted", ["security_admin"], [], "Zero-trust architecture enforcing hardware security key verification."),
                ],
            ),
            (
                "Corporate Operating Budget & Departmental Headcount Model",
                "documentation",
                "Finance",
                [
                    ("1.0", "internal", [], ["Finance", "Engineering"], "Initial operational headcount estimates shared across department leads."),
                    ("2.0", "confidential", [], ["Finance"], "Consolidated corporate budget with finalized salary band allocations."),
                    ("3.0", "restricted", ["manager", "finance"], ["Finance"], "Executive-approved final operating plan with executive compensation targets."),
                ],
            ),
            (
                "Major Data Breach & Security Incident Escalation Protocol",
                "documentation",
                "Security",
                [
                    ("1.0", "internal", [], ["Security", "DevOps"], "General incident triage steps and contact directory."),
                    ("2.0", "confidential", [], ["Security"], "Privileged incident response runbook with law enforcement notification rules."),
                    ("3.0", "restricted", ["security_admin"], [], "Restricted forensic response protocol with isolated evidence storage keys."),
                ],
            ),
            (
                "Customer Data Privacy & Cross-Border Information Transfer Policy",
                "policy",
                "Legal",
                [
                    ("1.0", "public", [], [], "Public customer privacy summary and cookie usage disclosures."),
                    ("2.0", "internal", [], ["Legal", "Security"], "Internal operational guidance for data classification and GDPR deletion requests."),
                    ("3.0", "confidential", ["legal_counsel", "security_admin"], ["Legal"], "Confidential regulatory audit filings and cross-border risk assessments."),
                ],
            ),
        ]

        for chain_idx, (title_base, st_type, dept, versions_info) in enumerate(chains):
            root_id = f"DOC-SEC-VACL-{chain_idx+1:02d}-V1"
            v2_id = f"DOC-SEC-VACL-{chain_idx+1:02d}-V2"
            v3_id = f"DOC-SEC-VACL-{chain_idx+1:02d}-V3"

            t1 = datetime(2025, 2, 1) + timedelta(days=chain_idx * 15)
            t2 = t1 + timedelta(days=90)
            t3 = t2 + timedelta(days=90)

            # v1
            v1_ver, v1_cls, v1_roles, v1_depts, v1_desc = versions_info[0]
            author1 = self.rng.choice(ns_users)
            v1_rec = SourceRecord(
                document_id=root_id,
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=f"{title_base} (v{v1_ver})",
                content=(
                    f"# {title_base} — Version {v1_ver}\n\n"
                    f"## Version Classification: {v1_cls.upper()}\n"
                    f"Status: Superseded | Effective: {t1.strftime('%B %Y')} – {t2.strftime('%B %Y')}\n\n"
                    f"## Specification Details\n{v1_desc}\n\n"
                    f"Historical note: Access to version {v1_ver} was governed under {v1_cls} classification rules."
                ),
                author_id=author1.user_id,
                department=dept,
                created_at=t1,
                updated_at=t1 + timedelta(days=5),
                valid_from=t1,
                valid_until=t2,
                version=v1_ver,
                status="superseded",
                classification=v1_cls,
                permissions=RecordPermissions(allowed_roles=v1_roles, allowed_departments=v1_depts),
                authority_level="low",
            )

            # v2
            v2_ver, v2_cls, v2_roles, v2_depts, v2_desc = versions_info[1]
            author2 = self.rng.choice(ns_users)
            v2_rec = SourceRecord(
                document_id=v2_id,
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=f"{title_base} (v{v2_ver})",
                content=(
                    f"# {title_base} — Version {v2_ver}\n\n"
                    f"## Version Classification: {v2_cls.upper()}\n"
                    f"Notice: Supersedes {root_id} | Status: Superseded\n"
                    f"Effective: {t2.strftime('%B %Y')} – {t3.strftime('%B %Y')}\n\n"
                    f"## Specification Details\n{v2_desc}\n\n"
                    f"Security note: Permissions escalated from {v1_cls} to {v2_cls} due to sensitive architectural additions."
                ),
                author_id=author2.user_id,
                department=dept,
                created_at=t2,
                updated_at=t2 + timedelta(days=5),
                valid_from=t2,
                valid_until=t3,
                version=v2_ver,
                status="superseded",
                classification=v2_cls,
                permissions=RecordPermissions(allowed_roles=v2_roles, allowed_departments=v2_depts),
                parent_id=root_id,
                supersedes_id=root_id,
                authority_level="medium",
            )

            # v3
            v3_ver, v3_cls, v3_roles, v3_depts, v3_desc = versions_info[2]
            author3 = self.rng.choice(ns_users)
            v3_rec = SourceRecord(
                document_id=v3_id,
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=f"{title_base} (v{v3_ver}) [CURRENT]",
                content=(
                    f"# {title_base} — Version {v3_ver} [CURRENT]\n\n"
                    f"## Active Classification: {v3_cls.upper()}\n"
                    f"Notice: Supersedes {v2_id} | Status: Published (Active)\n\n"
                    f"## Specification Details\n{v3_desc}\n\n"
                    f"Security note: Current active standard classified as {v3_cls}. Access strictly enforced."
                ),
                author_id=author3.user_id,
                department=dept,
                created_at=t3,
                updated_at=t3 + timedelta(days=5),
                valid_from=t3,
                valid_until=None,
                version=v3_ver,
                status="published",
                classification=v3_cls,
                permissions=RecordPermissions(allowed_roles=v3_roles, allowed_departments=v3_depts),
                parent_id=root_id,
                supersedes_id=v2_id,
                authority_level="high" if st_type != "policy" else "authoritative",
            )

            self.records.extend([v1_rec, v2_rec, v3_rec])
            self.version_specific_count += 3

    # ------------------------------------------------------------------
    # 7. Duplicate Documents with Different Permissions (10 records: 5 pairs)
    # ------------------------------------------------------------------
    def _generate_duplicate_acl_records(self, ns_users: list[User]) -> None:
        pairs = [
            (
                "Production Incident Postmortem: Core Checkout Outage",
                "postmortem",
                "Engineering",
                # Doc A: Sanitized internal postmortem
                ("DOC-SEC-DUP-0001", "internal", ["engineer", "employee"], [], "[Sanitized Team Copy]"),
                # Doc B: Unredacted restricted postmortem with raw customer PII & IPs
                ("DOC-SEC-DUP-0002", "restricted", ["security_admin"], [], "[Restricted Unredacted Audit Copy]"),
                (
                    "Executive Summary: On 2025-01-14, checkout-service experienced connection starvation. "
                    "All microservice database connection pool metrics saturated. Root cause: connection timeout misconfiguration."
                ),
            ),
            (
                "NovaStack Cloud REST API Integration Architecture Guide",
                "documentation",
                "Engineering",
                # Doc A: Public merchant guide
                ("DOC-SEC-DUP-0003", "public", [], [], "[Public Merchant Edition]"),
                # Doc B: Internal engineering guide with staging IP endpoints & internal gateway secrets
                ("DOC-SEC-DUP-0004", "internal", ["engineer"], ["Engineering"], "[Internal Developer Edition]"),
                (
                    "API Integration Guide: Standard authorization headers use Bearer tokens with OAuth2. "
                    "Rate limiting enforced at 100 requests/second per merchant API key. Endpoints support JSON payloads."
                ),
            ),
            (
                "Corporate Compensation Benchmarks & Salary Structure Review",
                "documentation",
                "HR",
                # Doc A: General salary band ranges for HR staff
                ("DOC-SEC-DUP-0005", "internal", ["hr", "manager"], ["HR"], "[General Staff Salary Bands]"),
                # Doc B: Executive compensation & individual equity target grants
                ("DOC-SEC-DUP-0006", "restricted", ["manager"], ["Finance", "HR"], "[Restricted Executive Equity Grants]"),
                (
                    "Compensation Planning Review: Standard base salary percentiles calibrated against peer fintech benchmarks. "
                    "Target bonus eligibility is calculated based on annual corporate EBITDA performance milestones."
                ),
            ),
            (
                "Quarterly Vulnerability Assessment & Penetration Audit Report",
                "documentation",
                "Security",
                # Doc A: High-level remediated vulnerabilities summary for engineering
                ("DOC-SEC-DUP-0007", "internal", ["engineer"], ["Engineering", "Security"], "[Remediation Summary for Engineering]"),
                # Doc B: Unpatched zero-day exploit details and payload logs
                ("DOC-SEC-DUP-0008", "restricted", ["security_admin"], ["Security"], "[Restricted Active Exploit Dossier]"),
                (
                    "Vulnerability Audit Report: Evaluated perimeter API gateways, authentication token handlers, and TLS cipher suites. "
                    "Identified session replay risk in legacy token refresh endpoint. Immediate patch deployed across gateway nodes."
                ),
            ),
            (
                "Enterprise Merchant Transaction Processing Fee Schedule",
                "documentation",
                "Finance",
                # Doc A: Standard rate cards shared with sales and support
                ("DOC-SEC-DUP-0009", "internal", ["support", "sales"], ["Sales", "Customer Support"], "[Standard Merchant Rate Card]"),
                # Doc B: Custom interchange fee concessions and volume rebates for top tier accounts
                ("DOC-SEC-DUP-0010", "confidential", ["finance", "manager"], ["Finance", "Legal"], "[Confidential Custom Rebate Schedule]"),
                (
                    "Fee Schedule Document: Standard transaction processing fee is 2.9% + $0.30 per successful credit card authorization. "
                    "Alternative payment methods (ACH, instant debit) incur a discounted 0.8% processing fee."
                ),
            ),
        ]

        for i, (title_base, st_type, dept, doc_a_spec, doc_b_spec, body_core) in enumerate(pairs):
            author = self.rng.choice(ns_users)
            created_at = datetime(2025, 5, 20, 11, 0) + timedelta(days=i * 6)

            doc_a_id, a_cls, a_roles, a_depts, a_prefix = doc_a_spec
            doc_b_id, b_cls, b_roles, b_depts, b_prefix = doc_b_spec

            # Doc A (Broader access)
            rec_a = SourceRecord(
                document_id=doc_a_id,
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=f"{a_prefix} {title_base}",
                content=(
                    f"# {title_base} — Public/Internal Copy\n\n"
                    f"Classification: {a_cls.capitalize()}\n"
                    f"Permitted Roles: {', '.join(a_roles) if a_roles else 'All'}\n\n"
                    f"## Core Summary\n{body_core}\n\n"
                    f"Operational Context: Standard document copy intended for general technical and operational reference."
                ),
                author_id=author.user_id,
                department=dept,
                created_at=created_at,
                updated_at=created_at + timedelta(days=1),
                version="1.0",
                status="published",
                classification=a_cls,
                permissions=RecordPermissions(allowed_roles=a_roles, allowed_departments=a_depts),
                authority_level="high" if st_type == "postmortem" else "medium",
            )

            # Doc B (Restricted duplicate)
            rec_b = SourceRecord(
                document_id=doc_b_id,
                tenant_id="TENANT-NOVASTACK",
                source_type=st_type,
                title=f"{b_prefix} {title_base}",
                content=(
                    f"# {title_base} — Restricted Security Copy\n\n"
                    f"Classification: {b_cls.upper()} [STRICT CONFIDENTIALITY]\n"
                    f"Permitted Roles: {', '.join(b_roles)}\n"
                    f"Cross-Reference: Parallels {doc_a_id} with unredacted sensitive disclosures.\n\n"
                    f"## Core Summary\n{body_core}\n\n"
                    f"Privileged Disclosures:\n"
                    f"- Unredacted technical traces, sensitive customer merchant IDs, and root credentials included.\n"
                    f"- Strictly prohibited from unauthorized dissemination."
                ),
                author_id=author.user_id,
                department=dept,
                created_at=created_at,
                updated_at=created_at + timedelta(days=1),
                version="1.0",
                status="published",
                classification=b_cls,
                permissions=RecordPermissions(allowed_roles=b_roles, allowed_departments=b_depts),
                parent_id=doc_a_id,
                authority_level="high",
            )

            self.records.extend([rec_a, rec_b])
            self.duplicate_acl_count += 2

    # ------------------------------------------------------------------
    # 8. Superseded Restricted Documents (10 records)
    # ------------------------------------------------------------------
    def _generate_superseded_restricted_records(self, ns_users: list[User]) -> None:
        superseded_specs = [
            (
                "2024 Production Database Master Encryption Key Management SOP",
                "documentation",
                "Security",
                "restricted",
                ["security_admin"],
                [],
                "Historical documentation of deprecated AES-128 master database encryption keys and retired KMS key store passphrases.",
            ),
            (
                "FY2024 Executive Bonus Calculation Model & Individual Allocations",
                "documentation",
                "Finance",
                "restricted",
                ["finance", "manager"],
                ["Finance"],
                "Archived executive performance evaluations, discretionary bonus percentages, and equity acceleration schedules for 2024.",
            ),
            (
                "Legacy Zero-Trust Network Perimeter Firewall Passwords & Pre-Shared Keys",
                "documentation",
                "Security",
                "restricted",
                ["security_admin"],
                ["Security"],
                "Decommissioned VPN pre-shared secrets, gateway administrative passwords, and legacy router credentials.",
            ),
            (
                "Project Titan M&A Due Diligence Preliminary Legal Assessment",
                "documentation",
                "Legal",
                "confidential",
                ["legal_counsel", "manager"],
                ["Legal"],
                "Archived legal review and valuation due diligence regarding confidential merger negotiations conducted in early 2024.",
            ),
            (
                "2024 SOC 2 Type II Formal Exception Log & Remediation Findings",
                "documentation",
                "Security",
                "restricted",
                ["security_admin"],
                ["Security"],
                "Historical audit findings detailing unpatched legacy servers, missing change management approvals, and remediated access exceptions.",
            ),
            (
                "Payment Gateway Merchant Private Key Generation Protocol (v1.0)",
                "documentation",
                "Security",
                "restricted",
                ["security_admin"],
                ["Security"],
                "Deprecated algorithm for merchant RSA 2048-bit private key generation and seed entropy generation.",
            ),
            (
                "Internal Fraud Investigation: Merchant Account Takeover Incident Report",
                "incident",
                "Security",
                "restricted",
                ["security_admin"],
                ["Security"],
                "Archived forensic report detailing a coordinated account takeover ring targeting high-volume enterprise merchants in 2024.",
            ),
            (
                "Enterprise Customer Commercial Settlement Agreement Terms & Release",
                "documentation",
                "Legal",
                "confidential",
                ["legal_counsel"],
                ["Legal"],
                "Superseded dispute settlement agreement detailing non-disclosure terms, liquidated damages payments, and mutual liability releases.",
            ),
            (
                "Q1 2025 Preliminary Revenue Recognition & Corporate Tax Filing Draft",
                "documentation",
                "Finance",
                "confidential",
                ["finance"],
                ["Finance"],
                "Archived internal tax schedules and preliminary revenue recognition figures prior to formal external audit approval.",
            ),
            (
                "Employee Leveling Matrix & Unadjusted Historical Equity Bands (2024)",
                "documentation",
                "HR",
                "confidential",
                ["hr", "manager"],
                ["HR"],
                "Historical salary leveling benchmarks, unvested options calculation matrices, and legacy compensation policies.",
            ),
        ]

        for i, (title_base, st_type, dept, classification, roles, depts, desc) in enumerate(superseded_specs):
            author = self.rng.choice(ns_users)
            doc_id = f"DOC-SEC-SUP-{i+1:04d}"
            t_created = datetime(2024, 6, 1) + timedelta(days=i * 20)
            t_superseded = t_created + timedelta(days=180)

            content = (
                f"# [SUPERSEDED] {title_base}\n\n"
                f"## Status: SUPERSEDED (Historical Archive)\n"
                f"Classification: {classification.upper()} [REMAINS RESTRICTED]\n"
                f"Permitted Roles: {', '.join(roles)}\n"
                f"Effective Window: {t_created.strftime('%Y-%m-%d')} to {t_superseded.strftime('%Y-%m-%d')}\n\n"
                f"## Document Abstract\n{desc}\n\n"
                f"## Security Notice on Superseded Sensitive Documents\n"
                f"CRITICAL: Although this operational document is marked with status='superseded', "
                f"the sensitive intellectual property and credential disclosures contained herein "
                f"REMAIN STRICTLY RESTRICTED. Superseded status does NOT downgrade security classification "
                f"or permit public or unauthorized access."
            )

            self.records.append(
                SourceRecord(
                    document_id=doc_id,
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=f"[Superseded] {title_base}",
                    content=content,
                    author_id=author.user_id,
                    department=dept,
                    created_at=t_created,
                    updated_at=t_superseded,
                    valid_from=t_created,
                    valid_until=t_superseded,
                    version="0.9",
                    status="superseded",
                    classification=classification,
                    permissions=RecordPermissions(allowed_roles=roles, allowed_departments=depts),
                    authority_level="high",
                )
            )
            self.superseded_restricted_count += 1

    # ------------------------------------------------------------------
    # Ground-Truth Security Test Fixtures
    # ------------------------------------------------------------------
    def _generate_security_fixtures(self, users_by_tenant: dict[str, list[User]]) -> None:
        """Construct explicit, verifiable security ground-truth test fixtures."""
        ns_users = users_by_tenant.get("TENANT-NOVASTACK", [])
        or_users = users_by_tenant.get("TENANT-ORBITAL", [])
        pc_users = users_by_tenant.get("TENANT-PINECONE", [])

        # Helpers to find representative test users
        def find_user(users: list[User], role_keyword: str, dept: str | None = None) -> User:
            for u in users:
                if dept and u.department != dept:
                    continue
                if role_keyword in u.role:
                    return u
            # Fallback
            for u in users:
                if dept and u.department == dept:
                    return u
            return users[0]

        eng_user = find_user(ns_users, "engineer", "Engineering")
        devops_user = find_user(ns_users, "engineer", "DevOps")
        sec_user = find_user(ns_users, "security", "Security")
        sales_user = find_user(ns_users, "sales", "Sales")
        prod_user = find_user(ns_users, "product", "Product")
        orbital_eng = find_user(or_users, "engineer", "Engineering")
        orbital_sales = find_user(or_users, "sales", "Sales")
        pinecone_eng = find_user(pc_users, "engineer", "Engineering")

        # 1. authorized engineer accessing engineering document (ALLOW)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0001",
                security_scenario="role_based",
                target_document_id="DOC-SEC-ROLE-0003",
                expected_access="allow",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                required_roles=["engineer"],
                security_reason="Authorized engineer has role 'engineer' matching document allowed_roles.",
            )
        )

        # 2. unauthorized employee attempting restricted engineering document (DENY)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0002",
                security_scenario="role_based",
                target_document_id="DOC-SEC-ROLE-0003",
                expected_access="deny",
                test_user_id=sales_user.user_id,
                test_user_role=sales_user.role,
                test_user_department=sales_user.department,
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                required_roles=["engineer"],
                security_reason="Sales user lacks required 'engineer' role for engineering document.",
            )
        )

        # 3. finance user accessing finance confidential document (ALLOW)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0003",
                security_scenario="department",
                target_document_id="DOC-SEC-DPT-0001",
                expected_access="allow",
                test_user_id=ns_users[0].user_id,  # Evaluated as finance role/dept
                test_user_role="finance",
                test_user_department="Finance",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="confidential",
                required_department="Finance",
                security_reason="Finance department member accessing confidential Finance tax document.",
            )
        )

        # 4. non-finance user denied finance document (DENY)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0004",
                security_scenario="department",
                target_document_id="DOC-SEC-DPT-0001",
                expected_access="deny",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="confidential",
                required_department="Finance",
                security_reason="Engineering user denied access to Finance confidential document.",
            )
        )

        # 5. HR document restricted to HR (ALLOW for HR, DENY for non-HR)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0005",
                security_scenario="department",
                target_document_id="DOC-SEC-DPT-0004",
                expected_access="allow",
                test_user_id=ns_users[1].user_id,
                test_user_role="hr",
                test_user_department="HR",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="confidential",
                required_department="HR",
                security_reason="HR department member authorized to access confidential HR PIP framework.",
            )
        )
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0005-DENY",
                security_scenario="department",
                target_document_id="DOC-SEC-DPT-0004",
                expected_access="deny",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="confidential",
                required_department="HR",
                security_reason="Engineering user denied access to confidential HR PIP documentation.",
            )
        )

        # 6. security incident restricted to security_admin (ALLOW / DENY)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0006",
                security_scenario="document_permission",
                target_document_id="DOC-SEC-ACL-0001",
                expected_access="allow",
                test_user_id=sec_user.user_id,
                test_user_role="security_admin",
                test_user_department="Security",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                required_roles=["security_admin"],
                security_reason="Security administrator possesses required 'security_admin' role claim.",
            )
        )
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0006-DENY",
                security_scenario="document_permission",
                target_document_id="DOC-SEC-ACL-0001",
                expected_access="deny",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                required_roles=["security_admin"],
                security_reason="General engineer denied access to document requiring explicit 'security_admin' role.",
            )
        )

        # 7. cross-tenant similarly named document (DENY across tenants, ALLOW within tenant)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0007",
                security_scenario="cross_tenant",
                target_document_id="DOC-SEC-TENT-0002",  # Orbital doc
                expected_access="deny",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-ORBITAL",
                classification="internal",
                security_reason="Cross-tenant breach prevented: NovaStack employee denied Orbital internal document.",
                related_document_ids=["DOC-SEC-TENT-0001"],
            )
        )
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0007-ALLOW",
                security_scenario="cross_tenant",
                target_document_id="DOC-SEC-TENT-0002",
                expected_access="allow",
                test_user_id=orbital_eng.user_id,
                test_user_role=orbital_eng.role,
                test_user_department=orbital_eng.department,
                test_user_tenant="TENANT-ORBITAL",
                expected_tenant="TENANT-ORBITAL",
                classification="internal",
                security_reason="Orbital employee authorized to access Orbital's own internal specification.",
            )
        )
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0007-PC-DENY",
                security_scenario="cross_tenant",
                target_document_id="DOC-SEC-TENT-0003",  # Pinecone doc
                expected_access="deny",
                test_user_id=orbital_sales.user_id,
                test_user_role=orbital_sales.role,
                test_user_department=orbital_sales.department,
                test_user_tenant="TENANT-ORBITAL",
                expected_tenant="TENANT-PINECONE",
                classification="internal",
                security_reason="Cross-tenant breach prevented: Orbital user denied Pinecone internal document.",
                related_document_ids=["DOC-SEC-TENT-0003"],
            )
        )

        # 8. duplicate document with different ACL (ALLOW on Doc A, DENY on Doc B)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0008-ALLOW",
                security_scenario="duplicate_acl",
                target_document_id="DOC-SEC-DUP-0001",  # Doc A: internal, engineer
                expected_access="allow",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                required_roles=["engineer", "employee"],
                security_reason="Engineer authorized to access sanitized public/internal postmortem copy.",
                related_document_ids=["DOC-SEC-DUP-0002"],
            )
        )
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0008-DENY",
                security_scenario="duplicate_acl",
                target_document_id="DOC-SEC-DUP-0002",  # Doc B: restricted, security_admin
                expected_access="deny",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="restricted",
                required_roles=["security_admin"],
                security_reason="Engineer denied access to restricted unredacted audit duplicate requiring 'security_admin'.",
                related_document_ids=["DOC-SEC-DUP-0001"],
            )
        )

        # 9. restricted superseded document (DENY general employee, ALLOW security_admin)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0009",
                security_scenario="superseded_restricted",
                target_document_id="DOC-SEC-SUP-0001",
                expected_access="deny",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="restricted",
                required_roles=["security_admin"],
                security_reason="General engineer denied access to superseded restricted key document; status='superseded' does NOT grant public access.",
            )
        )
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0009-ALLOW",
                security_scenario="superseded_restricted",
                target_document_id="DOC-SEC-SUP-0001",
                expected_access="allow",
                test_user_id=sec_user.user_id,
                test_user_role="security_admin",
                test_user_department="Security",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="restricted",
                required_roles=["security_admin"],
                security_reason="Security administrator authorized to view archived master encryption key manual.",
            )
        )

        # 10. historical version with different permissions (ALLOW v1, DENY v3)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0010-V1-ALLOW",
                security_scenario="version_specific",
                target_document_id="DOC-SEC-VACL-01-V1",  # v1: internal, engineer
                expected_access="allow",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                required_roles=["engineer"],
                security_reason="Engineer authorized to access version 1.0 which had 'internal' classification.",
                related_document_ids=["DOC-SEC-VACL-01-V2", "DOC-SEC-VACL-01-V3"],
            )
        )
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0010-V3-DENY",
                security_scenario="version_specific",
                target_document_id="DOC-SEC-VACL-01-V3",  # v3: restricted, security_admin
                expected_access="deny",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="restricted",
                required_roles=["security_admin"],
                security_reason="Engineer denied version 3.0 because specification escalated from 'internal' to 'restricted'.",
                related_document_ids=["DOC-SEC-VACL-01-V1", "DOC-SEC-VACL-01-V2"],
            )
        )

        # 11. internal document with user-specific permission (ALLOW listed user, DENY unlisted user)
        usr_rec = next(r for r in self.records if r.document_id == "DOC-SEC-USR-0001")
        allowed_uid = usr_rec.permissions.allowed_user_ids[0]
        unlisted_user = next(u for u in ns_users if u.user_id not in usr_rec.permissions.allowed_user_ids)

        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0011-ALLOW",
                security_scenario="document_permission",
                target_document_id="DOC-SEC-USR-0001",
                expected_access="allow",
                test_user_id=allowed_uid,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="confidential",
                allowed_user_ids=usr_rec.permissions.allowed_user_ids,
                security_reason="User explicitly listed in document-level allowed_user_ids access list.",
            )
        )
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0011-DENY",
                security_scenario="document_permission",
                target_document_id="DOC-SEC-USR-0001",
                expected_access="deny",
                test_user_id=unlisted_user.user_id,
                test_user_role=unlisted_user.role,
                test_user_department=unlisted_user.department,
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="confidential",
                allowed_user_ids=usr_rec.permissions.allowed_user_ids,
                security_reason="User omitted from explicit document-level allowed_user_ids list.",
            )
        )

        # 12. document where classification and explicit permissions must both be considered (DENY)
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0012",
                security_scenario="classification_and_permission",
                target_document_id="DOC-SEC-ACL-0002",
                expected_access="deny",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                required_roles=["security_admin"],
                security_reason="User has 'internal' classification clearance, but document-level permission requires 'security_admin' role.",
            )
        )
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0012-ALLOW",
                security_scenario="classification_and_permission",
                target_document_id="DOC-SEC-ACL-0002",
                expected_access="allow",
                test_user_id=sec_user.user_id,
                test_user_role="security_admin",
                test_user_department="Security",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                required_roles=["security_admin"],
                security_reason="User satisfies both 'internal' classification clearance and required 'security_admin' role.",
            )
        )

        # Additional fixtures across categories to ensure robust test harness coverage
        # Public classification accessible to all
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-CLS-PUB-01",
                security_scenario="classification",
                target_document_id="DOC-SEC-CLS-0001",
                expected_access="allow",
                test_user_id=orbital_eng.user_id,
                test_user_role=orbital_eng.role,
                test_user_department=orbital_eng.department,
                test_user_tenant="TENANT-ORBITAL",
                expected_tenant="TENANT-NOVASTACK",
                classification="public",
                security_reason="Public documents are universally accessible across tenants and roles.",
            )
        )
        # Restricted classification denied to general internal user
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-CLS-RES-01",
                security_scenario="classification",
                target_document_id="DOC-SEC-CLS-0013",
                expected_access="deny",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="restricted",
                required_roles=["security_admin"],
                security_reason="General engineer denied access to Restricted cryptographic master key documentation.",
            )
        )

        # Cross-tenant: Pinecone user authorized for Pinecone document
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0007-PC-ALLOW",
                security_scenario="cross_tenant",
                target_document_id="DOC-SEC-TENT-0003",
                expected_access="allow",
                test_user_id=pinecone_eng.user_id,
                test_user_role=pinecone_eng.role,
                test_user_department=pinecone_eng.department,
                test_user_tenant="TENANT-PINECONE",
                expected_tenant="TENANT-PINECONE",
                classification="internal",
                security_reason="Pinecone engineer authorized to access Pinecone internal document.",
            )
        )

        # Cross-tenant: Pinecone user denied NovaStack document
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-0007-NS-DENY",
                security_scenario="cross_tenant",
                target_document_id="DOC-SEC-TENT-0001",
                expected_access="deny",
                test_user_id=pinecone_eng.user_id,
                test_user_role=pinecone_eng.role,
                test_user_department=pinecone_eng.department,
                test_user_tenant="TENANT-PINECONE",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                security_reason="Cross-tenant isolation: Pinecone employee denied NovaStack internal document.",
                related_document_ids=["DOC-SEC-TENT-0003"],
            )
        )

        # Role: Support role accessing Support SOP
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-ROLE-SUP-ALLOW",
                security_scenario="role_based",
                target_document_id="DOC-SEC-ROLE-0005",
                expected_access="allow",
                test_user_id=ns_users[2].user_id,
                test_user_role="support",
                test_user_department="Customer Support",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                required_roles=["support"],
                security_reason="Support engineer authorized to access customer support escalation SOP.",
            )
        )

        # Role: Engineer denied Support SOP requiring role='support'
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-ROLE-SUP-DENY",
                security_scenario="role_based",
                target_document_id="DOC-SEC-ROLE-0005",
                expected_access="deny",
                test_user_id=eng_user.user_id,
                test_user_role="engineer",
                test_user_department="Engineering",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="internal",
                required_roles=["support"],
                security_reason="Engineer denied access to document requiring role='support'.",
            )
        )

        # Department: Legal department user authorized for Legal document
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-DPT-LGL-ALLOW",
                security_scenario="department",
                target_document_id="DOC-SEC-DPT-0010",
                expected_access="allow",
                test_user_id=ns_users[3].user_id,
                test_user_role="legal_counsel",
                test_user_department="Legal",
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="confidential",
                required_department="Legal",
                security_reason="Legal department personnel authorized to access litigation hold notice.",
            )
        )

        # Department: Sales user denied Legal document
        self.fixtures.append(
            SecurityFixture(
                fixture_id="FIX-SEC-DPT-LGL-DENY",
                security_scenario="department",
                target_document_id="DOC-SEC-DPT-0010",
                expected_access="deny",
                test_user_id=sales_user.user_id,
                test_user_role=sales_user.role,
                test_user_department=sales_user.department,
                test_user_tenant="TENANT-NOVASTACK",
                expected_tenant="TENANT-NOVASTACK",
                classification="confidential",
                required_department="Legal",
                security_reason="Sales user denied access to Legal department litigation hold notice.",
            )
        )
