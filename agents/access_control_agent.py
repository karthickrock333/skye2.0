"""
access_control_agent.py
========================
Determines whether the authenticated user is allowed to access policy data
for the requested region.  Resolves the user's home location, their allowed
locations (including direct-reports' countries for managers/VP/executives),
and decides whether to permit or deny the query.

Access Matrix:
┌──────────────────┬──────────────┬──────────────────────────────┬─────────────────────────┐
│ User Type        │ Home Country │ Allowed Locations            │ Employee Lookup         │
├──────────────────┼──────────────┼──────────────────────────────┼─────────────────────────┤
│ Regular Employee │ From BQ/Teams│ Global + own country         │ DENIED                  │
│ Manager          │ From BQ/Teams│ Global + own + reports' ctry │ Own reportees (1 level) │
│ VP/Executive     │ From BQ/Teams│ Global + own + reports' ctry │ Reportees (2 levels)    │
│ Super Admin      │ Any          │ ALL (bypass)                 │ ALL (bypass)            │
│ data_scope=global│ Any          │ ALL (bypass)                 │ Normal rules apply      │
└──────────────────┴──────────────┴──────────────────────────────┴─────────────────────────┘

Tools used: bq_tools (BigQuery), cache_tools (Redis).
"""

from google.adk.agents import Agent
from tools.bq_tools import (
    get_user_profile,
    get_user_details_from_bq,
    get_manager_details_from_bq,
    check_is_manager,
    get_user_roles,
    get_direct_reports_countries,
)
from tools.cache_tools import cache
from config import PROJECT_ID, CACHE_TTL_COUNTRY
import logging

logger = logging.getLogger("HD_SKYE_AGENT")

# ─── Fast static country map (avoids LLM call for 99% of cases) ──────────────
_COUNTRY_MAP = {
    # Full names (already standard)
    "united states": "United States",
    "india": "India",
    "united kingdom": "United Kingdom",
    "japan": "Japan",
    "vietnam": "Vietnam",
    "australia": "Australia",
    "canada": "Canada",
    "germany": "Germany",
    "france": "France",
    "singapore": "Singapore",
    "brazil": "Brazil",
    "mexico": "Mexico",
    "china": "China",
    "south korea": "South Korea",
    "thailand": "Thailand",
    "malaysia": "Malaysia",
    "philippines": "Philippines",
    "indonesia": "Indonesia",
    "netherlands": "Netherlands",
    "spain": "Spain",
    "italy": "Italy",
    "sweden": "Sweden",
    "switzerland": "Switzerland",
    "portugal": "Portugal",
    "bangladesh": "Bangladesh",
    "belgium": "Belgium",
    "sri lanka": "Sri Lanka",
    "south africa": "South Africa",
    "new zealand": "New Zealand",
    "united arab emirates": "United Arab Emirates",
    "saudi arabia": "Saudi Arabia",
    "hong kong": "Hong Kong",
    "taiwan": "Taiwan",
    "poland": "Poland",
    "denmark": "Denmark",
    "norway": "Norway",
    "finland": "Finland",
    "ireland": "Ireland",
    "czech republic": "Czech Republic",
    "romania": "Romania",
    "argentina": "Argentina",
    "chile": "Chile",
    "colombia": "Colombia",
    "peru": "Peru",
    "egypt": "Egypt",
    "nigeria": "Nigeria",
    "kenya": "Kenya",
    "israel": "Israel",
    # ISO 2-letter codes
    "us": "United States",
    "in": "India",
    "uk": "United Kingdom",
    "gb": "United Kingdom",
    "jp": "Japan",
    "vn": "Vietnam",
    "au": "Australia",
    "ca": "Canada",
    "de": "Germany",
    "fr": "France",
    "sg": "Singapore",
    "br": "Brazil",
    "mx": "Mexico",
    "cn": "China",
    "kr": "South Korea",
    "th": "Thailand",
    "my": "Malaysia",
    "ph": "Philippines",
    "id": "Indonesia",
    "nl": "Netherlands",
    "es": "Spain",
    "it": "Italy",
    "se": "Sweden",
    "ch": "Switzerland",
    "pt": "Portugal",
    "bd": "Bangladesh",
    "be": "Belgium",
    "lk": "Sri Lanka",
    "za": "South Africa",
    "nz": "New Zealand",
    "ae": "United Arab Emirates",
    "sa": "Saudi Arabia",
    "hk": "Hong Kong",
    "tw": "Taiwan",
    "pl": "Poland",
    "dk": "Denmark",
    "no": "Norway",
    "fi": "Finland",
    "ie": "Ireland",
    "cz": "Czech Republic",
    "ro": "Romania",
    "ar": "Argentina",
    "cl": "Chile",
    "co": "Colombia",
    "pe": "Peru",
    "eg": "Egypt",
    "ng": "Nigeria",
    "ke": "Kenya",
    "il": "Israel",
    # Common aliases
    "usa": "United States",
    "america": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "britain": "United Kingdom",
    "england": "United Kingdom",
    "aus": "Australia",
    "ger": "Germany",
    "fra": "France",
    "jpn": "Japan",
    "ind": "India",
    "mex": "Mexico",
    "bra": "Brazil",
    "kor": "South Korea",
    "tha": "Thailand",
    "sgp": "Singapore",
    "mys": "Malaysia",
    "phl": "Philippines",
    "idn": "Indonesia",
    "nld": "Netherlands",
    "esp": "Spain",
    "ita": "Italy",
    "swe": "Sweden",
    "che": "Switzerland",
    "prt": "Portugal",
    "bgd": "Bangladesh",
    "bel": "Belgium",
    "lka": "Sri Lanka",
    "zaf": "South Africa",
    "nzl": "New Zealand",
    "are": "United Arab Emirates",
    "sau": "Saudi Arabia",
    "twn": "Taiwan",
}


