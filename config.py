"""
config.py - Centralized configuration for the HD SKYE Agentic System.
Loads environment variables and provides constants used across all agents.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List

from dotenv import load_dotenv

logger = logging.getLogger("HD_SKYE_AGENT")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── Environment Selection ───────────────────────────────────────────────────
# Set SKYE_ENV to load a named env file for local testing:
#   SKYE_ENV=poc  → loads .env.poc  (POC project resources)
#   SKYE_ENV=prod → loads .env.prod (Prod project resources)
#   (unset)       → loads .env      (backward compatible default)
_skye_env = os.environ.get("SKYE_ENV", "").strip().lower()
if _skye_env:
    _env_file = os.path.join(BASE_DIR, f".env.{_skye_env}")
    if os.path.exists(_env_file):
        logger.info(f"Loading environment: .env.{_skye_env}")
        load_dotenv(_env_file)
    else:
        logger.warning(
            f"SKYE_ENV={_skye_env} but .env.{_skye_env} not found, falling back to .env"
        )
        load_dotenv(os.path.join(BASE_DIR, ".env"))
else:
    load_dotenv(os.path.join(BASE_DIR, ".env"))

# ─── Google Cloud ────────────────────────────────────────────────────────────
PROJECT_ID = os.getenv("PROJECT_ID")
REGION = os.getenv("REGION", "us-central1")
GCS_BUCKET_NAME = os.getenv("HD_SKYE_GCS_BUCKET_NAME", os.getenv("GCS_BUCKET_NAME"))
GCS_DOCUMENTS_PREFIX = os.getenv("HD_SKYE_DOCUMENTS_PREFIX", "hd-skye-2.0/Documents/")

# ─── ServiceNow ──────────────────────────────────────────────────────────────
# Base URL for linking users to KB articles on the ServiceNow portal.
SERVICENOW_PORTAL_URL = os.getenv(
    "SERVICENOW_PORTAL_URL",
    "https://hitachivantara.service-now.com/asknow",
)
GCS_SERVICENOW_KB_PREFIX = os.getenv(
    "HD_SKYE_SERVICENOW_KB_PREFIX", "servicenow_kb_extraction/"
)
INDEX_ID = os.getenv("HD_SKYE_INDEX_ID", os.getenv("INDEX_ID"))
INDEX_ENDPOINT_ID = os.getenv(
    "HD_SKYE_INDEX_ENDPOINT_ID", os.getenv("INDEX_ENDPOINT_ID")
)
DEPLOYED_INDEX_ID = os.getenv(
    "HD_SKYE_DEPLOYED_INDEX_ID", os.getenv("DEPLOYED_INDEX_ID", "rag_hr_deployed_index")
)
BQ_CREDENTIALS_PATH = os.getenv(
    "BQ_CREDENTIALS_PATH",
    os.getenv("BIGQUERY_APPLICATION_CREDENTIALS", os.path.join(BASE_DIR, "auth2.json")),
)

# ─── Firestore ───────────────────────────────────────────────────────────────
FIRESTORE_COLLECTION = os.getenv(
    "HD_SKYE_FIRESTORE_COLLECTION",
    os.getenv("FIRESTORE_COLLECTION", "hr_policy_chunks"),
)
FIRESTORE_DATABASE = os.getenv("HD_SKYE_FIRESTORE_DB", "(default)")

# ─── BigQuery Tables ─────────────────────────────────────────────────────────
BQ_USER_SNAPSHOT_TABLE = os.getenv(
    "BQ_USER_SNAPSHOT_TABLE",
    "hd-onedata-prod.hd1d_consumption_hds_hr.hr_worker_snapshot_ds_hyperion_feed_hds_erp_vw",
)
BQ_MANAGER_SNAPSHOT_TABLE = os.getenv(
    "BQ_MANAGER_SNAPSHOT_TABLE",
    "hd-onedata-prod.hd1d_consumption_hds_hr.hr_worker_snapshot_manager_okta_hds_erp_vw",
)
BQ_GBLC_LIFECYCLE_TABLE = os.getenv(
    "BQ_GBLC_LIFECYCLE_TABLE",
    "hd-onedata-prod.hd1d_consumption_gblc_hr.hr_worker_lifecycle_assignment_gblc_erp_vw",
)
BQ_GBLC_JOB_MAP_TABLE = os.getenv(
    "BQ_GBLC_JOB_MAP_TABLE", "hd-onedata-prod.hd1d_mdm.hr_hinext_gblc_job_family_map"
)

# ─── Document AI ─────────────────────────────────────────────────────────────
DOCUMENT_AI_OCR_ID = os.getenv("DOCUMENT_AI_OCR_ID")
DOCUMENT_AI_FORM_PARSER_ID = os.getenv("DOCUMENT_AI_FORM_PARSER_ID")
DOCUMENT_AI_LOCATION = os.getenv("DOCUMENT_AI_LOCATION", "us")

# ─── Redis ───────────────────────────────────────────────────────────────────
# REDIS_HOST = os.getenv("REDIS_HOST", "10.180.68.37")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "skye:")  # Namespace all keys

# ─── Super Admin ─────────────────────────────────────────────────────────────
SUPER_ADMIN_EMAILS = [
    e.strip().lower()
    for e in os.getenv("SUPER_ADMIN_EMAILS", "").split(",")
    if e.strip()
]

# ─── ServiceNow KB Index (separate endpoint + separate Firestore DB) ─────────
SERVICENOW_INDEX_ENDPOINT_ID = os.getenv("SERVICENOW_INDEX_ENDPOINT_ID", "")
SERVICENOW_DEPLOYED_INDEX_ID = os.getenv(
    "SERVICENOW_DEPLOYED_INDEX_ID", "hd_skye_agents_servicenow"
)
SERVICENOW_FIRESTORE_DB = os.getenv("SERVICENOW_FIRESTORE_DB", "hd-skye-db-servicenow")
SERVICENOW_FIRESTORE_COLLECTION = os.getenv(
    "SERVICENOW_FIRESTORE_COLLECTION", "hd-skye-chunks-servicenow"
)

# ─── APAC Payroll Index (on main endpoint, Firestore in ServiceNow DB) ──────
APAC_PAYROLL_FIRESTORE_DB = os.getenv(
    "APAC_PAYROLL_FIRESTORE_DB", "hd-skye-db-servicenow"
)
APAC_PAYROLL_FIRESTORE_COLLECTION = os.getenv(
    "APAC_PAYROLL_FIRESTORE_COLLECTION", "apac-payroll-chunks"
)

# ─── P-Card Index (on ServiceNow endpoint) ──────────────────────────────────
PCARD_DEPLOYED_INDEX_ID = os.getenv("PCARD_DEPLOYED_INDEX_ID", "p_card_1774597634873")
PCARD_FIRESTORE_DB = os.getenv("PCARD_FIRESTORE_DB", "hd-skye-db-servicenow")
PCARD_FIRESTORE_COLLECTION = os.getenv("PCARD_FIRESTORE_COLLECTION", "p-card_policy")

# ─── Bulk Expense Index (on ServiceNow endpoint) ────────────────────────────
BULK_EXP_DEPLOYED_INDEX_ID = os.getenv(
    "BULK_EXP_DEPLOYED_INDEX_ID", "bulk_exp_1774599081220"
)
BULK_EXP_FIRESTORE_DB = os.getenv("BULK_EXP_FIRESTORE_DB", "hd-skye-db-servicenow")
BULK_EXP_FIRESTORE_COLLECTION = os.getenv(
    "BULK_EXP_FIRESTORE_COLLECTION", "bulk-expense"
)

# ─── Cache TTLs & Thresholds ─────────────────────────────────────────────────
# All values in seconds. Override via environment variables.
CACHE_TTL_SEARCH = int(
    os.getenv("CACHE_TTL_SEARCH", 3600)
)  # 1 hour  — exact-match search cache
CACHE_TTL_ANSWER = int(
    os.getenv("CACHE_TTL_ANSWER", 10800)
)  # 3 hours — answer / semantic cache
CACHE_TTL_HISTORY = int(
    os.getenv("CACHE_TTL_HISTORY", 86400)
)  # 24 hours — conversation history
CACHE_TTL_SESSION = int(
    os.getenv("CACHE_TTL_SESSION", 86400)
)  # 24 hours — session context
CACHE_TTL_PROFILE = int(
    os.getenv("CACHE_TTL_PROFILE", 86400)
)  # 24 hours — user profile cache
CACHE_TTL_COUNTER = int(
    os.getenv("CACHE_TTL_COUNTER", 604800)
)  # 7 days  — semantic cache counter
CACHE_TTL_COUNTRY = int(
    os.getenv("CACHE_TTL_COUNTRY", 2592000)
)  # 30 days — resolved country cache
SIMILARITY_THRESHOLD = float(
    os.getenv("SIMILARITY_THRESHOLD", 0.95)
)  # cosine sim for semantic cache

# ─── Application ─────────────────────────────────────────────────────────────
TENANT = os.getenv("TENANT", "hd-skye")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-004")

# gemini-2.5-* models use "thinking" tokens by default which adds 3-5x latency.
# Set LLM_THINKING_BUDGET to 0 to disable, or a positive int to cap thinking tokens.
# Ignored for models that don't support thinking (gemini-2.0-flash, etc.).
LLM_THINKING_BUDGET: int | None = None
_thinking_raw = os.getenv("LLM_THINKING_BUDGET", "").strip()
if _thinking_raw:
    LLM_THINKING_BUDGET = int(_thinking_raw)
elif "2.5" in LLM_MODEL:
    # Auto-disable thinking for 2.5 models to match 2.0-flash latency
    LLM_THINKING_BUDGET = 0


def get_llm_generation_config() -> dict | None:
    """Return generation_config dict for GenerativeModel, or None if defaults are fine."""
    if LLM_THINKING_BUDGET is not None:
        return {"thinking_config": {"thinking_budget": LLM_THINKING_BUDGET}}
    return None


if not PROJECT_ID:
    raise ValueError("PROJECT_ID not set in .env")


# ─── Dynamic Index Groups ────────────────────────────────────────────────────
# Each index group maps a friendly name to its Vector Search + Firestore config.
# Groups are enabled/disabled at runtime via the ENABLED_INDEX_GROUPS env var.


@dataclass(frozen=True)
class IndexGroupConfig:
    """Configuration for a single searchable index group."""

    name: str
    endpoint_id: str
    deployed_index_id: str
    firestore_database: str
    firestore_collection: str


def _build_index_group_registry() -> Dict[str, IndexGroupConfig]:
    """Build the full registry of *available* index groups from env vars.

    Groups whose endpoint_id is empty/unset are silently excluded so that
    partial configurations (e.g. dev environments without ServiceNow) don't
    cause errors.
    """
    candidates = [
        IndexGroupConfig(
            name="main",
            endpoint_id=INDEX_ENDPOINT_ID or "",
            deployed_index_id=DEPLOYED_INDEX_ID or "",
            firestore_database=FIRESTORE_DATABASE,
            firestore_collection=FIRESTORE_COLLECTION or "",
        ),
        IndexGroupConfig(
            name="apac_payroll",
            endpoint_id=os.getenv(
                "APAC_PAYROLL_INDEX_ENDPOINT_ID", SERVICENOW_INDEX_ENDPOINT_ID
            )
            or "",
            deployed_index_id=os.getenv(
                "APAC_PAYROLL_DEPLOYED_INDEX_ID", "apac_payroll_deployed"
            ),
            firestore_database=APAC_PAYROLL_FIRESTORE_DB,
            firestore_collection=APAC_PAYROLL_FIRESTORE_COLLECTION,
        ),
        IndexGroupConfig(
            name="servicenow_kb",
            endpoint_id=SERVICENOW_INDEX_ENDPOINT_ID or "",
            deployed_index_id=SERVICENOW_DEPLOYED_INDEX_ID or "",
            firestore_database=SERVICENOW_FIRESTORE_DB,
            firestore_collection=SERVICENOW_FIRESTORE_COLLECTION or "",
        ),
        IndexGroupConfig(
            name="pcard",
            endpoint_id=SERVICENOW_INDEX_ENDPOINT_ID or "",
            deployed_index_id=PCARD_DEPLOYED_INDEX_ID or "",
            firestore_database=PCARD_FIRESTORE_DB,
            firestore_collection=PCARD_FIRESTORE_COLLECTION or "",
        ),
        IndexGroupConfig(
            name="bulk_exp",
            endpoint_id=SERVICENOW_INDEX_ENDPOINT_ID or "",
            deployed_index_id=BULK_EXP_DEPLOYED_INDEX_ID or "",
            firestore_database=BULK_EXP_FIRESTORE_DB,
            firestore_collection=BULK_EXP_FIRESTORE_COLLECTION or "",
        ),
    ]
    return {
        g.name: g
        for g in candidates
        if g.endpoint_id and g.deployed_index_id and g.firestore_collection
    }


INDEX_GROUP_REGISTRY: Dict[str, IndexGroupConfig] = _build_index_group_registry()

# Comma-separated list of group names to search at query time.
# Defaults to all groups present in the registry.
_enabled_raw = os.getenv("ENABLED_INDEX_GROUPS", "")
ENABLED_INDEX_GROUPS: List[str] = (
    [g.strip() for g in _enabled_raw.split(",") if g.strip()]
    if _enabled_raw.strip()
    else list(INDEX_GROUP_REGISTRY.keys())
)


def get_enabled_index_groups() -> List[IndexGroupConfig]:
    """Return only the index groups that are both registered and enabled."""
    return [
        INDEX_GROUP_REGISTRY[name]
        for name in ENABLED_INDEX_GROUPS
        if name in INDEX_GROUP_REGISTRY
    ]


# ─── Agent Variant Configuration ─────────────────────────────────────────────
# Each variant defines a named agent configuration with its own set of index
# groups. Variants are configured via env vars for easy customization.
#
# Env var pattern:
#   VARIANT_{NAME}_INDEX_GROUPS     — comma-separated index group names
#   VARIANT_{NAME}_PRIORITY_GROUPS  — comma-separated index groups to prioritize
#
# E.g.:  VARIANT_PCARD_INDEX_GROUPS="servicenow_kb,pcard"
#        VARIANT_PCARD_PRIORITY_GROUPS="pcard"

# Score boost applied to results from priority index groups during reranking.
INDEX_PRIORITY_BOOST = float(os.getenv("INDEX_PRIORITY_BOOST", "0.20"))


@dataclass(frozen=True)
class AgentVariantConfig:
    """Configuration for a named agent variant."""

    name: str
    display_name: str
    description: str
    index_groups: List[str]
    priority_index_groups: List[str]  # Index groups whose results get boosted


def _parse_variant_index_groups(env_key: str, default: str) -> List[str]:
    """Parse a comma-separated list of index group names from an env var."""
    raw = os.getenv(env_key, default)
    return [g.strip() for g in raw.split(",") if g.strip()]


def _build_variant_registry() -> Dict[str, AgentVariantConfig]:
    """Build the registry of agent variants from env vars.

    Each variant maps to a specific set of index groups. The index groups
    themselves are validated against the INDEX_GROUP_REGISTRY at query time,
    so variants can reference groups that may not be available in every
    deployment environment.

    ``priority_index_groups`` controls which index groups receive a score
    boost during reranking.  This makes the variant "prefer" answers from
    its specialty indexes while still having ServiceNow as fallback context.
    """
    variants = [
        AgentVariantConfig(
            name="main",
            display_name="Skye HR Agent",
            description="Main HR agent — searches all available indexes",
            index_groups=_parse_variant_index_groups(
                "VARIANT_MAIN_INDEX_GROUPS",
                "servicenow_kb,main,apac_payroll,pcard,bulk_exp",
            ),
            priority_index_groups=_parse_variant_index_groups(
                "VARIANT_MAIN_PRIORITY_GROUPS",
                "",  # No priority — all indexes equal for the main agent
            ),
        ),
        AgentVariantConfig(
            name="pcard",
            display_name="Skye P-Card Agent",
            description="P-Card specialist — searches ServiceNow KB and P-Card policy index",
            index_groups=_parse_variant_index_groups(
                "VARIANT_PCARD_INDEX_GROUPS",
                "servicenow_kb,pcard",
            ),
            priority_index_groups=_parse_variant_index_groups(
                "VARIANT_PCARD_PRIORITY_GROUPS",
                "pcard",
            ),
        ),
        AgentVariantConfig(
            name="bulk_expense",
            display_name="Skye Bulk Expense Agent",
            description="Bulk Expense specialist — searches ServiceNow KB and Bulk Expense index",
            index_groups=_parse_variant_index_groups(
                "VARIANT_BULK_EXPENSE_INDEX_GROUPS",
                "servicenow_kb,bulk_exp",
            ),
            priority_index_groups=_parse_variant_index_groups(
                "VARIANT_BULK_EXPENSE_PRIORITY_GROUPS",
                "bulk_exp",
            ),
        ),
        AgentVariantConfig(
            name="payroll",
            display_name="Skye Payroll Agent",
            description="Payroll specialist — searches APAC Payroll index only",
            index_groups=_parse_variant_index_groups(
                "VARIANT_PAYROLL_INDEX_GROUPS",
                "apac_payroll",
            ),
            priority_index_groups=_parse_variant_index_groups(
                "VARIANT_PAYROLL_PRIORITY_GROUPS",
                "apac_payroll",
            ),
        ),
    ]
    return {v.name: v for v in variants}


VARIANT_REGISTRY: Dict[str, AgentVariantConfig] = _build_variant_registry()

# Default variant used when no variant is specified
DEFAULT_VARIANT = os.getenv("DEFAULT_VARIANT", "main")


def get_variant_config(variant_name: str) -> AgentVariantConfig:
    """Get the configuration for a specific agent variant.

    Falls back to the default variant if the requested one is not found.
    """
    return VARIANT_REGISTRY.get(variant_name, VARIANT_REGISTRY[DEFAULT_VARIANT])


def get_variant_index_groups(variant_name: str) -> List[IndexGroupConfig]:
    """Return the index groups for a specific agent variant.

    Only returns groups that are both listed in the variant config AND
    present in the INDEX_GROUP_REGISTRY (i.e., properly configured).
    """
    variant = get_variant_config(variant_name)
    return [
        INDEX_GROUP_REGISTRY[name]
        for name in variant.index_groups
        if name in INDEX_GROUP_REGISTRY
    ]


def get_variant_priority_collections(variant_name: str) -> set:
    """Return the Firestore collection names that should be prioritized.

    Maps the variant's ``priority_index_groups`` (index group names like
    "pcard") to their Firestore collection names (like "p-card_policy")
    since retrieval results carry the ``collection`` field.

    Returns an empty set when no priority groups are configured (e.g. the
    main variant), meaning no boost is applied.
    """
    variant = get_variant_config(variant_name)
    if not variant.priority_index_groups:
        return set()
    return {
        INDEX_GROUP_REGISTRY[name].firestore_collection
        for name in variant.priority_index_groups
        if name in INDEX_GROUP_REGISTRY
    }


# Mapping from index group names to their ``category`` field values in
# Firestore (servicenow_kb collection).  Used by variant-specific category
# boosting.  Only P-Card and Bulk Expense have category values populated
# in the servicenow_kb collection.  Payroll lives in a separate index/
# Firestore DB so it's already isolated — no category boosting needed.
INDEX_GROUP_CATEGORIES = {
    "pcard": {"P Card"},
    "bulk_exp": {"Bulk Expense"},
}


# ─── Region Filter Mode ──────────────────────────────────────────────────────
# Controls which user roles get region-based result filtering during reranking.
#
#   "all"          — All users see region-filtered results (default)
#   "managers_up"  — Only Manager, VP, Executive, and Super Admin roles
#   "vp_up"        — Only VP, Executive, and Super Admin roles
#   "none"         — Disable region filtering for everyone
#
# When region filtering is DISABLED for a user:
#   - No region boost (+0.15) is applied to chunks matching the user's region
#   - No other-region penalty / exclusion is applied
#   - All results are ranked purely by semantic relevance + variant boosts
#
# This does NOT affect access control location gating (check_access).
# A user denied access to a region is still denied regardless of this setting.
REGION_FILTER_MODE = os.getenv("REGION_FILTER_MODE", "all")


def should_apply_region_filter(roles: dict) -> bool:
    """Determine whether region-based reranking should apply for a user.

    Checks ``REGION_FILTER_MODE`` against the user's resolved roles dict
    (from ``access_control_agent.check_access``).

    Returns True if region boost / other-region penalty should be applied
    during reranking, False to skip region filtering entirely.
    """
    mode = REGION_FILTER_MODE.lower().strip()

    if mode == "none":
        return False
    if mode == "all":
        return True
    if mode == "vp_up":
        return bool(
            roles.get("is_vp")
            or roles.get("is_executive")
            or roles.get("is_super_admin")
        )
    if mode == "managers_up":
        return bool(
            roles.get("is_manager")
            or roles.get("is_vp")
            or roles.get("is_executive")
            or roles.get("is_super_admin")
        )
    # Unknown mode — default to applying region filter
    logger.warning(
        f"Unknown REGION_FILTER_MODE='{REGION_FILTER_MODE}', defaulting to 'all'"
    )
    return True


def get_variant_priority_categories(variant_name: str) -> set:
    """Return the ``category`` values to boost for a variant.

    Maps the variant's ``priority_index_groups`` to their known category
    values so that results from shared collections (e.g. servicenow_kb)
    that match the variant's specialty category also get boosted.

    Returns an empty set when no category values apply.
    """
    variant = get_variant_config(variant_name)
    if not variant.priority_index_groups:
        return set()
    cats = set()
    for name in variant.priority_index_groups:
        cats |= INDEX_GROUP_CATEGORIES.get(name, set())
    return cats
