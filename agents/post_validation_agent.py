"""
post_validation_agent.py
=========================
Post-generation validation: source attribution (which documents actually
contributed to the answer) and fallback retry logic.
"""

import os
import re
from google.adk.agents import Agent
from vertexai.generative_models import GenerativeModel
from tools.opco_tools import is_hv_source
from config import LLM_MODEL, SERVICENOW_PORTAL_URL, get_llm_generation_config
import logging

logger = logging.getLogger("HD_SKYE_AGENT")

# ─── Singleton model instance ────────────────────────────────────────────────
_pv_model = None


def _get_pv_model():
    global _pv_model
    if _pv_model is None:
        _pv_model = GenerativeModel(
            LLM_MODEL, generation_config=get_llm_generation_config()
        )
    return _pv_model


def attribute_sources(answer: str, retrieved_results: list) -> list:
    """
    LLM-based source attribution: determines which retrieved documents
    actually contributed to the generated answer.  Language-agnostic.
    """
    if not retrieved_results or not answer:
        return []

    candidates = []
    seen_sources = set()
    for i, res in enumerate(retrieved_results, 1):
        bn = os.path.basename(res.get("source", ""))
        if bn and bn != "Unknown" and not is_hv_source(bn) and bn not in seen_sources:
            # Use more text for better attribution accuracy (was 400, now 800)
            candidates.append((i, bn, res.get("text", "")[:800]))
            seen_sources.add(bn)

    if not candidates:
        return []

    numbered = "\n".join(
        f"{idx}. {name}\n   Excerpt: {exc}" for idx, name, exc in candidates
    )
    prompt = f"""You are a strict source attribution assistant. Identify ONLY the documents whose content was DIRECTLY used to produce the answer below.

Rules:
- A document contributed if specific facts, numbers, dates, names, or instructions from its excerpt appear in the answer.
- Do NOT include documents that are merely related or tangentially relevant.
- Do NOT include documents about a different country/region than what the answer discusses.
- Typically only 1-3 documents directly contribute to an answer.

ANSWER (may be any language):
{answer}

CANDIDATE DOCUMENTS:
{numbered}

Return ONLY the numbers of documents whose specific content appears in the answer, comma-separated. If unsure about a document, do NOT include it. Return NONE if no document clearly contributed.
RELEVANT NUMBERS:"""

    try:
        model = _get_pv_model()
        raw = model.generate_content(prompt).text.strip()
        if raw.upper() == "NONE":
            return []
        seen = []
        attributed = []
        for part in re.split(r"[,\s]+", raw):
            if part.strip().isdigit():
                idx = int(part.strip())
                for ci, cn, _ in candidates:
                    norm = cn.replace("_", " ").strip()
                    if ci == idx and norm not in seen:
                        attributed.append(cn)
                        seen.append(norm)
        return attributed[:3]
    except Exception as e:
        logger.error(f"Source attribution failed: {e}")
        return []


# ── Country tag patterns (word-boundary safe) ────────────────────────────────
# Use compiled regex patterns with word boundaries to avoid substring
# false positives (e.g. "us" matching "business", "uk" matching "bulk").
_SOURCE_COUNTRY_TAGS = [
    "australia",
    "austria",
    "brazil",
    "canada",
    "china",
    "czech",
    "denmark",
    "france",
    "germany",
    "hong kong",
    "india",
    "indonesia",
    "israel",
    "italy",
    "japan",
    "korea",
    "malaysia",
    "mexico",
    "netherlands",
    "new zealand",
    "philippines",
    "poland",
    "romania",
    "singapore",
    "south africa",
    "spain",
    "sweden",
    "taiwan",
    "thailand",
    "turkey",
    "united kingdom",
    "vietnam",
    "colombia",
    "argentina",
    "chile",
    "peru",
]