def _resolve_country(raw: str) -> str:
    """Resolve raw location/country string to standard country name.
    Uses static map first (instant), falls back to LLM only for unknown values.
    """
    if not raw or raw.lower().strip() in (
        "global/unknown",
        "none",
        "(none)",
        "global",
        "",
    ):
        return "Global"

    # Try cache first
    cache_key = f"resolved_country_v3:{raw.strip().lower()}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    # Try static map (covers 99% of cases)
    cleaned = raw.strip().lower()
    # If the raw value contains extra info like "Country: US, UsageLocation: ..."
    # extract just the country part
    for token in cleaned.replace(",", " ").split():
        token = token.strip(".:;")
        if token in _COUNTRY_MAP:
            resolved = _COUNTRY_MAP[token]
            cache.set(cache_key, resolved, ttl=CACHE_TTL_COUNTRY)
            return resolved

    # Direct lookup
    if cleaned in _COUNTRY_MAP:
        resolved = _COUNTRY_MAP[cleaned]
        cache.set(cache_key, resolved, ttl=CACHE_TTL_COUNTRY)
        return resolved

    # Title-case might already be a valid country name
    titled = raw.strip().title()
    if titled.lower() in _COUNTRY_MAP:
        resolved = _COUNTRY_MAP[titled.lower()]
        cache.set(cache_key, resolved, ttl=CACHE_TTL_COUNTRY)
        return resolved

    # LLM fallback (rare — only for truly unknown location strings)
    try:
        from vertexai.generative_models import GenerativeModel

        model = GenerativeModel("gemini-2.5-flash")
        prompt = f"""Extract the standard COUNTRY NAME from: {raw}
Rules: Return ONLY the full country name. Map USA/US/America → United States, UK/GB → United Kingdom, AUS/AU → Australia. If ambiguous return Global."""
        resp = model.generate_content(prompt)
        resolved = resp.text.strip().replace('"', "").replace("'", "")
        cache.set(cache_key, resolved, ttl=CACHE_TTL_COUNTRY)
        logger.info(f"LLM country resolution: '{raw}' → '{resolved}'")
        return resolved
    except Exception:
        result = raw.strip().title()
        cache.set(cache_key, result, ttl=CACHE_TTL_COUNTRY)
        return result


