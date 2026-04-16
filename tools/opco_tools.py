"""
opco_tools.py - OPCO classification, HV filtering, holiday/pcard detection.
Shared utility used by guardrails_agent and retrieval_agent.
"""

import os
import re

# ─── Hitachi Vantara Patterns ────────────────────────────────────────────────
HV_PATTERNS = [
    r"(?:^|[-_ \s])hv(?:[-_ \s\d]|$)",
    r"hitachi[\s_-]?vantara",
]
HV_RE = re.compile("|".join(HV_PATTERNS), re.IGNORECASE)

# ─── OPCO Detection ─────────────────────────────────────────────────────────
OPCO_MAP = [
    (
        re.compile(
            r"\bhds\b|hitachi[\s_-]?digital[\s_-]?services|hitachids", re.IGNORECASE
        ),
        "Hitachi Digital Services (HDS)",
    ),
    (
        re.compile(r"\bgl\b|globallogic|global[\s_-]?logic", re.IGNORECASE),
        "GlobalLogic (GL)",
    ),
    (
        re.compile(r"\bhd\b|hitachi[\s_-]?digital(?![\s_-]?services)", re.IGNORECASE),
        "Hitachi Digital (HD)",
    ),
    (
        re.compile(
            r"global|anti[\s_-]?bribery|anti[\s_-]?corruption|procurement|travel[\s_-]?and[\s_-]?expense|expense[\s_-]?policy|gift|award|corporate[\s_-]?card|cybersecurity|p[\s_-]?card",
            re.IGNORECASE,
        ),
        "Global (All OPCOs)",
    ),
]

HV_QUERY_PATTERNS = [
    r"\bhitachi[\s_-]?vantara\b",
    r"\bvantara\b",
    r"\bhv\s+(?:policy|policies|payroll|benefit|leave|holiday|rule)",
    r"(?:policy|policies|payroll|benefit|leave|holiday|rule)\s+(?:for|of|at|in)\s+hv\b",
]
HV_QUERY_RE = re.compile("|".join(HV_QUERY_PATTERNS), re.IGNORECASE)


def is_hv_source(source: str) -> bool:
    if not source:
        return False
    return bool(HV_RE.search(os.path.basename(source)))


def get_opco_entity(source: str) -> str:
    if not source:
        return "Unknown"
    name = os.path.basename(source).lower()
    for pattern, label in OPCO_MAP:
        if pattern.search(name):
            return label
    return "Regional/Other"


def is_hv_query(query: str) -> bool:
    return bool(HV_QUERY_RE.search(query)) if query else False


def get_user_opco(email: str) -> str:
    if not email:
        return "Unknown"
    email = email.lower()
    if "hitachivantara" in email:
        return "Hitachi Vantara (HV)"
    if "hitachids" in email or "hitachi-ds" in email:
        return "Hitachi Digital Services (HDS)"
    if "globallogic" in email:
        return "GlobalLogic (GL)"
    if "hitachidigital" in email:
        return "Hitachi Digital (HD)"
    return "Regional/Other"


def filter_hv_results(results: list) -> list:
    return [r for r in results if not is_hv_source(r.get("source", ""))]


def build_opco_context_note(results: list) -> str:
    if not results:
        return ""
    seen = {}
    for r in results:
        src = r.get("source", "")
        bn = os.path.basename(src)
        if bn and bn not in seen:
            seen[bn] = get_opco_entity(src)
    if not seen:
        return ""
    lines = ["OPCO ENTITY CONTEXT:"]
    for fn, entity in seen.items():
        lines.append(f"  - {fn} → {entity}")
    return "\n".join(lines)


# ─── Holiday helpers ─────────────────────────────────────────────────────────
HOLIDAY_KEYWORDS = {
    "holiday",
    "holidays",
    "public holiday",
    "day off",
    "days off",
    "bank holiday",
    "national holiday",
    "paid holiday",
    "company holiday",
    "observed holiday",
    "annual holiday",
    "leave calendar",
    "holiday calendar",
    "holiday list",
    "holiday schedule",
    "festival",
    "festivals",
    "public holidays",
    "vacation",
    "vacations",
}