# Short tags that need word-boundary matching to avoid false positives
_SHORT_COUNTRY_TAGS = {
    "uk": re.compile(r"\buk\b", re.IGNORECASE),
    "us": re.compile(r"\bus\b", re.IGNORECASE),
    "usa": re.compile(r"\busa\b", re.IGNORECASE),
    "anz": re.compile(r"\banz\b", re.IGNORECASE),
    "cee": re.compile(r"\bcee\b", re.IGNORECASE),
    # Broad region names — critical for filtering non-KB sources with names
    # like "APAC Region Holiday Calendar.pdf" against country-targeted queries.
    "apac": re.compile(r"\bapac\b", re.IGNORECASE),
    "emea": re.compile(r"\bemea\b", re.IGNORECASE),
    "americas": re.compile(r"\bamericas\b", re.IGNORECASE),
}

# ── Country → Broad Region Mapping ──────────────────────────────────────────
# Used to expand target_region (e.g. "Poland") to include its parent broad
# region (e.g. "EMEA") when building the set of "wanted" region tags.
# This ensures that EMEA-scoped sources are kept for a Poland user, while
# APAC-scoped sources are correctly filtered out.
_COUNTRY_BROAD_REGION: dict[str, str] = {
    # APAC
    "india": "apac",
    "japan": "apac",
    "vietnam": "apac",
    "thailand": "apac",
    "singapore": "apac",
    "malaysia": "apac",
    "china": "apac",
    "korea": "apac",
    "australia": "apac",
    "new zealand": "apac",
    "hong kong": "apac",
    "taiwan": "apac",
    "indonesia": "apac",
    # EMEA
    "uk": "emea",
    "united kingdom": "emea",
    "poland": "emea",
    "belgium": "emea",
    "germany": "emea",
    "france": "emea",
    "netherlands": "emea",
    "spain": "emea",
    "italy": "emea",
    "portugal": "emea",
    "israel": "emea",
    "austria": "emea",
    "czech": "emea",
    "czech republic": "emea",
    "sweden": "emea",
    "denmark": "emea",
    "romania": "emea",
    "south africa": "emea",
    "turkey": "emea",
    "russia": "emea",
    "saudi arabia": "emea",
    # Americas
    "us": "americas",
    "usa": "americas",
    "united states": "americas",
    "canada": "americas",
    "brazil": "americas",
    "mexico": "americas",
    "argentina": "americas",
    "colombia": "americas",
    "chile": "americas",
    "peru": "americas",
}

# Sub-region markers (filename tags like "ANZ", "CEE") → countries they cover
_SUB_REGION_COUNTRIES: dict[str, set[str]] = {
    "anz": {"australia", "new zealand"},
    "cee": {"poland", "czech", "czech republic", "austria"},
    "dach": {"germany", "austria"},
    "latam": {"brazil", "mexico", "argentina"},
    "nordics": {"sweden", "denmark"},
}

# ── KB Article → Country Override Map ─────────────────────────────────────────
# ServiceNow KB articles with known country assignments that CANNOT be detected
# from filename or text content alone (generic filenames, no country names in text).
# This is the highest-priority country signal — overrides metadata and text scan.
_KB_COUNTRY_OVERRIDES: dict[str, str] = {
    # Indonesia KBs
    "KB0018923": "indonesia",  # Indonesia leave/absence FAQ (mentions Peoplepay)
    "KB0018926": "indonesia",  # Indonesia general HR FAQ
    "KB0018925": "indonesia",  # Indonesia New Joiner FAQs
    "KB0018922": "indonesia",  # Indonesia working hours
    # UK KBs
    "KB0018733": "uk",  # UK benefits (mentions Darwin)
    "KB0018757": "uk",  # UK holiday FAQ
    "KB0018734": "uk",  # UK holiday FAQ ("holiday entitlement")
    "KB0019772": "uk",  # UK voluntary benefits
    "KB0018339": "uk",  # UK/EMEA leave policy ("in lieu")
    "KB0017452": "uk",  # UK Absence Policy
    # Vietnam KBs
    "KB0018378": "vietnam",  # Vietnam leave policy
    # Malaysia KBs
    "KB0018389": "malaysia",  # Malaysia benefits
    # Spain KBs
    "KB0018330": "spain",  # Spain leave policy
    "KB0017921": "spain",  # Spain remote working policy
    # Israel KBs
    "KB0018410": "israel",  # Israel KB
    # Austria/CEE KBs
    "KB0018405": "austria",  # Austria/CEE KB
    # Thailand KBs
    "KB0018342": "thailand",  # Thailand insurance
    # France KBs
    "KB0018574": "france",  # French policy
    # Irrelevant / not country-specific but wrong for targeted users
    "KB0018614": "_irrelevant",  # Not useful for any specific country query
}