# ─── Permission helpers ──────────────────────────────────────────────────────


def get_user_allowed_locations(
    email: str,
    teams_metadata: dict = None,
    roles: dict = None,
    data_scope: str = "regional",
    user_profile: dict = None,
) -> list:
    """Resolve full list of locations a user may access based on the role matrix."""
    if user_profile is None:
        user_profile = get_user_profile(email) if email else {}

    if roles is None:
        roles = user_profile.get("roles", {})

    # Super Admin: full bypass
    if roles.get("is_super_admin"):
        return ["ALL"]

    # data_scope=global: location bypass (employee lookup follows normal rules)
    if data_scope == "global":
        return ["ALL"]

    allowed = ["Global"]
    home_country = None

    is_mgr = roles.get("is_manager", False)
    is_vp_or_exec = roles.get("is_vp", False) or roles.get("is_executive", False)

    # 1st preference: DB (from profile — already fetched, no extra BQ call)
    user_info = user_profile.get("details")
    if user_info and user_info.get("country"):
        home_country = _resolve_country(user_info["country"])

    # 2nd preference: Teams metadata (only if DB has no country)
    # NOTE: Teams metadata keys may be capitalised ("Country") or lowercase
    # ("country") depending on the source.  Try both to be safe.
    if not home_country and teams_metadata:
        _tm_country = (
            teams_metadata.get("Country") or teams_metadata.get("country") or "None"
        )
        _tm_usage = (
            teams_metadata.get("usageLocation")
            or teams_metadata.get("UsageLocation")
            or teams_metadata.get("usagelocation")
            or "None"
        )
        meta = f"Country: {_tm_country}, UsageLocation: {_tm_usage}"
        home_country = _resolve_country(meta)

    if home_country and home_country.lower() != "global":
        allowed.append(home_country)

    # Manager, VP, Executive: also add direct reports' countries (from profile)
    if is_mgr or is_vp_or_exec:
        reports_countries = user_profile.get("reports_countries", [])
        for c in reports_countries:
            rn = _resolve_country(c)
            if rn and rn.lower() != "global" and rn not in allowed:
                allowed.append(rn)

    return sorted(set(allowed))


def is_location_allowed(target_region: str, allowed_locations: list) -> bool:
    if not target_region or target_region.lower() == "global":
        return True
    if not allowed_locations:
        return False
    if "ALL" in allowed_locations:
        return True
    variants = {
        "usa": "united states",
        "us": "united states",
        "uk": "united kingdom",
        "gb": "united kingdom",
        "in": "india",
        "jp": "japan",
        "vn": "vietnam",
    }
    resolved_target = variants.get(target_region.lower(), target_region.lower())
    for loc in allowed_locations:
        if variants.get(loc.lower(), loc.lower()) == resolved_target:
            return True
    return False


# ─── Tool functions for ADK ──────────────────────────────────────────────────


