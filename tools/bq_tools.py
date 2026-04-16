"""
bq_tools.py - BigQuery utility functions for user/manager lookup, role checks.
Used by access_control_agent and other agents needing org-hierarchy data.

Uses ONLY two prod tables:
  1. hr_worker_snapshot_ds_hyperion_feed_hds_erp_vw  — manager check, reportees, direct-report countries
  2. hr_worker_lifecycle_oracle_cp5_vw               — VP/exec check, user/manager details (country, title, etc.)

OPTIMIZED: Single get_user_profile() fetches roles + details + reports countries
in 2 PARALLEL BQ queries (snapshot + lifecycle), cached 24h.
"""

import os
import time as _time
from concurrent.futures import ThreadPoolExecutor
from google.cloud import bigquery
from google.oauth2 import service_account
from config import SUPER_ADMIN_EMAILS, BQ_CREDENTIALS_PATH
from tools.cache_tools import cache
import logging

logger = logging.getLogger("HD_SKYE_AGENT")

# ─── Prod tables (single source of truth) ─────────────────────────────────
_PROD_PROJECT = "hd-onedata-prod"
_PROD_DATASET = "hd1d_consumption_hds_hr"
_WORKER_SNAPSHOT_VIEW = (
    f"{_PROD_PROJECT}.{_PROD_DATASET}.hr_worker_snapshot_ds_hyperion_feed_hds_erp_vw"
)
_WORKER_LIFECYCLE_VIEW = (
    f"{_PROD_PROJECT}.{_PROD_DATASET}.hr_worker_lifecycle_oracle_cp5_vw"
)

# ─── Singleton client ────────────────────────────────────────────────────────
_bq_client = None