def is_holiday_query(query: str) -> bool:
    if not query:
        return False
    q = query.lower()
    # Exclude process/how-to questions that mention "holiday" incidentally.
    # These are about requesting leave, not about holiday calendars.
    _PROCESS_SIGNALS = [
        "request time off",
        "how to request",
        "how can i request",
        "how do i request",
        "apply for leave",
        "apply leave",
        "request leave",
        "book time off",
        "book leave",
        "mark leave",
        "submit leave",
    ]
    if any(sig in q for sig in _PROCESS_SIGNALS):
        return False
    # Exclude bereavement/death queries that mention "days off" incidentally.
    # These are about bereavement leave policy, not holiday calendars.
    _BEREAVEMENT_SIGNALS = [
        "passed away",
        "died",
        "death",
        "funeral",
        "bereavement",
        "compassionate",
        "grandfather",
        "grandmother",
        "father",
        "mother",
        "parent",
        "spouse",
        "family member",
    ]
    if any(sig in q for sig in _BEREAVEMENT_SIGNALS):
        return False
    return any(kw in q for kw in HOLIDAY_KEYWORDS)


def is_holiday_file(source: str) -> bool:
    if not source:
        return False
    return "holiday" in os.path.basename(source).lower()


def is_holiday_result(result: dict) -> bool:
    """Check if a retrieval result is holiday-related.

    Checks both the source filename (fast) and the chunk text (fallback for
    ServiceNow KB articles that have generic filenames like
    ``ServiceNow_KB_KB00XXXXX.html`` but contain holiday calendar tables).
    """
    if is_holiday_file(result.get("source", "")):
        return True
    text = result.get("text", "").lower()
    # Holiday calendar tables typically have day/date/event columns and month names
    _HOLIDAY_TEXT_SIGNALS = [
        "available holidays by country",
        "holiday calendar",
        "holiday list",
        "holiday schedule",
        "public holiday",
        "public holidays",
        "national holiday",
        "bank holiday",
        "observed holiday",
        "📅",  # Emoji flag used in ServiceNow holiday tables
        "gazetted holiday",
        "restricted holiday",
        "company holiday",
        "paid holiday",
    ]
    return any(sig in text for sig in _HOLIDAY_TEXT_SIGNALS)


def prioritize_holiday_results(results: list) -> list:
    holidays = [r for r in results if is_holiday_result(r)]
    others = [r for r in results if not is_holiday_result(r)]
    return holidays + others


# ─── P-Card helpers ──────────────────────────────────────────────────────────
# Core P-Card keywords — these are highly specific and unlikely to be false positives.
# "credit card" and "business card" were removed because they trigger on generic
# queries about business cards (stationery) and credit card payment methods.
# "corporate credit card" is kept because translations (especially Japanese
# コーポレートカード) often produce "corporate credit card" rather than
# "corporate card", and this compound phrase is unambiguous.
PCARD_KEYWORDS = {
    "p-card",
    "pcard",
    "p card",
    "procurement card",
    "corporate card",
    "corporate credit card",
    "cardholder",
}

# Patterns that signal the query is an example list / menu, not an actual P-Card query
# e.g. "Which topic? (e.g., Travel & Expense, GenAI, Corporate Card, ...)"
_EXAMPLE_LIST_RE = re.compile(
    r"\((?:e\.g\.?|for example|such as|like)[^)]*\)", re.IGNORECASE
)


def is_p_card_query(query: str) -> bool:
    """Detect if the query is about P-Card / procurement card content.

    Returns False when P-Card keywords only appear inside parenthetical
    example lists (e.g., welcome message suggestions) to avoid false positives.
    """
    if not query:
        return False
    q = query.lower()

    # Quick check: does any keyword appear at all?
    if not any(kw in q for kw in PCARD_KEYWORDS):
        return False

    # Strip out parenthetical example lists and re-check
    q_stripped = _EXAMPLE_LIST_RE.sub("", q)
    return any(kw in q_stripped for kw in PCARD_KEYWORDS)


def is_pcard_file(source: str) -> bool:
    if not source:
        return False
    name = os.path.basename(source).lower()
    is_dedicated = (
        ("corporate" in name and "card" in name) or "pcard" in name or "p-card" in name
    )
    is_trap = "gift" in name or "faq" in name
    return is_dedicated and not is_trap


def prioritize_pcard_results(results: list) -> list:
    pcards = [r for r in results if is_pcard_file(r.get("source", ""))]
    others = [r for r in results if not is_pcard_file(r.get("source", ""))]
    return pcards + others