# Regex to extract KB number from filenames like "ServiceNow_KB_KB0018923.html"
# or "ServiceNow KB KB0018733.html"
_KB_NUM_RE = re.compile(r"KB\d{7}")

# ── Country-specific HR system names ──────────────────────────────────────────
# Maps HR system/tool names to the country they belong to.
# Used as a supplementary signal when text mentions a system but not a country.
_SYSTEM_COUNTRY_MAP: dict[str, str] = {
    "peoplepay": "indonesia",  # Indonesia leave/payroll system
    "darwin": "uk",  # UK benefits platform
    "sodexo": "india",  # India meal card benefit
}

# ── Foreign-language filename markers → country ─────────────────────────────
# Catches documents with non-English titles that belong to a specific country.
_FOREIGN_LANG_FN_COUNTRY: dict[str, str] = {
    "regulamin pracy": "poland",
    "regulamin wynagradzania": "poland",
    "regulamin": "poland",
    "kodeks pracy": "poland",
    "règlement intérieur": "france",
    "betriebsvereinbarung": "germany",
    "convenio colectivo": "spain",
}


def _detect_countries_in_text(
    text: str, include_system_names: bool = False
) -> set[str]:
    """Detect country mentions in text using safe word-boundary matching.

    Args:
        text: Text to scan for country mentions.
        include_system_names: If True, also detect country-specific HR system
            names (e.g. Peoplepay → Indonesia). Should be True for SOURCE text
            but False for ANSWER text (answer mentioning a wrong-country system
            name is the problem, not a signal to keep that country's sources).
    """
    if not text:
        return set()
    text_lower = text.lower()
    countries = set()

    # Long country names — safe to use substring matching
    for c in _SOURCE_COUNTRY_TAGS:
        if c in text_lower:
            countries.add(c)

    # Short tags — use regex word boundary
    for tag, pattern in _SHORT_COUNTRY_TAGS.items():
        if pattern.search(text):
            countries.add(tag)

    # Country-specific HR system names (only for source/chunk text)
    if include_system_names:
        for system_name, country in _SYSTEM_COUNTRY_MAP.items():
            if system_name in text_lower:
                countries.add(country)

    # Normalize aliases
    if "uk" in countries or "united kingdom" in countries:
        countries.add("uk")
        countries.add("united kingdom")
    if "us" in countries or "usa" in countries:
        countries.add("us")
        countries.add("usa")

    return countries