def get_bq_client():
    """Returns a BigQuery client authenticated with BQ credentials for prod (singleton)."""
    global _bq_client
    if _bq_client is not None:
        return _bq_client
    creds_path = BQ_CREDENTIALS_PATH
    if not os.path.exists(creds_path):
        logger.error(f"BQ credentials not found at: {creds_path}")
        return None
    try:
        credentials = service_account.Credentials.from_service_account_file(creds_path)
        _bq_client = bigquery.Client(project=_PROD_PROJECT, credentials=credentials)
        logger.info(f"BQ client initialized for {_PROD_PROJECT}")
        return _bq_client
    except Exception as e:
        logger.error(f"Failed to init BQ client: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# UNIFIED USER PROFILE — single cached object with roles + details + countries
# Reduces cold-cache from 4+ sequential BQ queries → 2 parallel queries
# ═══════════════════════════════════════════════════════════════════════════════

_profile_executor = ThreadPoolExecutor(max_workers=2)


def get_user_profile(email: str) -> dict:
    """
    Fetch everything about a user in ONE call (2 parallel BQ queries).
    Returns: {
        "roles": {"is_manager": bool, "is_vp": bool, "is_executive": bool, "is_super_admin": bool},
        "details": {"name": str, "country": str, "region": str|None, "location": str, "job_title": str, "department": str} | None,
        "reports_countries": [str, ...],
        "reports_regions": [str, ...],
    }
    Cached for 24 hours.
    """
    empty_roles = {
        "is_manager": False,
        "is_vp": False,
        "is_executive": False,
        "is_super_admin": False,
    }
    if not email:
        return {
            "roles": empty_roles,
            "details": None,
            "reports_countries": [],
            "reports_regions": [],
        }

    cache_key = f"user_profile_v1:{email}"
    cached = cache.get(cache_key)
    if cached:
        cached["roles"]["is_super_admin"] = email.strip().lower() in SUPER_ADMIN_EMAILS
        return cached

    t0 = _time.time()
    client = get_bq_client()
    if not client:
        empty_roles["is_super_admin"] = email.strip().lower() in SUPER_ADMIN_EMAILS
        return {
            "roles": empty_roles,
            "details": None,
            "reports_countries": [],
            "reports_regions": [],
        }

    # ── Run snapshot + lifecycle in PARALLEL ──────────────────────────────
    snap_result = {"report_count": 0, "countries": [], "regions": []}
    life_result = None

    def _query_snapshot():
        q = f"""
            SELECT
                COUNT(1) as report_count,
                ARRAY_AGG(DISTINCT EMPLOYING_COUNTRY IGNORE NULLS) as countries,
                ARRAY_AGG(DISTINCT EMPLOYING_REGION IGNORE NULLS) as regions
            FROM `{_WORKER_SNAPSHOT_VIEW}`
            WHERE LOWER(MANAGER_EMAIL) = LOWER(@email)
        """
        jc = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", email)]
        )
        for row in client.query(q, job_config=jc).result():
            return {
                "report_count": row.report_count,
                "countries": list(row.countries or []),
                "regions": list(row.regions or []),
            }
        return {"report_count": 0, "countries": [], "regions": []}

    def _query_lifecycle():
        q = f"""
            SELECT *
            FROM `{_WORKER_LIFECYCLE_VIEW}`
            WHERE `Email Address` = @email
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY `Employee ID`
                ORDER BY `HD1D Updated At` DESC
            ) = 1
        """
        jc = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", email)]
        )
        for row in client.query(q, job_config=jc).result():
            return dict(row.items())
        return None

    try:
        fut_snap = _profile_executor.submit(_query_snapshot)
        fut_life = _profile_executor.submit(_query_lifecycle)
        snap_result = fut_snap.result(timeout=30)
        life_result = fut_life.result(timeout=30)
    except Exception as e:
        logger.error(f"Parallel profile query failed for {email}: {e}")

    # ── Extract roles ─────────────────────────────────────────────────────
    is_manager = snap_result.get("report_count", 0) > 0
    is_vp = False
    is_exec = False
    if life_result:
        title = (
            life_result.get("Title") or life_result.get("Oracle Job") or ""
        ).lower()
        level = (
            life_result.get("HR Job Level Code")
            or life_result.get("HR Job Level Name")
            or ""
        ).upper()
        is_vp = title.startswith("vice") or title.startswith("vp")
        is_exec = any(level.startswith(p) for p in ("E2", "E3", "E4", "E5"))

    # ── HR / Finance department check (Oracle Department ID only) ────
    # HR:      Oracle Department ID ∈ {252, 994}  → P-Card + ALL locations
    # Finance: Oracle Department ID ∈ {312, 994}  → P-Card only
    is_hr = False
    is_finance = False
    if life_result:
        dept_id = str(life_result.get("Oracle Department ID") or "").strip()
        if dept_id in ("252", "994"):
            is_hr = True
        if dept_id in ("312", "994"):
            is_finance = True

    roles = {
        "is_manager": is_manager,
        "is_vp": is_vp,
        "is_executive": is_exec,
        "is_super_admin": email.strip().lower() in SUPER_ADMIN_EMAILS,
        "is_hr": is_hr,
        "is_finance": is_finance,
        "is_hr_finance": is_hr or is_finance,
    }

    # ── Extract details ───────────────────────────────────────────────────
    details = None
    if life_result:
        details = {
            "name": f"{life_result.get('First Name', '')} {life_result.get('Last Name', '')}".strip()
            or life_result.get("Employee Nickname"),
            "country": life_result.get("Work Country Desc")
            or life_result.get("Work Country Code"),
            "region": life_result.get("EMPLOYING_REGION")
            or life_result.get("Employing Region"),
            "location": life_result.get("Oracle Location"),
            "job_title": life_result.get("Title") or life_result.get("Oracle Job"),
            "department": life_result.get("Oracle Department")
            or life_result.get("Sub Team"),
        }

    # ── Extract reports countries + regions ────────────────────────────────
    reports_countries = snap_result.get("countries", [])
    reports_regions = snap_result.get("regions", [])

    profile = {
        "roles": roles,
        "details": details,
        "reports_countries": reports_countries,
        "reports_regions": reports_regions,
    }
    cache.set(cache_key, profile, ttl=86400)
    elapsed = _time.time() - t0
    logger.info(
        f"User profile for {email} loaded in {elapsed:.2f}s: roles={roles}, "
        f"country={details.get('country') if details else 'N/A'}, "
        f"region={details.get('region') if details else 'N/A'}, "
        f"reports_countries={reports_countries}, reports_regions={reports_regions}"
    )
    return profile


# ═══════════════════════════════════════════════════════════════════════════════
# Backward-compatible wrappers — delegate to get_user_profile(), zero extra BQ
# ═══════════════════════════════════════════════════════════════════════════════


def get_user_roles(email: str) -> dict:
    return get_user_profile(email)["roles"]


def check_is_manager(email: str) -> bool:
    return get_user_roles(email).get("is_manager", False)


def get_user_details_from_bq(email: str) -> dict | None:
    return get_user_profile(email)["details"]


def get_manager_details_from_bq(email: str) -> dict | None:
    details = get_user_profile(email)["details"]
    if not details:
        return None
    return {
        "name": details.get("name"),
        "country": details.get("country"),
        "location": details.get("location"),
    }


def get_direct_reports_countries(manager_email: str) -> list:
    return get_user_profile(manager_email)["reports_countries"]


