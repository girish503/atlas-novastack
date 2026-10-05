"""Background enterprise corpus generator — Milestone 4B.

Generates ~400–500 realistic background enterprise source records across all 10
NovaStack departments (Engineering, Product, Customer Support, Sales, Finance,
HR, Security, Operations, Legal, DevOps / Infrastructure) that are NOT primarily
centered around the 12 canonical events.

Introduces realistic enterprise diversity and semantic distraction (routine payment
configs, ordinary checkout discussions, general latency benchmarks, normal onboarding)
so search retrieval must distinguish topic similarity from answer relevance.

Strict boundary:
- No deliberate near-duplicate attacks
- No stale or conflicting version attacks
- No prompt injection or retrieval poisoning
- No fake EVT-* / INC-* event provenance links
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from novastack.config import RANDOM_SEED
from novastack.models import (
    Customer,
    RecordPermissions,
    Service,
    SourceRecord,
    Team,
    User,
)


class BackgroundCorpusGenerator:
    """Generates realistic background enterprise source records across all 10 departments."""

    def __init__(self, generator: Any, seed: int = RANDOM_SEED) -> None:
        self.gen = generator
        self.seed = seed
        self.rng = random.Random(seed)
        self.records: list[SourceRecord] = []
        self._doc_counter = 1

    def _next_doc_id(self) -> str:
        doc_id = f"DOC-BKG-{self._doc_counter:04d}"
        self._doc_counter += 1
        return doc_id

    def _date_in_range(self, index: int, total: int) -> datetime:
        """Deterministically distribute timestamps across Jan 2025 – Dec 2026."""
        start_date = datetime(2025, 1, 6, 9, 0, 0)
        # Spanning ~710 days
        day_offset = int((index / max(1, total - 1)) * 710)
        jitter_hours = self.rng.randint(0, 8)
        jitter_mins = self.rng.randint(0, 59)
        return start_date + timedelta(days=day_offset, hours=jitter_hours, minutes=jitter_mins)

    def generate_corpus(self) -> list[SourceRecord]:
        """Generate the complete set of background enterprise records."""
        self.records.clear()
        self._doc_counter = 1

        tenant_id = "TENANT-NOVASTACK"
        ns_users = [u for u in self.gen.users if u.tenant_id == tenant_id]
        ns_teams = [t for t in self.gen.teams if t.tenant_id == tenant_id]
        ns_services = [s for s in self.gen.services if s.tenant_id == tenant_id]
        ns_customers = [c for c in self.gen.customers if c.tenant_id == tenant_id]

        users_by_dept: dict[str, list[User]] = {}
        for u in ns_users:
            users_by_dept.setdefault(u.department, []).append(u)

        # 1. Engineering (50 records)
        self._generate_engineering(ns_users, ns_teams, ns_services)

        # 2. Product (45 records)
        self._generate_product(ns_users, ns_teams, ns_services, ns_customers)

        # 3. Customer Support (85 records)
        self._generate_customer_support(ns_users, ns_teams, ns_services, ns_customers)

        # 4. Sales (45 records)
        self._generate_sales(ns_users, ns_teams, ns_customers)

        # 5. Finance (40 records)
        self._generate_finance(ns_users, ns_teams)

        # 6. HR (40 records)
        self._generate_hr(ns_users, ns_teams)

        # 7. Security (45 records)
        self._generate_security(ns_users, ns_teams, ns_services)

        # 8. Operations (35 records)
        self._generate_operations(ns_users, ns_teams)

        # 9. Legal (35 records)
        self._generate_legal(ns_users, ns_teams, ns_customers)

        # 10. DevOps / Infrastructure (50 records)
        self._generate_devops(ns_users, ns_teams, ns_services)

        return self.records

    # ------------------------------------------------------------------
    # 1. Engineering (50 records)
    # ------------------------------------------------------------------
    def _generate_engineering(
        self, users: list[User], teams: list[Team], services: list[Service]
    ) -> None:
        eng_users = [u for u in users if u.department == "Engineering"] or users
        perms_eng = RecordPermissions(
            allowed_roles=["engineer", "senior_engineer", "staff_engineer", "engineering_manager"],
            allowed_departments=["Engineering", "DevOps"],
        )

        eng_topics = [
            (
                "documentation",
                "high",
                "Architecture Decision Record: Database Connection Pooling & Circuit Breakers",
                (
                    "# ADR-042: Database Connection Pooling Strategy\n\n"
                    "## Context\n"
                    "Downstream database queries across microservices experience connection saturation during peak traffic. "
                    "Individual services previously created unbounded connection pools, leading to thread starvation.\n\n"
                    "## Decision\n"
                    "1. All Golang and Python services must standardize on PgBouncer connection pooling.\n"
                    "2. Maximum idle connection timeout is set to 300 seconds.\n"
                    "3. Acquire connection timeout is capped at 1,500ms before returning 503 Service Unavailable.\n"
                    "4. Client-side query timeout is enforced at 3,000ms for read replicas and 5,000ms for primary nodes.\n\n"
                    "## Consequences\n"
                    "Eliminates cascading database connection exhaustions. Services fail fast rather than hanging indefinitely."
                ),
            ),
            (
                "documentation",
                "high",
                "Service Guide: Checkout Microservice API Contracts & Idempotency",
                (
                    "# Service Specification: Checkout API v3\n\n"
                    "## Overview\n"
                    "The checkout service orchestrates order validation, inventory holds, and payment submission. "
                    "To prevent double-charging on network retries, all mutating endpoints require an Idempotency-Key header.\n\n"
                    "## Protocol\n"
                    "- `POST /v3/checkout/sessions`: Creates a hosted checkout session with a 15-minute expiration.\n"
                    "- `POST /v3/checkout/orders`: Commits the cart items and submits payment token.\n"
                    "- Latency SLA: p95 latency under 120ms; p99 latency under 250ms.\n"
                    "- Retry Policy: Exponential backoff with jitter on HTTP 502/504 responses."
                ),
            ),
            (
                "documentation",
                "high",
                "Coding Standards: REST API Error Handling & Timeout Propagations",
                (
                    "# Engineering Guidelines: Distributed Timeout Propagation\n\n"
                    "## Guidelines\n"
                    "Every inter-service HTTP and gRPC call must propagate contextual deadlines using W3C baggage.\n"
                    "1. Always set an explicit read timeout on outgoing HTTP client transports.\n"
                    "2. When upstream callers cancel a request, downstream database queries must be cancelled immediately.\n"
                    "3. Standard error payload must return RFC-7807 problem details with an `error_code` and `trace_id`."
                ),
            ),
            (
                "engineering_note",
                "medium",
                "Performance Benchmark: Checkout Flow Frontend Bundle & API Latency",
                (
                    "Engineering Analysis: Checkout Page Latency Profiling\n\n"
                    "Conducted web vitals profiling on modern desktop and mobile viewports.\n"
                    "- First Contentful Paint (FCP): 0.82s\n"
                    "- Largest Contentful Paint (LCP): 1.45s\n"
                    "- Time to Interactive (TTI): 1.80s\n"
                    "- Median API checkout submission roundtrip: 142ms\n\n"
                    "Identified 45KB of unused payment method polyfills in bundle vendor chunk. "
                    "Recommendation: Lazy-load Apple Pay and Google Pay SDK scripts only upon payment button interaction."
                ),
            ),
            (
                "engineering_note",
                "medium",
                "Database Migration Note: Index Tuning for Payment Ledger Transactions",
                (
                    "Database Optimization Notes: payment_transactions Table\n\n"
                    "Query telemetry showed sequential scans on `customer_id` and `created_at` during monthly statement exports.\n"
                    "Action Taken:\n"
                    "Created composite index `idx_payment_tx_cust_created` concurrently on `(customer_id, created_at DESC)`.\n"
                    "Observed index creation time: 4m 12s on staging; zero lock contention on concurrent writes.\n"
                    "Query execution latency dropped from 840ms to 14ms (98.3% improvement)."
                ),
            ),
            (
                "conversation",
                "low",
                "Slack Thread: #eng-backend — Discussion on Redis Cache Invalidation Strategies",
                (
                    "David Miller: Hey team, we're seeing occasional cache staleness when products are updated in the catalog.\n"
                    "Elena Rostova: Are we using write-through caching or explicit TTL eviction?\n"
                    "David Miller: Currently relying on 60-second TTL. But during flash sales, customers notice the 60s lag.\n"
                    "Marcus Vance: Let's publish a Redis pub/sub eviction event on catalog mutations instead. Simple invalidation key.\n"
                    "Elena Rostova: Agreed. Let's schedule a ticket for next sprint to switch from TTL polling to pub/sub eviction."
                ),
            ),
            (
                "meeting",
                "medium",
                "Meeting Minutes: Bi-Weekly Core Architecture & Latency Review",
                (
                    "Meeting: Core Engineering Architecture Sync\n"
                    "Attendees: Principal Engineers, Technical Leads\n\n"
                    "Agenda & Discussions:\n"
                    "1. Microservices Latency Budget: Reviewed service mesh latency telemetry across all tier-1 services.\n"
                    "2. Database Connection Limits: Confirmed PgBouncer rollouts completed for 9 of 12 production databases.\n"
                    "3. GraphQL Federation Pilot: Discussed schema stitching vs Apollo Federation for unified customer graph.\n\n"
                    "Action Items:\n"
                    "- Complete PgBouncer verification for remaining analytics databases.\n"
                    "- Draft architectural RFC on GraphQL client-side query timeouts."
                ),
            ),
        ]

        for i in range(50):
            author = self.rng.choice(eng_users)
            svc = self.rng.choice(services)
            created_at = self._date_in_range(i, 50)
            st_type, auth, title_base, content_base = eng_topics[i % len(eng_topics)]
            variant = (i // len(eng_topics)) + 1

            title = f"{title_base} (Part {variant})" if variant > 1 else title_base
            content = f"{content_base}\n\n---\nRef Service: {svc.name} ({svc.service_id})\nModule: eng-core-{i:03d}"

            self.records.append(
                SourceRecord(
                    document_id=self._next_doc_id(),
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=title,
                    content=content,
                    author_id=author.user_id,
                    department="Engineering",
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=self.rng.randint(1, 14)),
                    status="published",
                    classification="internal",
                    permissions=perms_eng,
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=[svc.service_id],
                    authority_level=auth,
                )
            )

    # ------------------------------------------------------------------
    # 2. Product (45 records)
    # ------------------------------------------------------------------
    def _generate_product(
        self, users: list[User], teams: list[Team], services: list[Service], customers: list[Customer]
    ) -> None:
        prod_users = [u for u in users if u.department == "Product"] or users
        perms_prod = RecordPermissions(
            allowed_roles=["product_manager", "designer", "engineer", "director"],
            allowed_departments=["Product", "Engineering", "Sales"],
        )

        prod_topics = [
            (
                "documentation",
                "high",
                "Product Requirements Document (PRD): One-Click Express Checkout Experience",
                (
                    "# PRD: Express Checkout & Saved Payment Methods\n\n"
                    "## Executive Summary\n"
                    "Customer drop-off during the final payment step accounts for 28% of abandoned cart sessions. "
                    "This PRD defines an accelerated checkout modal that allows authenticated customers to complete purchase in under 3 seconds.\n\n"
                    "## User Personas\n"
                    "- Returning B2B Buyer: Needs quick re-ordering without re-entering corporate credit card or billing details.\n"
                    "- Mobile Shopper: Prefers biometric Apple Pay / Google Pay authentication.\n\n"
                    "## Key Requirements\n"
                    "1. Automatic detection of stored payment tokens via customer vault.\n"
                    "2. Inline address autocomplete with postal validation.\n"
                    "3. Fallback to standard checkout if payment gateway responds with friction challenge."
                ),
            ),
            (
                "documentation",
                "high",
                "Feature Specification: Webhook Notification Center & Customer Delivery Logs",
                (
                    "# Feature Spec: Self-Service Customer Webhook Portal\n\n"
                    "## Problem Statement\n"
                    "Enterprise customers currently open support tickets whenever webhook delivery fails or times out. "
                    "They require real-time visibility into outgoing order and payment webhook attempts.\n\n"
                    "## Functional Scope\n"
                    "- Customer dashboard displaying last 5,000 webhook events.\n"
                    "- Manual retry button for failed delivery attempts (HTTP 4xx/5xx or timeout > 5,000ms).\n"
                    "- Configurable alert emails when delivery failure rate exceeds 10% over 1 hour."
                ),
            ),
            (
                "documentation",
                "high",
                "Product Roadmap: Q3/Q4 Multi-Currency Pricing & Localized Settlement",
                (
                    "# Strategic Roadmap: International Payment Expansion\n\n"
                    "## Goals\n"
                    "Expand platform checkout capabilities into European and Asia-Pacific markets.\n\n"
                    "## Milestone Deliverables\n"
                    "- Q3 M1: Support EUR and GBP direct billing with local SEPA and BACS rails.\n"
                    "- Q3 M2: Dynamic currency conversion at checkout based on customer IP geolocation.\n"
                    "- Q4 M1: Integration with regional payment processors to reduce cross-border transaction fees."
                ),
            ),
            (
                "meeting",
                "medium",
                "Meeting Notes: Quarterly Customer Advisory Board & Feature Feedback",
                (
                    "Meeting: Customer Advisory Board (CAB) — Product Review\n"
                    "Attendees: VP Product, Enterprise Customers, Account Leads\n\n"
                    "Discussion Highlights:\n"
                    "- Customers praised recent checkout reliability and reduced page load latency.\n"
                    "- Requested feature: Bulk invoice downloading and automated monthly payment reconciliation.\n"
                    "- Feedback noted: Staging sandbox needs faster reset mechanisms for payment testing.\n\n"
                    "Next Steps: Incorporate bulk export API into Q4 roadmap planning."
                ),
            ),
            (
                "conversation",
                "low",
                "Slack Thread: #product-feedback — Mobile Checkout UX Friction Observations",
                (
                    "Sarah Chen: Reviewed hotjar sessions for checkout flow on iOS Safari.\n"
                    "Alex Turner: Did you notice the virtual keyboard pushing the 'Place Order' button offscreen?\n"
                    "Sarah Chen: Yes, exactly! Users are scrolling back and forth trying to confirm the payment amount.\n"
                    "Alex Turner: Let's adjust viewport meta tags and pin the payment summary bar to bottom screen.\n"
                    "Sarah Chen: Great, creating a design polish ticket for this."
                ),
            ),
        ]

        for i in range(45):
            author = self.rng.choice(prod_users)
            cust = self.rng.choice(customers) if customers else None
            svc = self.rng.choice(services)
            created_at = self._date_in_range(i, 45)
            st_type, auth, title_base, content_base = prod_topics[i % len(prod_topics)]
            variant = (i // len(prod_topics)) + 1

            title = f"{title_base} (Iteration {variant})" if variant > 1 else title_base
            related = [svc.service_id]
            if cust:
                related.append(cust.customer_id)

            content = f"{content_base}\n\n---\nTarget Service: {svc.name}\nCustomer Focus: {cust.name if cust else 'General Enterprise'}"

            self.records.append(
                SourceRecord(
                    document_id=self._next_doc_id(),
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=title,
                    content=content,
                    author_id=author.user_id,
                    department="Product",
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=self.rng.randint(2, 20)),
                    status="published",
                    classification="internal",
                    permissions=perms_prod,
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=related,
                    authority_level=auth,
                )
            )

    # ------------------------------------------------------------------
    # 3. Customer Support (85 records)
    # ------------------------------------------------------------------
    def _generate_customer_support(
        self, users: list[User], teams: list[Team], services: list[Service], customers: list[Customer]
    ) -> None:
        support_users = users  # Support tickets assigned to NovaStack agents
        perms_support = RecordPermissions(
            allowed_roles=["support_tier1", "support_tier2", "customer_success", "account_manager"],
            allowed_departments=["Customer Support", "Sales", "Engineering"],
        )

        ticket_scenarios = [
            (
                "Assistance with Sandbox Payment Gateway Test Cards",
                (
                    "Customer Question:\n"
                    "We are integrating the checkout API in our staging environment and need a set of valid test credit card "
                    "numbers for simulating 3DS verification and insufficient funds responses.\n\n"
                    "Agent Response:\n"
                    "Hello! You can use our standard sandbox test card suite:\n"
                    "- 4242 4242 4242 4242: Success (any future expiry, CVC 123)\n"
                    "- 4000 0000 0000 0002: Simulates card declined / insufficient funds\n"
                    "- 4000 0000 0000 0322: Triggers 3DS challenge authentication\n"
                    "Please refer to developer documentation at docs.novastack.example/testing for complete payloads.\n\n"
                    "Resolution: Customer verified successful testing; ticket closed."
                ),
            ),
            (
                "How to Configure Webhook Retry Timeout Intervals",
                (
                    "Customer Question:\n"
                    "Our customer endpoint occasionally takes up to 4 seconds to process order confirmation webhooks. "
                    "Does NovaStack support custom timeout limits before triggering a retry?\n\n"
                    "Agent Response:\n"
                    "Hello. NovaStack default webhook timeout is 5,000ms. If your endpoint does not respond with HTTP 200 "
                    "within 5 seconds, our dispatcher executes exponential retries at intervals of 1m, 5m, 15m, and 1h.\n"
                    "You can customize the endpoint connection timeout under Developer Settings -> Webhooks -> Advanced.\n\n"
                    "Resolution: Guided customer through portal settings; verified successful delivery."
                ),
            ),
            (
                "Request to Increase API Rate Limit for Scheduled Data Sync",
                (
                    "Customer Question:\n"
                    "We run a nightly catalog reconciliation job at 02:00 UTC and currently hit HTTP 429 Too Many Requests. "
                    "Can we increase our burst limit from 500 req/min to 1,500 req/min?\n\n"
                    "Agent Response:\n"
                    "Hi there. We reviewed your production usage patterns and current tier. We have approved and applied a "
                    "temporary burst rate limit increase to 1,500 req/min during the 01:00–04:00 UTC window.\n"
                    "We also recommend utilizing our bulk upload API endpoint `/v2/catalog/batch` which consumes only 1 request per 250 items.\n\n"
                    "Resolution: Rate limit updated in rate-limiter service configuration."
                ),
            ),
            (
                "Updating Invoice Recipient Email & Corporate Billing Address",
                (
                    "Customer Question:\n"
                    "Our accounts payable team has changed. Please update our billing contact email to ap@acme.example "
                    "and update our European VAT registration number on future monthly statements.\n\n"
                    "Agent Response:\n"
                    "Thank you for contacting billing support. The billing contact address and VAT number have been verified "
                    "and updated in your enterprise account profile. All future invoices will automatically reflect these changes.\n\n"
                    "Resolution: Account billing details confirmed with customer admin."
                ),
            ),
            (
                "Inquiry Regarding Session Inactivity Timeout Settings",
                (
                    "Customer Question:\n"
                    "Our internal security compliance requires all staff dashboard sessions to automatically log out after 30 minutes of inactivity. "
                    "Where can our organization administrator enforce this setting?\n\n"
                    "Agent Response:\n"
                    "Hello. As an enterprise administrator, you can navigate to Organization Settings -> Security & Compliance -> Session Management. "
                    "Select 'Enforce idle timeout' and set the duration slider to 30 minutes.\n\n"
                    "Resolution: Admin confirmed setting applied across all organization user accounts."
                ),
            ),
            (
                "Assistance with Custom CSS Branding on Checkout Page",
                (
                    "Customer Question:\n"
                    "We want to embed our corporate logo and match our brand hex color (#1E3A8A) on the primary payment button. "
                    "Can we upload custom stylesheet assets to the checkout portal?\n\n"
                    "Agent Response:\n"
                    "Hello! Yes, under Checkout Settings -> Branding, you can upload your corporate SVG logo and specify primary, secondary, and accent colors. "
                    "Our hosted checkout renderer applies these variables while maintaining PCI-DSS Level 1 iframe isolation.\n\n"
                    "Resolution: Provided styling documentation link and verified preview mode."
                ),
            ),
            (
                "Clarification on High-Volume API Latency Guarantees",
                (
                    "Customer Question:\n"
                    "Our architecture team is evaluating our enterprise SLA. Could you provide your standard API latency SLO benchmarks "
                    "for customer query endpoints across US and EU regions?\n\n"
                    "Agent Response:\n"
                    "Hello. Our production SLA commits to 99.9% uptime with p95 API latency under 150ms for cached read requests and "
                    "under 300ms for database transactional writes. Regional telemetry is published live on status.novastack.example.\n\n"
                    "Resolution: Attached latest quarterly SOC 2 and SLA performance summary."
                ),
            ),
        ]

        for i in range(85):
            author = self.rng.choice(support_users)
            cust = customers[i % len(customers)] if customers else None
            svc = self.rng.choice(services)
            created_at = self._date_in_range(i, 85)

            scen_title, scen_body = ticket_scenarios[i % len(ticket_scenarios)]
            ticket_id = f"TKT-RTN-{i+1001:04d}"
            title = f"Support Ticket #{ticket_id}: {scen_title} — {cust.name if cust else 'Customer'}"

            content = (
                f"Support Ticket #{ticket_id}\n"
                f"Customer: {cust.name if cust else 'Enterprise Customer'} ({cust.customer_id if cust else 'N/A'})\n"
                f"Status: Resolved | Priority: Normal\n"
                f"Assigned Service: {svc.name} ({svc.service_id})\n"
                f"Timestamp: {created_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                f"{scen_body}"
            )

            related = [svc.service_id]
            if cust:
                related.append(cust.customer_id)

            self.records.append(
                SourceRecord(
                    document_id=self._next_doc_id(),
                    tenant_id="TENANT-NOVASTACK",
                    source_type="support_ticket",
                    title=title,
                    content=content,
                    author_id=author.user_id,
                    department="Customer Support",
                    created_at=created_at,
                    updated_at=created_at + timedelta(hours=self.rng.randint(2, 48)),
                    status="published",
                    classification="internal",
                    permissions=perms_support,
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=related,
                    authority_level="low",
                )
            )

    # ------------------------------------------------------------------
    # 4. Sales (45 records)
    # ------------------------------------------------------------------
    def _generate_sales(
        self, users: list[User], teams: list[Team], customers: list[Customer]
    ) -> None:
        sales_users = [u for u in users if u.department == "Sales"] or users
        perms_sales = RecordPermissions(
            allowed_roles=["account_executive", "sales_manager", "solutions_engineer", "director"],
            allowed_departments=["Sales", "Customer Support"],
        )

        sales_topics = [
            (
                "meeting",
                "medium",
                "Quarterly Business Review (QBR) & Platform Adoption Review",
                (
                    "Executive Account Review: QBR Minutes\n\n"
                    "Executive Summary:\n"
                    "- Customer has scaled monthly checkout transactions by 34% over prior quarter.\n"
                    "- Platform availability and API latency met all contracted Tier-1 SLA parameters.\n"
                    "- Customer expressed interest in testing the new multi-currency payment integration in early beta.\n\n"
                    "Commercial Discussion:\n"
                    "- Upcoming contract renewal scheduled for Q4.\n"
                    "- Proposal to expand from 500,000 monthly transactions tier to 1,500,000 tier with 15% volume discount.\n"
                    "- Action Item: Solutions engineer to schedule staging payment integration walk-through."
                ),
            ),
            (
                "documentation",
                "high",
                "Enterprise Account Strategy & Growth Plan",
                (
                    "# Strategic Account Plan\n\n"
                    "## Current Engagement\n"
                    "Customer utilizes NovaStack Core APIs and Checkout gateway for their primary consumer storefront.\n"
                    "Annual recurring revenue (ARR): $180,000.\n\n"
                    "## Expansion Opportunities\n"
                    "1. Upsell Notification Service: Customer currently uses third-party email provider with high delivery latency.\n"
                    "2. Database Replication Add-on: Multi-region active-passive disaster recovery failover tier.\n"
                    "3. Security & Compliance Package: Dedicated audit log streaming and custom data retention."
                ),
            ),
            (
                "meeting",
                "medium",
                "Technical Discovery Meeting: High-Throughput Integration Requirements",
                (
                    "Meeting Notes: Technical Discovery & Architecture Review\n\n"
                    "Participants: Enterprise Solutions Architect, Customer VP of Engineering\n\n"
                    "Technical Requirements:\n"
                    "- Expected Black Friday peak throughput: 3,200 checkout requests per second.\n"
                    "- Latency requirement: Strict 99th percentile under 200ms end-to-end.\n"
                    "- Database resilience: Dedicated Redis cluster nodes requested for customer session cache.\n\n"
                    "Action Items:\n"
                    "- NovaStack DevOps to provision load testing staging environment.\n"
                    "- Review rate limiter quotas and configure customer-specific threshold exceptions."
                ),
            ),
            (
                "conversation",
                "low",
                "Slack Thread: #sales-deal-desk — Custom Contract SLA & Payment Terms Approval",
                (
                    "Rachel Adams: Hey team, customer is requesting net-45 payment terms instead of standard net-30.\n"
                    "Mark Sterling: What is the total contract value (TCV)?\n"
                    "Rachel Adams: 2-year commitment at $240k ARR, paid annually upfront.\n"
                    "Mark Sterling: For a 2-year upfront commitment, net-45 is approved by Finance. Ensure payment terms clause 4.2 is updated in agreement."
                ),
            ),
        ]

        for i in range(45):
            author = self.rng.choice(sales_users)
            cust = customers[i % len(customers)] if customers else None
            created_at = self._date_in_range(i, 45)
            st_type, auth, title_base, content_base = sales_topics[i % len(sales_topics)]
            variant = (i // len(sales_topics)) + 1

            title = f"{title_base}: {cust.name if cust else 'Enterprise Client'} (Cycle {variant})"
            content = f"{content_base}\n\n---\nAccount: {cust.name if cust else 'Enterprise Client'} ({cust.customer_id if cust else 'N/A'})\nLead AE: {author.name}"
            related = [cust.customer_id] if cust else []

            self.records.append(
                SourceRecord(
                    document_id=self._next_doc_id(),
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=title,
                    content=content,
                    author_id=author.user_id,
                    department="Sales",
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=self.rng.randint(1, 10)),
                    status="published",
                    classification="confidential",
                    permissions=perms_sales,
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=related,
                    authority_level=auth,
                )
            )

    # ------------------------------------------------------------------
    # 5. Finance (40 records)
    # ------------------------------------------------------------------
    def _generate_finance(self, users: list[User], teams: list[Team]) -> None:
        perms_fin = RecordPermissions(
            allowed_roles=["financial_analyst", "controller", "cfo", "finance_manager"],
            allowed_departments=["Finance"],
        )

        fin_topics = [
            (
                "documentation",
                "high",
                "Annual Cloud Infrastructure & SaaS Expenditure Forecast",
                (
                    "# Financial Planning: Cloud Infrastructure & Database Hosting\n\n"
                    "## Overview\n"
                    "Annual analysis of AWS/GCP computing expenditures, managed database clusters, and CDN transit costs.\n\n"
                    "## Budget Breakdown\n"
                    "- Production Kubernetes Cluster Instances: $420,000 / year (Reserved instances cover 75% baseline load).\n"
                    "- Managed PostgreSQL Database & Replica Storage: $185,000 / year.\n"
                    "- CDN Proxy Transit & DDoS Protection: $95,000 / year.\n"
                    "- Observability, Metrics & Telemetry Storage: $60,000 / year.\n\n"
                    "## Cost Optimization Initiatives\n"
                    "Targeting 12% compute savings through automated rightsizing of development and staging cluster nodes."
                ),
            ),
            (
                "policy",
                "authoritative",
                "Corporate Travel, Entertainment & Expense Reimbursement Policy",
                (
                    "# NovaStack Policy: Corporate Travel & Business Expenses\n\n"
                    "## General Principles\n"
                    "Employees traveling on official NovaStack business must exercise prudent fiscal responsibility.\n\n"
                    "## Expense Guidelines\n"
                    "1. Airfare: Standard economy class for all flights under 6 hours duration. Business class permitted for international flights over 8 hours with VP approval.\n"
                    "2. Lodging: Maximum hotel rate benchmark is $250/night for tier-1 cities and $180/night elsewhere.\n"
                    "3. Meals & Incidentals: Daily per diem maximum of $75 per employee.\n"
                    "4. Expense Submission: All receipts must be submitted in the expense portal within 30 days of expense incurring."
                ),
            ),
            (
                "documentation",
                "high",
                "Quarterly Financial Performance & Budget Variance Analysis",
                (
                    "# Quarterly Budget Variance Report\n\n"
                    "## Summary\n"
                    "Total operating expenditures (OpEx) for the quarter finished 2.4% under planned budget.\n\n"
                    "## Departmental Variances\n"
                    "- Engineering: +1.1% variance due to additional staging cloud capacity for load testing.\n"
                    "- Marketing & Sales: -4.2% variance due to deferred conference sponsorships.\n"
                    "- Operations & Facilities: On budget (+0.2%).\n\n"
                    "## Cash Flow Position\n"
                    "Cash and liquid equivalents remain healthy, providing 28 months of runway under current operating model."
                ),
            ),
            (
                "meeting",
                "medium",
                "Finance Review: Payment Processor Interchange Fees & Rate Renegotiation",
                (
                    "Meeting Notes: Payment Gateway Processing Fee Review\n\n"
                    "Objective: Review interchange plus pricing agreements across credit card processing networks.\n\n"
                    "Findings:\n"
                    "- Blended payment processing fee currently stands at 2.15% + $0.30 per transaction.\n"
                    "- With platform annualized processing volume reaching $85M, NovaStack qualifies for enterprise tier discounts.\n\n"
                    "Action Items:\n"
                    "- Issue RFP to secondary payment gateway provider to create competitive leverage.\n"
                    "- Target fee reduction to 1.85% + $0.20, projected to save $255,000 annually."
                ),
            ),
        ]

        for i in range(40):
            author = self.rng.choice(users)
            created_at = self._date_in_range(i, 40)
            st_type, auth, title_base, content_base = fin_topics[i % len(fin_topics)]
            variant = (i // len(fin_topics)) + 1

            title = f"{title_base} (FY25/26 Cycle {variant})"
            content = f"{content_base}\n\n---\nClassification: Confidential — Finance Department\nAudit Ref: FIN-{i+100:04d}"

            self.records.append(
                SourceRecord(
                    document_id=self._next_doc_id(),
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=title,
                    content=content,
                    author_id=author.user_id,
                    department="Finance",
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=self.rng.randint(3, 30)),
                    status="published",
                    classification="confidential",
                    permissions=perms_fin,
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=[],
                    authority_level=auth,
                )
            )

    # ------------------------------------------------------------------
    # 6. HR (40 records)
    # ------------------------------------------------------------------
    def _generate_hr(self, users: list[User], teams: list[Team]) -> None:
        perms_hr_conf = RecordPermissions(
            allowed_roles=["hr_manager", "talent_lead", "vp_hr"],
            allowed_departments=["HR"],
        )
        perms_hr_all = RecordPermissions(
            allowed_roles=[],
            allowed_departments=[],
        )

        hr_topics = [
            (
                "policy",
                "authoritative",
                "internal",
                perms_hr_all,
                "Employee Handbook: Flexible Work & Remote Collaboration Guidelines",
                (
                    "# NovaStack Policy: Workplace Flexibility & Remote Collaboration\n\n"
                    "## Policy Statement\n"
                    "NovaStack supports a distributed-first hybrid workforce. Employees are empowered to work from approved home locations.\n\n"
                    "## Core Collaboration Hours\n"
                    "To facilitate cross-timezone coordination, teams observe core hours between 10:00 AM and 3:00 PM local time. "
                    "Async communication via Slack and ticket updates is prioritized over unscheduled meetings.\n\n"
                    "## Home Office Stipend\n"
                    "Full-time employees receive a one-time $1,000 ergonomic equipment reimbursement and a monthly $75 broadband allowance."
                ),
            ),
            (
                "policy",
                "authoritative",
                "internal",
                perms_hr_all,
                "Comprehensive Benefits Guide: Healthcare, Wellness & Retirement Plans",
                (
                    "# NovaStack Employee Benefits Summary\n\n"
                    "## Medical, Dental & Vision Coverage\n"
                    "NovaStack sponsors 100% of employee healthcare premiums and 80% for eligible dependents. Coverage begins on day one of employment.\n\n"
                    "## 401(k) Retirement Savings\n"
                    "NovaStack matches employee contributions dollar-for-dollar up to 4% of annual salary. Matching funds vest immediately.\n\n"
                    "## Paid Time Off & Parental Leave\n"
                    "- Unlimited Flexible Paid Time Off (PTO) with recommended minimum of 20 days per calendar year.\n"
                    "- 16 weeks of fully paid parental leave for birthing and non-birthing primary caregivers."
                ),
            ),
            (
                "documentation",
                "high",
                "internal",
                perms_hr_all,
                "Engineering Onboarding Roadmap: First 30-60-90 Days Curriculum",
                (
                    "# Engineering Onboarding Guide\n\n"
                    "Welcome to NovaStack Engineering! This guide outlines your initial milestones:\n\n"
                    "## Days 1–30: Environment Setup & First PR\n"
                    "- Configure developer laptop with 1Password, VPN, and Docker runtime.\n"
                    "- Clone primary repositories, configure pre-commit git hooks, and execute local test suite.\n"
                    "- Ship your first pull request to staging environment under mentor guidance.\n\n"
                    "## Days 31–60: Feature Ownership\n"
                    "- Participate in on-call shadow rotations and incident triage drills.\n"
                    "- Take lead on sprint user stories and code reviews.\n\n"
                    "## Days 61–90: Full Integration\n"
                    "- Participate in architecture syncs and contribute to runbook documentation."
                ),
            ),
            (
                "documentation",
                "high",
                "confidential",
                perms_hr_conf,
                "Annual Performance Calibration & Promotion Review Rubric",
                (
                    "# HR Protocol: Annual Talent Review & Calibration\n\n"
                    "## Overview\n"
                    "Standardized evaluation guidelines for engineering, product, and operational career ladders.\n\n"
                    "## Performance Rating Scale\n"
                    "1. Needs Improvement: Fails to meet expectations for current level.\n"
                    "2. Meets Expectations: Consistently delivers reliable, high-quality output.\n"
                    "3. Exceeds Expectations: Proactively solves complex problems and uplifts peer team members.\n"
                    "4. Exceptional: Demonstrates enterprise-wide impact, technical leadership, or innovation.\n\n"
                    "Calibration committee ensures uniform standards across departments to mitigate unconscious bias."
                ),
            ),
        ]

        for i in range(40):
            author = self.rng.choice(users)
            created_at = self._date_in_range(i, 40)
            st_type, auth, cls, perms, title_base, content_base = hr_topics[i % len(hr_topics)]
            variant = (i // len(hr_topics)) + 1

            title = f"{title_base} (Version {variant}.0)" if variant > 1 else title_base
            content = f"{content_base}\n\n---\nIssued by NovaStack People Operations\nDoc ID: HR-DOC-{i+200:04d}"

            self.records.append(
                SourceRecord(
                    document_id=self._next_doc_id(),
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=title,
                    content=content,
                    author_id=author.user_id,
                    department="HR",
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=self.rng.randint(2, 25)),
                    status="published",
                    classification=cls,
                    permissions=perms,
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=[],
                    authority_level=auth,
                )
            )

    # ------------------------------------------------------------------
    # 7. Security (45 records)
    # ------------------------------------------------------------------
    def _generate_security(
        self, users: list[User], teams: list[Team], services: list[Service]
    ) -> None:
        sec_users = [u for u in users if u.department == "Security"] or users
        perms_sec = RecordPermissions(
            allowed_roles=["security_engineer", "ciso", "security_architect", "compliance_lead"],
            allowed_departments=["Security"],
        )

        sec_topics = [
            (
                "policy",
                "authoritative",
                "restricted",
                "Enterprise Access Control & Production Database Privilege Auditing",
                (
                    "# Security Policy: Production Access Control & Privilege Management\n\n"
                    "## Policy Statement\n"
                    "Zero standing administrative or direct database credentials are permitted in production environments.\n\n"
                    "## Access Protocols\n"
                    "1. Production SSH and Kubernetes cluster access requires Just-In-Time (JIT) approval via Teleport.\n"
                    "2. JIT access sessions automatically expire after 2 hours and require peer engineering approval.\n"
                    "3. Direct database write access requires explicit ticket justification and session recording.\n"
                    "4. Access logs are archived to an immutable S3 bucket with 7-year retention."
                ),
            ),
            (
                "policy",
                "authoritative",
                "internal",
                "Workstation Hygiene, Disk Encryption & Endpoint Security Standards",
                (
                    "# Corporate Security Standard: Employee Endpoint Security\n\n"
                    "## Mandates\n"
                    "- Full-Disk Encryption: FileVault (macOS) or BitLocker (Windows) must remain enabled at all times.\n"
                    "- Password Hygiene: Minimum 16 characters enforced via enterprise password manager; multi-factor authentication (MFA) mandatory on all logins.\n"
                    "- Screen Lock: Automatic screen lock engaged after 5 minutes of inactivity.\n"
                    "- Patch Compliance: OS and browser security updates must be applied within 7 days of release."
                ),
            ),
            (
                "documentation",
                "high",
                "restricted",
                "Annual SOC 2 Type II Audit Evidence Collection & Control Checklist",
                (
                    "# SOC 2 Type II Compliance Playbook\n\n"
                    "## Trust Services Criteria\n"
                    "- CC6.1: Logical access controls, quarterly user access reviews, and terminated employee deprovisioning within 24 hours.\n"
                    "- CC7.1: Vulnerability management, automated dependency scanning in CI/CD, and external penetration tests.\n"
                    "- CC8.1: Change management, required pull request reviews, and automated CI test execution.\n\n"
                    "## Evidence Collection Schedule\n"
                    "Auditor walkthroughs scheduled semi-annually. All log archives and change records must be verified by team leads."
                ),
            ),
            (
                "documentation",
                "high",
                "internal",
                "Third-Party SaaS Vendor Risk Assessment & Data Security Review Protocol",
                (
                    "# Vendor Security Assessment Procedure\n\n"
                    "## Overview\n"
                    "Any third-party software service handling customer data or integrated into NovaStack networks must undergo security vetting.\n\n"
                    "## Evaluation Criteria\n"
                    "1. SOC 2 Type II or ISO 27001 certification report required.\n"
                    "2. Encryption in transit (TLS 1.3) and at rest (AES-256) validation.\n"
                    "3. Data residency verification to ensure adherence to customer GDPR/CCPA agreements.\n"
                    "4. Approval by CISO required before contract signature."
                ),
            ),
            (
                "meeting",
                "medium",
                "restricted",
                "Security Operations Sync: Quarterly Vulnerability & Patching Status",
                (
                    "Meeting Notes: Quarterly Vulnerability Management Review\n\n"
                    "Telemetry Review:\n"
                    "- Critical vulnerabilities: 0 outstanding (100% patched within 48h SLA).\n"
                    "- High severity: 2 dependencies identified in non-production analytics tools; patch PRs scheduled.\n"
                    "- Container Base Images: Re-anchored to Alpine 3.20 minimal distribution, reducing CVE exposure by 65%.\n\n"
                    "Action Items: Validate automated vulnerability alerts in GitHub Security center."
                ),
            ),
        ]

        for i in range(45):
            author = self.rng.choice(sec_users)
            svc = self.rng.choice(services)
            created_at = self._date_in_range(i, 45)
            st_type, auth, cls, title_base, content_base = sec_topics[i % len(sec_topics)]
            variant = (i // len(sec_topics)) + 1

            title = f"{title_base} (Revision {variant})" if variant > 1 else title_base
            content = f"{content_base}\n\n---\nMonitored Service: {svc.name} ({svc.service_id})\nSecurity Audit Ref: SEC-{i+300:04d}"

            self.records.append(
                SourceRecord(
                    document_id=self._next_doc_id(),
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=title,
                    content=content,
                    author_id=author.user_id,
                    department="Security",
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=self.rng.randint(2, 14)),
                    status="published",
                    classification=cls,
                    permissions=perms_sec,
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=[svc.service_id],
                    authority_level=auth,
                )
            )

    # ------------------------------------------------------------------
    # 8. Operations (35 records)
    # ------------------------------------------------------------------
    def _generate_operations(self, users: list[User], teams: list[Team]) -> None:
        perms_ops = RecordPermissions(
            allowed_roles=["operations_manager", "workplace_lead", "director"],
            allowed_departments=["Operations"],
        )

        ops_topics = [
            (
                "policy",
                "authoritative",
                "Physical Office Access, Visitor Management & Facility Safety",
                (
                    "# Workplace Operations Policy: Facilities & Access Badges\n\n"
                    "## Badge Protocol\n"
                    "All employees and contractors must display their RFID access badges visibly when on company premises.\n\n"
                    "## Visitor Policy\n"
                    "All external guests must sign in via Envoy at the reception desk, sign the digital visitor NDA, "
                    "and remain escorted by an authorized host employee at all times.\n\n"
                    "## Emergency Evacuation\n"
                    "Floor wardens lead annual fire evacuation drills. Assembly points are marked in the central courtyard."
                ),
            ),
            (
                "documentation",
                "high",
                "IT Hardware Procurement, Asset Tagging & Equipment Lifecycle",
                (
                    "# Operations SOP: IT Asset Provisioning Workflow\n\n"
                    "## Hardware Standards\n"
                    "Standard engineering configuration: 16-inch Apple MacBook Pro (M3 Max, 36GB RAM, 1TB SSD) or Dell XPS 15.\n\n"
                    "## Lifecycle Protocol\n"
                    "1. Hardware Procurement: Ordered 2 weeks prior to new hire start date via centralized vendor portal.\n"
                    "2. Asset Tagging: Serial number registered into MDM (Mobile Device Management) asset database.\n"
                    "3. Decommissioning: Hardware wiped according to NIST 800-88 sanitization guidelines upon employee departure."
                ),
            ),
            (
                "documentation",
                "high",
                "Corporate Travel Booking & Global Mobility Guidelines",
                (
                    "# Operations Guide: Corporate Travel Booking\n\n"
                    "## Booking Tool\n"
                    "All domestic and international business flights and hotels must be booked through Navan (corporate travel portal).\n\n"
                    "## Support & Duty of Care\n"
                    "In the event of travel disruption, severe weather, or health emergencies, 24/7 travel assistance is available "
                    "via the corporate emergency helpline."
                ),
            ),
            (
                "meeting",
                "medium",
                "Operations Review: Workplace Facilities & Equipment Refresh Sync",
                (
                    "Meeting Notes: Quarterly Workplace Facilities Sync\n\n"
                    "Key Updates:\n"
                    "- Ergonomic workstation audit completed for 120 desk setups.\n"
                    "- Completed replacement of conference room AV hardware with Zoom Rooms native soundbars.\n"
                    "- Scheduled HVAC preventative maintenance during upcoming holiday weekend."
                ),
            ),
        ]

        for i in range(35):
            author = self.rng.choice(users)
            created_at = self._date_in_range(i, 35)
            st_type, auth, title_base, content_base = ops_topics[i % len(ops_topics)]
            variant = (i // len(ops_topics)) + 1

            title = f"{title_base} (Ref {variant})" if variant > 1 else title_base
            content = f"{content_base}\n\n---\nOperations Reference: OPS-FAC-{i+400:04d}"

            self.records.append(
                SourceRecord(
                    document_id=self._next_doc_id(),
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=title,
                    content=content,
                    author_id=author.user_id,
                    department="Operations",
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=self.rng.randint(3, 20)),
                    status="published",
                    classification="internal",
                    permissions=perms_ops,
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=[],
                    authority_level=auth,
                )
            )

    # ------------------------------------------------------------------
    # 9. Legal (35 records)
    # ------------------------------------------------------------------
    def _generate_legal(
        self, users: list[User], teams: list[Team], customers: list[Customer]
    ) -> None:
        perms_legal = RecordPermissions(
            allowed_roles=["general_counsel", "legal_counsel", "contracts_manager"],
            allowed_departments=["Legal"],
        )

        legal_topics = [
            (
                "documentation",
                "high",
                "Master Services Agreement (MSA) Standard Terms & Fallback Negotiation Playbook",
                (
                    "# Legal Guidance: Enterprise MSA Negotiation Playbook\n\n"
                    "## Limitation of Liability (Clause 11)\n"
                    "- Standard Clause: Mutual limitation capped at 12 months of customer fees paid under applicable order form.\n"
                    "- Fallback 1: 2x 12-month fees for material breach of confidentiality or IP indemnification.\n"
                    "- Non-Negotiable: Uncapped liability will not be accepted without written General Counsel approval.\n\n"
                    "## Payment Terms (Clause 4)\n"
                    "- Standard: Net 30 days from invoice date. Fallback to Net 45 permitted for contracts exceeding $200k ARR.\n\n"
                    "## Service Level Commitments\n"
                    "- Standard SLA commitment: 99.9% availability with service credit remedies as exclusive financial remedy."
                ),
            ),
            (
                "policy",
                "authoritative",
                "Data Processing Addendum (DPA) & Cross-Border Data Transfer Protocol",
                (
                    "# Corporate Legal Standard: Data Protection & Privacy Compliance\n\n"
                    "## GDPR & CCPA Compliance Requirements\n"
                    "NovaStack acts as a Data Processor on behalf of enterprise customer Data Controllers.\n\n"
                    "## Standard Contractual Clauses (SCCs)\n"
                    "Cross-border customer data transfers from the EU/UK to US processing nodes rely on European Commission approved SCCs.\n"
                    "Customer sub-processors list must be published and updated at least 30 days prior to onboarding any new cloud vendor."
                ),
            ),
            (
                "documentation",
                "high",
                "Open Source Software (OSS) Licensing & Attribution Guidelines",
                (
                    "# Engineering Compliance: Open Source Software Governance\n\n"
                    "## Permissible Licenses\n"
                    "Apache 2.0, MIT, BSD 2-Clause, BSD 3-Clause, and ISC licenses are pre-approved for inclusion in proprietary NovaStack products.\n\n"
                    "## Restricted / Copyleft Licenses\n"
                    "GPL v2, GPL v3, and AGPL licenses are strictly prohibited in server-side binaries or microservice dependencies without Legal review.\n"
                    "Automated license scanners in CI/CD pipeline flag and block forbidden licenses."
                ),
            ),
            (
                "documentation",
                "high",
                "Standard Non-Disclosure Agreement (NDA) Protocol & Execution Workflow",
                (
                    "# Legal Workflow: Mutual Non-Disclosure Agreements\n\n"
                    "## Execution Procedure\n"
                    "Prior to sharing confidential technical architecture, roadmap documents, or commercial pricing with prospective partners:\n"
                    "1. Generate mutual NDA via DocuSign template portal.\n"
                    "2. Verify counterparty legal corporate entity name against Secretary of State records.\n"
                    "3. Term of confidentiality: 3 years from execution date (5 years for source code trade secrets)."
                ),
            ),
        ]

        for i in range(35):
            author = self.rng.choice(users)
            cust = customers[i % len(customers)] if customers else None
            created_at = self._date_in_range(i, 35)
            st_type, auth, title_base, content_base = legal_topics[i % len(legal_topics)]
            variant = (i // len(legal_topics)) + 1

            title = f"{title_base} (Doc {variant})" if variant > 1 else title_base
            content = f"{content_base}\n\n---\nLegal Reference: LGL-MSA-{i+500:04d}\nCounterparty Context: {cust.name if cust else 'Standard Template'}"
            related = [cust.customer_id] if cust else []

            self.records.append(
                SourceRecord(
                    document_id=self._next_doc_id(),
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=title,
                    content=content,
                    author_id=author.user_id,
                    department="Legal",
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=self.rng.randint(5, 30)),
                    status="published",
                    classification="confidential",
                    permissions=perms_legal,
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=related,
                    authority_level=auth,
                )
            )

    # ------------------------------------------------------------------
    # 10. DevOps / Infrastructure (50 records)
    # ------------------------------------------------------------------
    def _generate_devops(
        self, users: list[User], teams: list[Team], services: list[Service]
    ) -> None:
        devops_users = [u for u in users if u.department == "DevOps"] or users
        perms_devops = RecordPermissions(
            allowed_roles=["devops_engineer", "sre", "infrastructure_lead", "system_architect"],
            allowed_departments=["DevOps", "Engineering"],
        )

        devops_topics = [
            (
                "documentation",
                "high",
                "Standard Operating Procedure (SOP): Kubernetes Node Pool Rolling Upgrade",
                (
                    "# Infrastructure SOP: EKS Node Pool Kernel Upgrades\n\n"
                    "## Purpose\n"
                    "Zero-downtime rolling replacement of production Kubernetes worker nodes for AMI and kernel security updates.\n\n"
                    "## Procedure\n"
                    "1. Provision new worker node group with updated AMI (`k8s-node-pool-v1.30-2025`).\n"
                    "2. Cordon old node pool to prevent new pod scheduling: `kubectl cordon -l pool=legacy`.\n"
                    "3. Drain nodes sequentially with 60-second grace periods, observing PodDisruptionBudgets.\n"
                    "4. Monitor service mesh traffic latency: ensure p99 latency remains stable under 120ms throughout migration.\n"
                    "5. Terminate decommissioned node pool once all workloads have migrated."
                ),
            ),
            (
                "documentation",
                "high",
                "Runbook: Multi-Region DNS Failover & Anycast Routing Health Checks",
                (
                    "# Operational Runbook: Global DNS & CDN Traffic Steering\n\n"
                    "## Overview\n"
                    "NovaStack edge proxy uses Route53 Latency-Based Routing (LBR) and Cloudflare Anycast to route customer traffic.\n\n"
                    "## Health Check Parameters\n"
                    "- Endpoint probe interval: 10 seconds.\n"
                    "- Consecutive failures to mark unhealthy: 3 consecutive 5xx or connection timeout > 2,500ms.\n"
                    "- Failover protocol: Automatic shift of DNS weight to alternate region within 30 seconds."
                ),
            ),
            (
                "documentation",
                "high",
                "Runbook: Automated PostgreSQL Backup Verification & Monthly Restore Drill",
                (
                    "# Database Runbook: Disaster Recovery & Backup Integrity Verification\n\n"
                    "## Verification Routine\n"
                    "Daily WAL (Write-Ahead Log) archiving and snapshot images are automatically validated in an isolated staging sandbox.\n\n"
                    "## Monthly Restore Drill Checklist\n"
                    "1. Restore primary database snapshot from S3 archive into temporary RDS instance.\n"
                    "2. Replay WAL logs up to target timestamp (Point-in-Time Recovery - PITR).\n"
                    "3. Execute automated schema and row count parity validation script.\n"
                    "4. Record Recovery Time Objective (RTO) and Recovery Point Objective (RPO) in compliance ledger."
                ),
            ),
            (
                "deployment_note",
                "high",
                "Scheduled Maintenance Release: Container Host Security Patches & Proxy Reconfiguration",
                (
                    "Deployment Log: Infrastructure Maintenance Window\n\n"
                    "Window: Sunday 02:00–04:00 UTC\n"
                    "Engineers On Call: Infrastructure & SRE\n\n"
                    "Tasks Completed:\n"
                    "- Applied OpenSSL 3.0.13 security update across all edge proxy instances.\n"
                    "- Reloaded NGINX ingress controller with updated HTTP/2 multiplexing limits.\n"
                    "- Flushed stale DNS cache on internal cluster resolver.\n"
                    "Verification: Post-deployment smoke tests passed 100%; zero customer error spikes observed."
                ),
            ),
            (
                "pull_request_note",
                "medium",
                "PR Review: Bump Base Container Images & Harden Non-Root Security Context",
                (
                    "Pull Request: #infra-428 — Harden Kubernetes Pod Security Contexts\n\n"
                    "Changes Proposed:\n"
                    "- Enforce `runAsNonRoot: true` across all production Deployment manifests.\n"
                    "- Set `readOnlyRootFilesystem: true` with temporary in-memory volume for `/tmp`.\n"
                    "- Update base Dockerfile to `cgr.dev/chainguard/static:latest`.\n\n"
                    "Review Notes:\n"
                    "- CI automated security scan completed with 0 high/critical CVEs.\n"
                    "- Staging deployment verified with functional test suites."
                ),
            ),
            (
                "engineering_note",
                "medium",
                "Observability Review: Prometheus Metric Cardinality & Telemetry Pruning",
                (
                    "DevOps Diagnostic Note: Prometheus TSDB Cardinality Optimization\n\n"
                    "Telemetry analysis revealed rapid memory consumption on internal Prometheus servers.\n"
                    "Root Cause: Service mesh metrics were recording high-cardinality `user_agent` labels on public checkout requests.\n"
                    "Remediation Applied:\n"
                    "- Configured metric relabeling rule to drop `user_agent` label prior to storage.\n"
                    "- Cardinality dropped from 2.4 million series to 180,000 series.\n"
                    "- Reduced memory footprint by 42GB with no impact on latency or error rate dashboards."
                ),
            ),
        ]

        for i in range(50):
            author = self.rng.choice(devops_users)
            svc = self.rng.choice(services)
            created_at = self._date_in_range(i, 50)
            st_type, auth, title_base, content_base = devops_topics[i % len(devops_topics)]
            variant = (i // len(devops_topics)) + 1

            title = f"{title_base} (Pass {variant})" if variant > 1 else title_base
            content = f"{content_base}\n\n---\nService Scope: {svc.name} ({svc.service_id})\nInfra Track: DEV-OPS-{i+600:04d}"

            self.records.append(
                SourceRecord(
                    document_id=self._next_doc_id(),
                    tenant_id="TENANT-NOVASTACK",
                    source_type=st_type,
                    title=title,
                    content=content,
                    author_id=author.user_id,
                    department="DevOps",
                    created_at=created_at,
                    updated_at=created_at + timedelta(days=self.rng.randint(1, 15)),
                    status="published",
                    classification="internal",
                    permissions=perms_devops,
                    source_entity_id=None,
                    source_entity_type=None,
                    related_entity_ids=[svc.service_id],
                    authority_level=auth,
                )
            )