def _get_source_country_from_metadata(source_name: str, results: list) -> set[str]:
    """Extract country/region from chunk metadata (Firestore fields) for a given source.

    Priority order:
    0. KB article override map (hardcoded, highest confidence)
    1. Firestore 'country' field (most specific metadata)
    2. Firestore 'region' field (broader metadata)
    3. Fallback: scan chunk text for country mentions + system names
    """
    countries = set()

    # Priority 0: KB article override map — catches ServiceNow KBs with generic
    # filenames and no country metadata.
    kb_match = _KB_NUM_RE.search(source_name)
    if kb_match:
        kb_num = kb_match.group(0)
        override_country = _KB_COUNTRY_OVERRIDES.get(kb_num)
        if override_country:
            if override_country == "_irrelevant":
                # Mark as belonging to a non-existent country so it gets filtered
                # against any target region
                countries.add("_irrelevant")
            else:
                countries.add(override_country)
            logger.debug(
                f"KB override: {source_name} → {override_country} (from {kb_num})"
            )
            return countries

    chunk_text = ""
    for r in results:
        bn = os.path.basename(r.get("source", ""))
        if bn != source_name:
            continue

        # Priority 1: Firestore 'country' field (most specific)
        chunk_country = r.get("country", "")
        if chunk_country and chunk_country.upper() not in ("GLOBAL", "ALL", ""):
            countries.add(chunk_country.lower())

        # Priority 2: Firestore 'region' field (broader)
        chunk_region = r.get("region", "")
        if chunk_region and chunk_region.upper() not in ("GLOBAL", "ALL", ""):
            countries.add(chunk_region.lower())

        # Save chunk text for fallback detection
        if not chunk_text:
            chunk_text = r.get("text", "")

        # Only need first match per source with metadata
        if countries:
            break

    # Fallback: scan chunk text for country mentions if no metadata found
    # Use include_system_names=True because this is SOURCE text, not answer text
    if not countries and chunk_text:
        text_countries = _detect_countries_in_text(
            chunk_text[:1000], include_system_names=True
        )
        if text_countries:
            countries.update(text_countries)

    return countries


def _filter_mismatched_sources(
    answer: str,
    sources: list,
    results: list | None = None,
    target_region: str | None = None,
) -> list:
    """Remove sources whose country doesn't match the answer or the user's region.

    Enhanced version that uses:
    1. Chunk metadata (country/region from Firestore) — catches generic filenames
    2. Filename country tags — catches named files
    3. Answer text country detection — determines what the answer discusses
    4. Target region (user's country) — ultimate arbiter when available
    """
    if not sources:
        return sources

    # Detect which countries the answer actually discusses
    answer_countries = _detect_countries_in_text(answer)

    # If target_region is provided, add it as a known "wanted" country
    wanted_countries = set(answer_countries)
    if target_region:
        tr_lower = target_region.lower()
        wanted_countries.add(tr_lower)

        # Add broad region (APAC/EMEA/Americas) so region-scoped sources
        # matching the user's region are kept.
        broad = _COUNTRY_BROAD_REGION.get(tr_lower)
        if broad:
            wanted_countries.add(broad)

        # Add sub-region markers (ANZ, CEE, etc.) that contain this country
        for marker, marker_countries in _SUB_REGION_COUNTRIES.items():
            if tr_lower in marker_countries:
                wanted_countries.add(marker)

        # Add well-known aliases
        if tr_lower in ("us", "usa", "united states"):
            wanted_countries.update(["us", "usa", "united states"])
        elif tr_lower in ("uk", "united kingdom"):
            wanted_countries.update(["uk", "united kingdom"])

    # If we have no signal about what countries are wanted, keep all
    if not wanted_countries:
        return sources

    filtered = []
    for src in sources:
        src_countries = set()

        # Method 1: Check chunk metadata from retrieval results
        if results:
            meta_countries = _get_source_country_from_metadata(src, results)
            src_countries.update(meta_countries)

        # Method 2: Check filename for country names
        src_norm = src.lower().replace("_", " ").replace("-", " ")
        filename_countries = _detect_countries_in_text(src_norm)
        src_countries.update(filename_countries)

        # Method 2b: Check foreign-language filename markers
        for marker, marker_country in _FOREIGN_LANG_FN_COUNTRY.items():
            if marker in src_norm:
                src_countries.add(marker_country)
                break

        if not src_countries:
            # Source has no country signal — keep it (likely global)
            filtered.append(src)
        elif src_countries & wanted_countries:
            # Source country matches wanted countries — keep it
            filtered.append(src)
        else:
            # Source is about a different country — drop it
            logger.info(
                f"Filtering mismatched source: {src} "
                f"(source_countries={src_countries}, wanted={wanted_countries})"
            )

    # When target_region is explicit and ALL sources are wrong-country,
    # return empty rather than showing misleading links to users.
    # Only fall back to originals when we can't determine countries (no target_region).
    if not filtered and target_region:
        logger.warning(
            f"All {len(sources)} sources filtered as wrong-country for "
            f"target_region={target_region}. Returning empty sources."
        )
        return []

    return filtered if filtered else sources