def get_reportees_for_user(email: str, is_vp: bool = False) -> list:
    """Returns flat list of reportees from snapshot table. Manager=1 level, VP=2 levels."""
    if not email:
        return []
    cache_key = f"reportees_list_v3:{email}:{is_vp}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        client = get_bq_client()
        if not client:
            return []

        def _fetch_under(mgr_email: str) -> list:
            q = f"""
                SELECT *
                FROM `{_WORKER_SNAPSHOT_VIEW}`
                WHERE LOWER(MANAGER_EMAIL) = LOWER(@mgr_email)
            """
            jc = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("mgr_email", "STRING", mgr_email)
                ]
            )
            results = []
            for row in client.query(q, job_config=jc).result():
                rd = dict(row.items())
                results.append(
                    {
                        "name": rd.get("EMPLOYEE_NAME", ""),
                        "preferred_name": rd.get("PREFFERED_NAME", "")
                        or rd.get("PREFERRED_NAME", ""),
                        "email": rd.get("EMAIL_ADDRESS", ""),
                        "country": rd.get("EMPLOYING_COUNTRY", ""),
                        "region": rd.get("EMPLOYING_REGION", ""),
                        "location": rd.get("WORK_LOCATION_NAME", ""),
                        "ldap_id": rd.get("LDAP_ID", ""),
                        "manager_email": rd.get("MANAGER_EMAIL", ""),
                    }
                )
            logger.info(f"Fetched {len(results)} direct reports under {mgr_email}")
            return results

        reportees = []
        if is_vp:
            mid_managers = _fetch_under(email)
            seen_emails = set()
            for mgr in mid_managers:
                me = mgr.get("email")
                if me and me not in seen_emails:
                    seen_emails.add(me)
                    reportees.extend(_fetch_under(me))
            reportees.extend(mid_managers)
        else:
            reportees = _fetch_under(email)

        # Deduplicate
        seen = set()
        unique = []
        for r in reportees:
            key = r.get("email", "").lower() or r.get("name", "").lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(r)

        if unique:
            cache.set(cache_key, unique, ttl=3600)
        logger.info(f"Total unique reportees for {email}: {len(unique)}")
        return unique
    except Exception as e:
        logger.error(f"Error fetching reportees for {email}: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Employee search
# ═══════════════════════════════════════════════════════════════════════════════


def find_employee_in_reportees(reportees: list, name_query: str) -> list:
    """Search reportees by name or LDAP ID (partial, case-insensitive)."""
    if not reportees or not name_query:
        return []
    q = name_query.strip().lower()
    if q.startswith("ldap:"):
        ldap_id = q[5:].strip()
        return [
            r for r in reportees if ldap_id and ldap_id in r.get("ldap_id", "").lower()
        ]
    matched = [
        r
        for r in reportees
        if q in r.get("name", "").lower() or q in r.get("preferred_name", "").lower()
    ]
    if not matched:
        parts = q.split()
        if parts:
            matched = [
                r
                for r in reportees
                if any(
                    p in r.get("name", "").lower()
                    or p in r.get("preferred_name", "").lower()
                    for p in parts
                )
            ]
    return matched


def search_employee_globally(name_query: str) -> list:
    """Search any employee by name across the snapshot table (super admin only)."""
    if not name_query:
        return []
    cache_key = f"global_emp_search:{name_query.strip().lower()}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        client = get_bq_client()
        if not client:
            return []
        query = f"""
            SELECT *
            FROM `{_WORKER_SNAPSHOT_VIEW}`
            WHERE LOWER(EMPLOYEE_NAME) LIKE CONCAT('%', LOWER(@name), '%')
            LIMIT 20
        """
        jc = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("name", "STRING", name_query.strip())
            ]
        )
        results = []
        for row in client.query(query, job_config=jc).result():
            rd = dict(row.items())
            results.append(
                {
                    "name": rd.get("EMPLOYEE_NAME", ""),
                    "email": rd.get("EMAIL_ADDRESS", ""),
                    "country": rd.get("EMPLOYING_COUNTRY", ""),
                    "region": rd.get("EMPLOYING_REGION", ""),
                    "location": rd.get("WORK_LOCATION_NAME", ""),
                    "ldap_id": rd.get("LDAP_ID", ""),
                    "manager_email": rd.get("MANAGER_EMAIL", ""),
                }
            )
        if results:
            cache.set(cache_key, results, ttl=3600)
        return results
    except Exception as e:
        logger.error(f"Error searching employee globally for '{name_query}': {e}")
        return []
