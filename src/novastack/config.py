"""Configuration for the NovaStack synthetic data generator.

All generation parameters are centralised here so that tenant distribution,
seed, and domain data can be changed without touching generator logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
RANDOM_SEED: int = 20260909
DATASET_VERSION: str = "0.1.0"

# ---------------------------------------------------------------------------
# Tenant configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TenantConfig:
    """Per-tenant entity-count targets and identity metadata."""

    tenant_id: str
    name: str
    short_code: str
    email_domain: str
    user_count: int
    team_count: int
    customer_count: int
    service_count: int


DEFAULT_TENANTS: list[TenantConfig] = [
    TenantConfig(
        tenant_id="TENANT-NOVASTACK",
        name="NovaStack",
        short_code="NS",
        email_domain="novastack.example",
        user_count=78,
        team_count=10,
        customer_count=55,
        service_count=12,
    ),
    TenantConfig(
        tenant_id="TENANT-ORBITAL",
        name="Orbital",
        short_code="OR",
        email_domain="orbital.example",
        user_count=14,
        team_count=3,
        customer_count=12,
        service_count=2,
    ),
    TenantConfig(
        tenant_id="TENANT-PINECONE",
        name="Pinecone",
        short_code="PC",
        email_domain="pinecone.example",
        user_count=8,
        team_count=2,
        customer_count=8,
        service_count=1,
    ),
]

# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------
DEPARTMENTS: list[str] = [
    "Engineering",
    "Product",
    "Customer Support",
    "Sales",
    "Finance",
    "HR",
    "Security",
    "DevOps",
    "Legal",
    "Operations",
]

# ---------------------------------------------------------------------------
# Team templates — (name, department, can_own_services)
#
# The generator selects the first *team_count* entries from the pool
# that corresponds to a given tenant.  Pools are ordered so that
# service-owning and sales teams appear early enough to satisfy the
# constraint that every tenant has ≥1 of each.
# ---------------------------------------------------------------------------

TeamTemplate = tuple[str, str, bool]

NOVASTACK_TEAM_POOL: list[TeamTemplate] = [
    # Service-owning technical teams
    ("Platform Engineering", "Engineering", True),
    ("Payments", "Engineering", True),
    ("Checkout", "Engineering", True),
    ("Identity & Access", "Engineering", True),
    ("Developer Experience", "Engineering", True),
    ("SRE", "DevOps", True),
    ("Infrastructure", "DevOps", True),
    ("Security Engineering", "Security", True),
    # Non-service-owning teams (Sales first to ensure account-owner pool)
    ("Enterprise Sales", "Sales", False),
    ("Product Management", "Product", False),
    ("Customer Support", "Customer Support", False),
    ("Revenue Operations", "Sales", False),
    ("Finance & Accounting", "Finance", False),
    ("People Operations", "HR", False),
    ("Legal & Compliance", "Legal", False),
    ("Business Operations", "Operations", False),
]

GENERIC_TEAM_POOL: list[TeamTemplate] = [
    ("Engineering", "Engineering", True),
    ("Sales", "Sales", False),
    ("Operations", "Operations", False),
    ("Product", "Product", False),
    ("Support", "Customer Support", False),
]

# Mapping from tenant_id → team pool.  Tenants not listed fall back to
# the generic pool.
TENANT_TEAM_POOLS: dict[str, list[TeamTemplate]] = {
    "TENANT-NOVASTACK": NOVASTACK_TEAM_POOL,
}

# ---------------------------------------------------------------------------
# Service-owning team name keywords
#
# A team whose name contains any of these tokens (case-insensitive) is
# eligible to own services.  This is used only as a validation aid —
# the authoritative flag is the third element of each TeamTemplate.
# ---------------------------------------------------------------------------
SERVICE_OWNING_KEYWORDS: set[str] = {
    "engineering",
    "payments",
    "checkout",
    "sre",
    "devops",
    "platform",
    "infrastructure",
    "security",
    "identity",
    "developer experience",
}

# ---------------------------------------------------------------------------
# Roles by department
# ---------------------------------------------------------------------------
ROLES_BY_DEPARTMENT: dict[str, list[str]] = {
    "Engineering": [
        "engineer", "senior_engineer", "staff_engineer",
        "tech_lead", "engineering_manager",
    ],
    "DevOps": [
        "sre", "devops_engineer", "senior_devops_engineer",
        "infrastructure_engineer",
    ],
    "Security": [
        "security_engineer", "senior_security_engineer",
        "security_analyst",
    ],
    "Product": [
        "product_manager", "senior_product_manager",
        "product_analyst",
    ],
    "Sales": [
        "account_executive", "sales_engineer",
        "sales_manager", "business_development_rep",
    ],
    "Customer Support": [
        "support_engineer", "senior_support_engineer",
        "support_manager",
    ],
    "Finance": [
        "financial_analyst", "accountant", "finance_manager",
    ],
    "HR": [
        "hr_specialist", "recruiter", "hr_manager",
    ],
    "Legal": [
        "legal_counsel", "compliance_analyst", "paralegal",
    ],
    "Operations": [
        "operations_analyst", "operations_manager",
        "project_manager",
    ],
}

# Fallback when department not found in the mapping above.
DEFAULT_ROLES: list[str] = ["analyst", "specialist", "coordinator", "manager"]

# ---------------------------------------------------------------------------
# Synthetic name pools
# ---------------------------------------------------------------------------
FIRST_NAMES: list[str] = [
    "Aria", "Ben", "Carmen", "David", "Elena", "Feng", "Grace",
    "Hassan", "Iris", "James", "Kenji", "Luna", "Marco", "Nina",
    "Oscar", "Priya", "Quinn", "Raj", "Sofia", "Thomas",
    "Uma", "Victor", "Wei", "Xena", "Yuki", "Zara",
    "Aiden", "Bella", "Carlos", "Diana", "Emil", "Fatima",
    "George", "Hana", "Ivan", "Julia", "Kai", "Leah",
    "Miguel", "Nadia", "Owen", "Petra", "Rafael", "Sana",
    "Tomas", "Valentina", "William", "Ximena", "Yusuf", "Zoe",
    "Amit", "Bianca", "Chen", "Dara", "Ethan", "Fiona",
    "Gael", "Hope", "Ines", "Jakob", "Kira", "Leo",
    "Maya", "Noah", "Olivia", "Pablo", "Rena", "Sam",
    "Tara", "Uri", "Vera", "Wyatt", "Yara", "Zain",
]

LAST_NAMES: list[str] = [
    "Abadi", "Baker", "Castellano", "Dubois", "Ellis", "Fernandez",
    "Garcia", "Huang", "Ibrahim", "Jensen", "Kim", "Larsson",
    "Morales", "Nakamura", "O'Brien", "Park", "Quintero", "Reyes",
    "Singh", "Torres", "Ueda", "Volkov", "Wang", "Xu",
    "Yamamoto", "Zhang", "Anderson", "Becker", "Costa", "Dalton",
    "Evans", "Fischer", "Gonzalez", "Harper", "Ito", "Johnson",
    "Kumar", "Lopez", "Mitchell", "Novak", "Ortiz", "Patel",
    "Ramos", "Sullivan", "Tanaka", "Varma", "Williams", "Yang",
    "Zhao", "Adams",
]

# ---------------------------------------------------------------------------
# Service templates — (name, description, criticality)
# ---------------------------------------------------------------------------
ServiceTemplate = tuple[str, str, str]

SERVICE_TEMPLATES: list[ServiceTemplate] = [
    ("atlas-api", "Core API gateway and routing service", "critical"),
    ("payment-service", "Payment processing and billing", "critical"),
    ("checkout-service", "Shopping cart and checkout flow management", "critical"),
    ("identity-service", "Authentication, authorization, and SSO", "critical"),
    ("search-service", "Full-text search and indexing engine", "high"),
    ("notification-service", "Multi-channel notification delivery", "high"),
    ("analytics-pipeline", "Event ingestion and analytics processing", "high"),
    ("config-service", "Distributed configuration management", "medium"),
    ("audit-service", "Audit logging and compliance tracking", "high"),
    ("cdn-proxy", "Content delivery and edge caching proxy", "medium"),
    ("monitoring-stack", "Infrastructure and application monitoring", "high"),
    ("deploy-pipeline", "CI/CD deployment automation", "high"),
    ("data-warehouse", "Central data warehouse and ETL pipelines", "medium"),
    ("feature-flags", "Feature flag and experiment management", "medium"),
    ("rate-limiter", "API rate limiting and throttling service", "high"),
    ("email-service", "Transactional and marketing email delivery", "medium"),
    ("media-service", "Image and video processing pipeline", "medium"),
    ("scheduler-service", "Job scheduling and task queue management", "medium"),
    ("cache-service", "Distributed caching layer", "high"),
    ("log-aggregator", "Centralized log collection and analysis", "medium"),
]

# ---------------------------------------------------------------------------
# Customer pools
# ---------------------------------------------------------------------------
CUSTOMER_NAMES: list[str] = [
    "Acme Corp", "Apex Industries", "Aurora Technologies",
    "Beacon Systems", "BlueShift Analytics", "Brightwave Digital",
    "Cascade Networks", "Cobalt Solutions", "Coral Health",
    "Crescent Financial", "CrystalBridge", "CyanTech",
    "DeltaForce Logistics", "Driftwood Media", "EchoVault",
    "Ember Robotics", "Evergreen Consulting", "FalconEdge",
    "Firestone Manufacturing", "FluxPoint", "GreenLeaf Agri",
    "GridIron Security", "Harborview Properties", "Helix Biotech",
    "Horizon Capital", "IronClad Insurance", "Jade Commerce",
    "Keystone Education", "Lantern Health", "Lighthouse Partners",
    "Magnolia Retail", "Marble Fintech", "Meridian Telecom",
    "Mistral AI Solutions", "Mosaic Learning", "NexGen Pharma",
    "Nimbus Cloud", "Northwind Traders", "OakBridge Ventures",
    "Obsidian Data", "Onyx Energy", "Opal Healthcare",
    "Orbit Aerospace", "Palladium Group", "Paragon Logistics",
    "Pinnacle Ventures", "Prism Analytics", "Quartz Semiconductors",
    "Radiant Solar", "RedRock Mining", "Ripple Payments",
    "Rosewood Hotels", "Sapphire Technologies", "Sentinel Defense",
    "SilverLine Transit", "Skyward Aviation", "Solstice Wellness",
    "Spectrum Communications", "Starlight Entertainment",
    "Summit Financial", "Terracotta Design", "Tidal Wave Sports",
    "Titanium Aerospace", "Topaz Insurance", "Trident Shipping",
    "Umbra Security", "Unity Healthcare", "Vanguard Capital",
    "Velvet Fashion", "Vertex Engineering", "Wildfire Games",
    "WindChime Music", "Xenon Labs", "Yellowstone Outdoors",
    "Zenith Global", "Zephyr Logistics", "Alpine Construction",
    "Bamboo Software", "Cedar Education", "Dune Energy",
    "Falcon Ridge",
]

CUSTOMER_SEGMENTS: list[str] = [
    "enterprise", "mid_market", "smb", "startup",
]

CUSTOMER_INDUSTRIES: list[str] = [
    "Technology", "Healthcare", "Finance", "Retail",
    "Manufacturing", "Education", "Media", "Telecommunications",
    "Energy", "Transportation", "Real Estate", "Government",
    "Non-profit", "Hospitality", "Agriculture",
]

# ---------------------------------------------------------------------------
# Date range for created_at fields
# ---------------------------------------------------------------------------
DATE_START_ISO: str = "2024-01-01"
DATE_RANGE_DAYS: int = 900  # ~2.5 years