def extract_unique_sources(
    results: list,
    score_threshold: float = 0.5,
    target_region: str | None = None,
) -> list:
    """Score-based fallback source extraction — only top-scoring documents.
    Deduplicates by source filename and prefers higher-scored results.
    Now also applies country filtering when target_region is provided."""
    if not results:
        return []

    # Deduplicate: keep highest score per source filename
    best_by_source: dict[str, tuple[float, dict]] = {}
    for r in results:
        src = r.get("source")
        if not src or src == "Unknown":
            continue
        bn = os.path.basename(src)
        if is_hv_source(src):
            continue
        score = r.get("rank_score", 0.0)
        dist = r.get("distance", 0.0)
        effective = max(score, dist)
        if effective < score_threshold and dist < 0.3:
            continue
        norm = bn.replace("_", " ").strip()
        if norm not in best_by_source or effective > best_by_source[norm][0]:
            best_by_source[norm] = (effective, r)

    # Sort by score descending, return top 3
    sorted_sources = sorted(best_by_source.items(), key=lambda x: x[1][0], reverse=True)
    top_sources = [name for name, _ in sorted_sources[:3]]

    # Apply country filtering on fallback sources too
    if target_region and top_sources:
        top_results = [v[1] for _, v in sorted_sources[:3]]
        filtered = _filter_mismatched_sources(
            "",  # No answer text for fallback filtering
            top_sources,
            results=top_results,
            target_region=target_region,
        )
        if filtered:
            return filtered

    return top_sources


def _build_source_url_map(results: list) -> dict:
    """Build a mapping of source basename → URL for results.

    Priority:
    1. Use ``servicenow_url`` from Firestore metadata (if present).
    2. Extract the KB number from ``ServiceNow_KB_KB…`` filenames and
       construct a direct link to the Hitachi Vantara ServiceNow portal.
    3. Otherwise, the source gets no entry (orchestrator falls back to
       ``/documents/{filename}``).
    """
    _KB_RE = re.compile(r"ServiceNow_KB_(KB\d+)")
    url_map: dict[str, str] = {}
    for r in results:
        bn = os.path.basename(r.get("source", ""))
        if not bn or bn in url_map:
            continue

        # 1. Explicit ServiceNow URL from Firestore metadata
        sn_url = r.get("servicenow_url")
        if sn_url:
            url_map[bn] = sn_url
            continue

        # 2. Derive URL from KB number in the filename
        m = _KB_RE.search(bn)
        if m:
            kb_number = m.group(1)
            url_map[bn] = (
                f"{SERVICENOW_PORTAL_URL}"
                f"?id=kb_article_view&sysparm_article={kb_number}"
            )

    return url_map


# ── Greeting / chitchat detection ────────────────────────────────────────────
_CHITCHAT_PATTERNS = [
    r"^(thanks|thank you|ok|okay|got it|great|nice|cool|good|perfect|awesome)\W*$",
    r"^you'?re welcome\W*$",
    r"^(hello|hi|hey|good morning|good afternoon|good evening)\W*$",
    r"^(bye|goodbye|see you|take care)\W*$",
]
_CHITCHAT_RE = re.compile("|".join(_CHITCHAT_PATTERNS), re.IGNORECASE)


def _is_chitchat_answer(answer: str) -> bool:
    """Detect if the answer is a simple chitchat/acknowledgment response."""
    # Very short responses to greetings/thanks don't need sources
    if len(answer) < 100:
        stripped = answer.strip().rstrip("!.?")
        if _CHITCHAT_RE.match(stripped):
            return True
    # Check for common short acknowledgment patterns
    lower = answer.lower()
    short_acks = [
        "you're welcome",
        "how can i assist you",
        "how can i help you",
        "is there anything else",
        "glad i could help",
        "happy to help",
    ]
    if len(answer) < 200 and any(ack in lower for ack in short_acks):
        return True
    return False