def check_access(
    email: str,
    target_region: str,
    teams_metadata: dict = None,
    data_scope: str = "regional",
) -> dict:
    """
    ADK tool: check whether user has access to policies for target_region.

    Returns: {
        allowed: bool,
        allowed_locations: [...],
        home_location: str,
        home_region: str|None,  # BQ EMPLOYING_REGION (APAC/EMEA/Americas)
        roles: {...},
        can_lookup_employees: bool,
        employee_lookup_depth: int|str,  # 0=denied, 1=one level, 2=two levels, "all"=bypass
        denial_message: str|None,
    }
    """
    # ── Single profile fetch (2 parallel BQ queries, cached 24h) ─────────
    profile = (
        get_user_profile(email)
        if email
        else {"roles": {}, "details": None, "reports_countries": []}
    )
    roles = profile["roles"]
    is_super = roles.get("is_super_admin", False)
    is_global_scope = data_scope == "global"

    allowed_locs = get_user_allowed_locations(
        email, teams_metadata, roles, data_scope, user_profile=profile
    )

    # ── Resolve home_location from profile (no extra BQ call) ────────────
    home_loc = "Global"
    home_region = None  # BQ EMPLOYING_REGION (APAC/EMEA/Americas)
    if email:
        user_info = profile.get("details")
        if user_info and user_info.get("country"):
            home_loc = _resolve_country(user_info["country"])
        elif teams_metadata:
            _tm_country = (
                teams_metadata.get("Country") or teams_metadata.get("country") or "None"
            )
            _tm_usage = (
                teams_metadata.get("usageLocation")
                or teams_metadata.get("UsageLocation")
                or teams_metadata.get("usagelocation")
                or "None"
            )
            meta = f"Country: {_tm_country}, UsageLocation: {_tm_usage}"
            resolved = _resolve_country(meta)
            if resolved and resolved.lower() != "global":
                home_loc = resolved

        # Extract home_region from BQ (primary: user's own EMPLOYING_REGION,
        # fallback: if user manages employees in a single region, use that)
        if user_info and user_info.get("region"):
            home_region = user_info["region"]
        if not home_region:
            reports_regions = profile.get("reports_regions", [])
            if len(reports_regions) == 1:
                home_region = reports_regions[0]

    # ── Employee Lookup Permission ────────────────────────────────────────
    # Super Admin: NO special employee lookup — uses normal role-based rules
    #   (super admins get full POLICY access across all regions, but cannot
    #    look up other employees unless they also hold a manager/VP/exec role)
    # VP/Executive: 2 levels deep
    # Manager: 1 level (direct reports only)
    # Regular Employee: DENIED
    # data_scope=global does NOT override employee lookup — normal rules apply
    if roles.get("is_hr"):
        can_lookup_employees = True
        employee_lookup_depth = 2
    elif roles.get("is_finance"):
        can_lookup_employees = True
        employee_lookup_depth = 2
    elif roles.get("is_vp") or roles.get("is_executive"):
        can_lookup_employees = True
        employee_lookup_depth = 2
    elif roles.get("is_manager"):
        can_lookup_employees = True
        employee_lookup_depth = 1
    else:
        can_lookup_employees = False
        employee_lookup_depth = 0

    # ── Location Access Check ─────────────────────────────────────────────
    allowed = True
    denial = None

    if is_super or is_global_scope or "ALL" in allowed_locs or roles.get("is_hr"):
        # Super Admin / data_scope=global / ALL bypass / HR dept — always allowed
        allowed = True
        if roles.get("is_hr") and "ALL" not in allowed_locs:
            allowed_locs = ["ALL"]
    elif email:
        if not is_location_allowed(target_region, allowed_locs):
            allowed = False
            display = [l for l in allowed_locs if l.lower() != "global"] or ["Global"]
            denial = (
                f"You do not have permission to access HR policies for {target_region}. "
                f"You have access to: {', '.join(display)}."
            )

    return {
        "allowed": allowed,
        "allowed_locations": allowed_locs,
        "home_location": home_loc,
        "home_region": home_region,
        "roles": roles,
        "can_lookup_employees": can_lookup_employees,
        "employee_lookup_depth": employee_lookup_depth,
        "denial_message": denial,
    }


# ─── ADK Agent Definition ───────────────────────────────────────────────────

access_control_agent = Agent(
    name="access_control_agent",
    model="gemini-2.0-flash",
    description="Determines if the user is authorized to access HR policy data for a given region.",
    instruction="""You are the Access Control Agent for HD SKYE.
Your job:
1. Given a user email, target region, teams metadata, and data scope, check if the user is allowed to access that region's policies.
2. Use the check_access tool.
3. Return the result dict with allowed status, roles, home location, and denial message if applicable.
""",
    tools=[check_access],
)
