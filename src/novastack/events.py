"""Ground-truth event blueprints for the NovaStack enterprise.

Each blueprint defines the structured truth for one major enterprise
event.  The generator resolves service/team names to actual IDs at
generation time, so blueprints are portable across seed changes as
long as the referenced entities exist.

Service-name ↔ event mapping (seed 20260909):
    checkout-service        → Checkout timeout outage
    feature-flags           → Authentication degradation
    config-service          → Payment gateway failure
    cdn-proxy               → Search latency spike
    notification-service    → Notification delivery failure
    data-warehouse          → Database migration incident
    rate-limiter            → API rate-limit misconfiguration
    analytics-pipeline      → Customer billing discrepancy
    media-service           → Deployment rollback, Inventory sync
    email-service           → Security incident
    log-aggregator          → Regional infrastructure outage
"""

from __future__ import annotations

from typing import Any

# Type alias for blueprint dicts — keeps signatures concise.
Blueprint = dict[str, Any]


EVENT_BLUEPRINTS: list[Blueprint] = [
    # ---------------------------------------------------------------
    # 1. Checkout timeout outage
    # ---------------------------------------------------------------
    {
        "title": "Checkout timeout outage",
        "event_type": "outage",
        "severity": "critical",
        "root_cause": (
            "Connection pool exhaustion in checkout-service caused by "
            "misconfigured maximum connections parameter (set to 10 "
            "instead of 100) deployed in v2.4.1"
        ),
        "final_resolution": (
            "Connection pool max_connections increased from 10 to 100 "
            "and connection timeout reduced to 30s; pool saturation "
            "monitoring alert added at 80% threshold"
        ),
        "primary_service": "checkout-service",
        "secondary_services": [],
        "responsible_team": "Platform Engineering",
        "triggering_deployment": {
            "description": (
                "Update checkout-service connection pool configuration"
            ),
            "version": "2.4.1",
            "status": "succeeded",
        },
        "rollback_deployment": None,
        "fixing_pr": {
            "title": "fix: correct checkout-service connection pool settings",
            "description": (
                "Increase max_connections from 10 to 100 and reduce idle "
                "timeout to prevent pool exhaustion under load"
            ),
        },
        "incident_title": (
            "Checkout requests timing out across all regions"
        ),
        "customer_impact_pct": 0.30,
        "timeline_month": 2,      # March 2025
        "duration_hours": 4.0,
    },

    # ---------------------------------------------------------------
    # 2. Authentication degradation
    # ---------------------------------------------------------------
    {
        "title": "Authentication degradation",
        "event_type": "degradation",
        "severity": "high",
        "root_cause": (
            "Feature flag rollout for gradual-login-redesign triggered "
            "unexpected Redis cache eviction in authentication token "
            "validation path due to shared cache namespace"
        ),
        "final_resolution": (
            "Feature flag reverted; Redis eviction policy changed from "
            "allkeys-lru to volatile-lru for auth token namespace; "
            "cache namespaces isolated between feature flags and auth"
        ),
        "primary_service": "feature-flags",
        "secondary_services": [],
        "responsible_team": "Identity & Access",
        "triggering_deployment": {
            "description": (
                "Roll out gradual-login-redesign feature flag to 50% of users"
            ),
            "version": "1.8.0",
            "status": "succeeded",
        },
        "rollback_deployment": None,
        "fixing_pr": {
            "title": (
                "fix: isolate auth token cache from feature flag "
                "cache namespace"
            ),
            "description": (
                "Separate Redis namespace for auth tokens with volatile-lru "
                "eviction policy to prevent feature flag operations from "
                "evicting authentication data"
            ),
        },
        "incident_title": (
            "Intermittent authentication failures and elevated login latency"
        ),
        "customer_impact_pct": 0.50,
        "timeline_month": 3,      # April 2025
        "duration_hours": 6.0,
    },

    # ---------------------------------------------------------------
    # 3. Payment gateway failure
    # ---------------------------------------------------------------
    {
        "title": "Payment gateway failure",
        "event_type": "failure",
        "severity": "critical",
        "root_cause": (
            "Payment gateway endpoint URL updated to incorrect staging "
            "endpoint in config-service deployment during PCI compliance "
            "credential rotation"
        ),
        "final_resolution": (
            "Config-service rolled back to previous version; payment "
            "gateway URL validation added to deployment pipeline; "
            "configuration change approval workflow implemented for "
            "payment-critical settings"
        ),
        "primary_service": "config-service",
        "secondary_services": [],
        "responsible_team": "Payments",
        "triggering_deployment": {
            "description": (
                "Update payment gateway configuration endpoints for "
                "PCI compliance credential rotation"
            ),
            "version": "3.1.2",
            "status": "rolled_back",
        },
        "rollback_deployment": None,
        "fixing_pr": {
            "title": (
                "fix: add payment gateway URL validation and rollback "
                "procedure"
            ),
            "description": (
                "Add URL format and health-check validation for payment "
                "gateway config; implement automated pre-activation check; "
                "add one-click rollback for critical config changes"
            ),
        },
        "incident_title": (
            "All payment transactions failing with gateway timeout errors"
        ),
        "customer_impact_pct": 0.45,
        "timeline_month": 5,      # June 2025
        "duration_hours": 2.0,
    },

    # ---------------------------------------------------------------
    # 4. Search latency spike
    # ---------------------------------------------------------------
    {
        "title": "Search latency spike",
        "event_type": "degradation",
        "severity": "medium",
        "root_cause": (
            "CDN cache invalidation storm caused by bulk product catalog "
            "update triggered massive origin server load; search queries "
            "routed through CDN experienced 5-10s response times"
        ),
        "final_resolution": (
            "Implemented staggered cache invalidation with configurable "
            "rate limiting; added background cache warming for "
            "top-1000 search queries after bulk invalidation events"
        ),
        "primary_service": "cdn-proxy",
        "secondary_services": [],
        "responsible_team": "Platform Engineering",
        "triggering_deployment": None,
        "rollback_deployment": None,
        "fixing_pr": {
            "title": (
                "feat: implement staggered CDN cache invalidation "
                "with rate limiting"
            ),
            "description": (
                "Add configurable rate limiter for cache invalidation "
                "requests; implement background cache warming for "
                "top-1000 queries after bulk invalidation"
            ),
        },
        "incident_title": (
            "Search response times degraded to 5-10s across "
            "product catalog queries"
        ),
        "customer_impact_pct": 0.20,
        "timeline_month": 7,      # August 2025
        "duration_hours": 3.0,
    },

    # ---------------------------------------------------------------
    # 5. Notification delivery failure
    # ---------------------------------------------------------------
    {
        "title": "Notification delivery failure",
        "event_type": "failure",
        "severity": "high",
        "root_cause": (
            "Message queue partition rebalancing during notification-service "
            "scaling event caused consumer group desynchronization and "
            "message loss across email and push notification channels"
        ),
        "final_resolution": (
            "Notification-service consumer group updated with static "
            "partition assignment; dead-letter queue processing "
            "implemented for undelivered messages; missed notifications "
            "re-sent from dead-letter queue"
        ),
        "primary_service": "notification-service",
        "secondary_services": [],
        "responsible_team": "Infrastructure",
        "triggering_deployment": {
            "description": (
                "Scale notification-service from 3 to 6 replicas "
                "for holiday traffic preparation"
            ),
            "version": "4.2.0",
            "status": "succeeded",
        },
        "rollback_deployment": None,
        "fixing_pr": {
            "title": (
                "fix: implement static partition assignment for "
                "notification consumers"
            ),
            "description": (
                "Replace dynamic partition assignment with static "
                "assignment in notification-service consumer group; "
                "add dead-letter queue with automatic retry for "
                "failed deliveries"
            ),
        },
        "incident_title": (
            "Customer notification emails and push notifications "
            "not being delivered"
        ),
        "customer_impact_pct": 0.35,
        "timeline_month": 9,      # October 2025
        "duration_hours": 8.0,
    },

    # ---------------------------------------------------------------
    # 6. Database migration incident
    # ---------------------------------------------------------------
    {
        "title": "Database migration incident",
        "event_type": "incident",
        "severity": "high",
        "root_cause": (
            "Schema migration on data-warehouse added NOT NULL column "
            "without default value, locking writes to affected tables "
            "for 47 minutes during backfill of 12M rows"
        ),
        "final_resolution": (
            "Migration rolled back manually by DBA; subsequent migration "
            "redesigned to use ADD COLUMN with default value followed by "
            "background backfill; migration runbook updated"
        ),
        "primary_service": "data-warehouse",
        "secondary_services": [],
        "responsible_team": "Security Engineering",
        "triggering_deployment": {
            "description": (
                "Apply schema migration v45 to add compliance audit "
                "columns to data-warehouse"
            ),
            "version": "1.45.0",
            "status": "failed",
        },
        "rollback_deployment": None,
        "fixing_pr": None,   # Manual rollback — no code fix PR
        "incident_title": (
            "Data warehouse write operations blocked during "
            "schema migration"
        ),
        "customer_impact_pct": 0.0,
        "timeline_month": 11,     # December 2025
        "duration_hours": 2.5,
    },

    # ---------------------------------------------------------------
    # 7. API rate-limit misconfiguration
    # ---------------------------------------------------------------
    {
        "title": "API rate-limit misconfiguration",
        "event_type": "degradation",
        "severity": "medium",
        "root_cause": (
            "Rate limiter configuration update incorrectly set "
            "per-customer limits to per-global scope, causing "
            "legitimate high-volume enterprise API consumers to "
            "be throttled at aggregate traffic levels"
        ),
        "final_resolution": (
            "Rate limiter configuration corrected to per-customer "
            "scope; integration test added to validate rate limit "
            "scope configuration on every deployment"
        ),
        "primary_service": "rate-limiter",
        "secondary_services": [],
        "responsible_team": "Infrastructure",
        "triggering_deployment": {
            "description": (
                "Update rate-limiter thresholds for new API tier pricing"
            ),
            "version": "2.1.0",
            "status": "succeeded",
        },
        "rollback_deployment": None,
        "fixing_pr": {
            "title": (
                "fix: restore per-customer rate limit scope and "
                "add config validation"
            ),
            "description": (
                "Fix rate limiter scope from global to per-customer; "
                "add integration test asserting rate limit scoping; "
                "add config schema validation for limit scope field"
            ),
        },
        "incident_title": (
            "Enterprise API consumers reporting HTTP 429 rate limit "
            "errors on normal traffic volumes"
        ),
        "customer_impact_pct": 0.15,
        "timeline_month": 13,     # February 2026
        "duration_hours": 5.0,
    },

    # ---------------------------------------------------------------
    # 8. Customer billing discrepancy
    # ---------------------------------------------------------------
    {
        "title": "Customer billing discrepancy",
        "event_type": "issue",
        "severity": "high",
        "root_cause": (
            "Analytics pipeline double-counted checkout events due to "
            "idempotency key collision in event deduplication logic, "
            "inflating billing amounts for affected customers"
        ),
        "final_resolution": (
            "Billing recalculated for all affected customers and "
            "credits issued; idempotency key generation updated to "
            "include timestamp component; daily reconciliation job "
            "added to detect future discrepancies exceeding 0.1%"
        ),
        "primary_service": "analytics-pipeline",
        "secondary_services": ["checkout-service"],
        "responsible_team": "Checkout",
        "triggering_deployment": None,
        "rollback_deployment": None,
        "fixing_pr": {
            "title": (
                "fix: prevent analytics pipeline event double-counting "
                "via improved idempotency keys"
            ),
            "description": (
                "Include event timestamp in idempotency key hash; "
                "add daily reconciliation job comparing analytics "
                "totals with checkout transaction log; add alerting "
                "for discrepancy > 0.1%"
            ),
        },
        "incident_title": (
            "Multiple customers reporting inflated invoice amounts "
            "for current billing period"
        ),
        "customer_impact_pct": 0.10,
        "timeline_month": 15,     # April 2026
        "duration_hours": 24.0,
    },

    # ---------------------------------------------------------------
    # 9. Deployment rollback — media-service
    # ---------------------------------------------------------------
    {
        "title": "Deployment rollback — media-service",
        "event_type": "rollback",
        "severity": "medium",
        "root_cause": (
            "Media-service v3.0.0 deployment introduced breaking API "
            "change in image processing endpoint that caused failures "
            "across dependent services consuming the v2 API contract"
        ),
        "final_resolution": (
            "Media-service rolled back to v2.9.5; v3.0.1 released "
            "with backward-compatible API preserving v2 endpoints; "
            "API versioning strategy documented and enforced via CI"
        ),
        "primary_service": "media-service",
        "secondary_services": [],
        "responsible_team": "Developer Experience",
        "triggering_deployment": {
            "description": (
                "Deploy media-service v3.0.0 with new image "
                "processing API"
            ),
            "version": "3.0.0",
            "status": "rolled_back",
        },
        "rollback_deployment": {
            "description": (
                "Emergency rollback media-service to v2.9.5"
            ),
            "version": "2.9.5",
            "status": "succeeded",
        },
        "fixing_pr": {
            "title": (
                "fix: make media-service v3 API backward compatible"
            ),
            "description": (
                "Maintain v2 API endpoints alongside v3; add API "
                "version negotiation header; add backward "
                "compatibility integration tests"
            ),
        },
        "incident_title": (
            "Media-service image processing failing after "
            "v3.0.0 deployment"
        ),
        "customer_impact_pct": 0.08,
        "timeline_month": 17,     # June 2026
        "duration_hours": 1.5,
    },

    # ---------------------------------------------------------------
    # 10. Security incident — SMTP relay misconfiguration
    # ---------------------------------------------------------------
    {
        "title": "Security incident — SMTP relay misconfiguration",
        "event_type": "security_incident",
        "severity": "critical",
        "root_cause": (
            "Email-service SMTP relay configuration allowed "
            "unauthenticated relay from an internal network range "
            "broader than intended (10.0.0.0/8 instead of "
            "10.0.42.0/24), potentially allowing unauthorized "
            "email sending from any internal host"
        ),
        "final_resolution": (
            "SMTP relay restricted to application service accounts "
            "only; network ACL tightened to 10.0.42.0/24; security "
            "audit of all email-service relay rules completed; "
            "no evidence of exploitation found during forensic review"
        ),
        "primary_service": "email-service",
        "secondary_services": [],
        "responsible_team": "Security Engineering",
        "triggering_deployment": None,
        "rollback_deployment": None,
        "fixing_pr": {
            "title": (
                "fix: restrict email-service SMTP relay to "
                "authenticated service accounts"
            ),
            "description": (
                "Replace network-range-based SMTP relay ACL with "
                "service-account authentication; add relay attempt "
                "logging and alerting for unauthorized senders; "
                "add weekly automated relay config audit"
            ),
        },
        "incident_title": (
            "Security review identified overly permissive SMTP relay "
            "configuration in email-service"
        ),
        "customer_impact_pct": 0.0,
        "timeline_month": 18,     # July 2026
        "duration_hours": 48.0,
    },

    # ---------------------------------------------------------------
    # 11. Inventory synchronization failure
    # ---------------------------------------------------------------
    {
        "title": "Inventory synchronization failure",
        "event_type": "issue",
        "severity": "medium",
        "root_cause": (
            "Race condition in media-service asset synchronization "
            "caused duplicate entries when concurrent uploads targeted "
            "the same asset namespace without distributed locking"
        ),
        "final_resolution": (
            "Redis-based distributed lock implemented for asset "
            "namespace writes; duplicate detection and cleanup job "
            "deployed; affected customer assets deduplicated with "
            "zero data loss"
        ),
        "primary_service": "media-service",
        "secondary_services": ["analytics-pipeline"],
        "responsible_team": "Developer Experience",
        "triggering_deployment": {
            "description": (
                "Deploy media-service concurrent upload optimization"
            ),
            "version": "3.1.0",
            "status": "succeeded",
        },
        "rollback_deployment": None,
        "fixing_pr": {
            "title": (
                "fix: add distributed locking for media-service "
                "asset namespace writes"
            ),
            "description": (
                "Implement Redis-based distributed lock for asset "
                "namespace writes; add duplicate asset detection cron "
                "job; add asset count reconciliation alerting"
            ),
        },
        "incident_title": (
            "Customers reporting duplicate media assets appearing "
            "in their asset libraries"
        ),
        "customer_impact_pct": 0.12,
        "timeline_month": 20,     # September 2026
        "duration_hours": 12.0,
    },

    # ---------------------------------------------------------------
    # 12. Regional infrastructure outage
    # ---------------------------------------------------------------
    {
        "title": "Regional infrastructure outage",
        "event_type": "outage",
        "severity": "critical",
        "root_cause": (
            "Cloud provider us-east-1 region experienced network "
            "partition affecting availability zones hosting "
            "log-aggregator, cdn-proxy, and notification-service "
            "primary instances"
        ),
        "final_resolution": (
            "Services failed over to us-west-2 region; multi-region "
            "active-active architecture accelerated for all critical "
            "services; automated regional failover runbook created "
            "and tested"
        ),
        "primary_service": "log-aggregator",
        "secondary_services": ["cdn-proxy", "notification-service"],
        "responsible_team": "SRE",
        "triggering_deployment": None,
        "rollback_deployment": None,
        "fixing_pr": {
            "title": (
                "feat: implement automated multi-region failover "
                "for critical infrastructure services"
            ),
            "description": (
                "Add automated health-check-based failover for "
                "log-aggregator, cdn-proxy, and notification-service; "
                "implement cross-region data replication with 30s RPO; "
                "add regional failover runbook to incident response "
                "playbook"
            ),
        },
        "incident_title": (
            "Multiple infrastructure services unavailable due to "
            "regional cloud provider outage"
        ),
        "customer_impact_pct": 0.60,
        "timeline_month": 22,     # November 2026
        "duration_hours": 6.0,
    },
]