def validate_and_attribute(
    answer: str,
    results: list,
    target_region: str | None = None,
) -> dict:
    """
    Full post-validation: attribute sources, check for no-info fallback.

    Args:
        answer: The generated answer text
        results: Retrieved chunks with metadata (source, country, region, etc.)
        target_region: User's country/region for cross-country filtering

    Returns: {final_sources: [...], is_no_info: bool, source_urls: {name: url}}
    """
    _lower = answer.lower()

    # ── Stage 0: detect chitchat/greeting responses ──────────────────────
    # "Thanks!" → "You're welcome!" should never have sources
    if _is_chitchat_answer(answer):
        return {"final_sources": [], "is_no_info": False, "source_urls": {}}

    # ── Stage 1: detect explicit fallback / no-info answers ──────────────
    FALLBACK_PHRASES = [
        "i don't have information",
        "i don't have the answer",
        "i don't have access to your",
        "i do not have access",
        "i cannot provide you with",
        "i cannot access your",
        "i'm unable to provide",
        "i am unable to provide",
        "no relevant context found",
        "không có thông tin",
        "情報がありません",
        "no information about that",
        "not found in the available documents",
        "missing context",
        "no relevant context found for your region",
        "no clear answer available",
        "corporatecard@hitachidigital.com",
        "i cannot assist with",
        "i am sorry, but i cannot",
        "my expertise is in hr",
    ]
    is_no_info = any(p in _lower for p in FALLBACK_PHRASES)

    if is_no_info or not results:
        return {"final_sources": [], "is_no_info": is_no_info, "source_urls": {}}

    # ── Stage 2: detect short redirect-only answers ──────────────────────
    # If the answer is very short AND only contains a redirect phrase without
    # substantive guidance, it's not actually using any KB content — skip
    # attribution entirely.  Raise the threshold and exclude answers that
    # contain actionable instructions (e.g., "raise a ticket with approval
    # attached") — those ARE valid answers even though they mention AskNow.
    _REDIRECT_SIGNALS = [
        "i don't have",
        "i do not have",
        "i cannot provide",
        "i cannot access",
        "i'm unable to",
        "i am unable to",
    ]
    _has_redirect = any(sig in _lower for sig in _REDIRECT_SIGNALS)
    # Very short answer that is purely a "can't help" redirect
    if _has_redirect and len(answer) < 200:
        return {"final_sources": [], "is_no_info": True, "source_urls": {}}

    # Build source→URL mapping from all results (before filtering to final sources)
    source_urls = _build_source_url_map(results)

    llm_sources = attribute_sources(answer, results)
    if llm_sources:
        # Post-filter: remove sources about a different country than the answer
        # Now uses chunk metadata AND target_region for more accurate filtering
        llm_sources = _filter_mismatched_sources(
            answer,
            llm_sources,
            results=results,
            target_region=target_region,
        )
        if llm_sources:
            return {
                "final_sources": llm_sources,
                "is_no_info": False,
                "source_urls": source_urls,
            }

    # LLM attribution returned nothing (or was filtered to nothing).
    # If the answer still contains redirect language, don't show random docs.
    if _has_redirect:
        return {
            "final_sources": [],
            "is_no_info": False,
            "source_urls": source_urls,
        }

    fallback_sources = extract_unique_sources(
        results,
        target_region=target_region,
    )
    # Apply country filtering on fallback sources too
    if fallback_sources and target_region:
        fallback_sources = _filter_mismatched_sources(
            answer,
            fallback_sources,
            results=results,
            target_region=target_region,
        )
    return {
        "final_sources": fallback_sources,
        "is_no_info": False,
        "source_urls": source_urls,
    }


post_validation_agent = Agent(
    name="post_validation_agent",
    model="gemini-2.0-flash",
    description="Validates generated answers, attributes sources, and detects no-info fallbacks.",
    instruction="""You are the Post-Validation Agent.
Use validate_and_attribute to check the answer quality and identify contributing source documents.
Return the final sources list and whether the answer is a no-info fallback.""",
    tools=[validate_and_attribute],
)
