"""
reranking_agent.py
===================
Re-ranks and filters retrieved results based on geographic context,
OPCO entity, holiday/p-card priorities, region relevance, and
variant-specific index prioritization.
"""

import os
import re
from typing import Set
from google.adk.agents import Agent
from config import INDEX_PRIORITY_BOOST
from tools.opco_tools import (
    filter_hv_results,
    is_holiday_query,
    is_holiday_file,
    is_holiday_result,
    is_p_card_query,
    prioritize_holiday_results,
    prioritize_pcard_results,
    get_opco_entity,
    build_opco_context_note,
)
import logging

logger = logging.getLogger("HD_SKYE_AGENT")

# ── Regex to identify ServiceNow KB article sources ─────────────────────────
_KB_SOURCE_RE = re.compile(r"ServiceNow[_ ]KB[_ ]KB\d+", re.I)

# Collections that are expected to hold non-KB (raw policy PDF) sources.
# Results from these collections should NOT be penalised by KB-preference logic.
_NON_KB_COLLECTIONS = {
    "apac-payroll-chunks",
    "p-card_policy",
    "bulk-expense",
}


def _is_kb_source(result: dict) -> bool:
    """Return True if the retrieval result comes from a ServiceNow KB article.

    A result is considered a KB source if:
    1. It has a ``servicenow_url`` or ``servicenow_number`` field, OR
    2. Its filename matches the ``ServiceNow_KB_*`` naming pattern, OR
    3. Its ``collection`` is the ServiceNow KB collection.
    """
    if result.get("servicenow_url") or result.get("servicenow_number"):
        return True
    source = result.get("source", "")
    if _KB_SOURCE_RE.search(os.path.basename(source)):
        return True
    coll = result.get("collection", "")
    if "servicenow" in coll.lower():
        return True
    return False


REGION_GROUPS = {
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
    "czech republic": "emea",
    "czech": "emea",
    "sweden": "emea",
    "denmark": "emea",
    "russia": "emea",
    "saudi arabia": "emea",
    "south africa": "emea",
    "uae": "emea",
    "finland": "emea",
    "norway": "emea",
    "indonesia": "apac",
    "philippines": "apac",
    "usa": "americas",
    "united states": "americas",
    "us": "americas",
    "america": "americas",
    "canada": "americas",
    "brazil": "americas",
    "mexico": "americas",
    "argentina": "americas",
}

# Mapping from Firestore ``region`` field values (APAC/EMEA/Americas) to
# the country-level regions they encompass, so we can match a user's country
# against the chunk's broad region tag.
BROAD_REGION_COUNTRIES = {
    "apac": {
        "india",
        "japan",
        "vietnam",
        "thailand",
        "singapore",
        "malaysia",
        "china",
        "korea",
        "australia",
        "new zealand",
        "hong kong",
        "taiwan",
        "indonesia",
    },
    "emea": {
        "uk",
        "united kingdom",
        "poland",
        "belgium",
        "germany",
        "france",
        "netherlands",
        "spain",
        "italy",
        "russia",
        "portugal",
        "south_africa",
        "saudi arabia",
        "israel",
        "austria",
        "czech republic",
        "sweden",
        "denmark",
        "uae",
        "finland",
        "norway",
    },
    "americas": {
        "usa",
        "us",
        "united states",
        "america",
        "canada",
        "brazil",
        "mexico",
        "argentina",
    },
}

KNOWN_REGIONS = [
    "india",
    "japan",
    "vietnam",
    "thailand",
    "singapore",
    "malaysia",
    "china",
    "korea",
    "australia",
    "new zealand",
    "hong kong",
    "taiwan",
    "uk",
    "united kingdom",
    "poland",
    "belgium",
    "germany",
    "france",
    "netherlands",
    "spain",
    "italy",
    "russia",
    "usa",
    "america",
    "united states",
    "us",
    "portugal",
    "apac",
    "emea",
    "latam",
    "nordics",
    "saudi arabia",
    "south_africa",
    "indonesia",
    "israel",
    "austria",
    "czech republic",
    "czech",
    "sweden",
    "denmark",
    "canada",
    "brazil",
    "mexico",
    "argentina",
    # ── Added for old-schema filename detection (UAT Round 2) ──
    "uae",
    "finland",
    "norway",
    "philippines",
    "colombia",
    "chile",
    "peru",
    "americas",
]

# Additional filename-only region markers that don't appear in country names
# but are used in filenames (e.g. "Payroll FAQs List - ANZ.docx")
_FN_REGION_MARKERS = {
    "anz": {"australia", "new zealand"},
    "cee": {"poland", "czech republic", "austria"},
    "dach": {"germany", "austria"},
    "latam": {"brazil", "mexico", "argentina"},
    "nordics": {"sweden", "denmark"},
    # ── Short abbreviations that appear in old-schema filenames ──
    "hk ": {"hong kong"},  # "HK Various Leave Program.pdf"
    "nz ": {"new zealand"},  # "NZ Parental Leave Policy.pdf"
    "u.s.": {"usa", "united states"},  # "U.S. New Joiner FAQs.pdf"
    "hds ": {"thailand"},  # "HDS Thailand" prefix in payroll docs
    # ── Topic-based markers unique to specific countries ──
    "state income tax": {"usa", "united states"},  # US-only concept
    "401k": {"usa", "united states"},  # US retirement plan
    "401(k)": {"usa", "united states"},
}

# ─── Text-based country detection for chunks with no metadata ────────────────
# Used to catch ServiceNow KB articles that have generic filenames and no
# country/region fields but clearly reference a specific country in their text.
_TEXT_COUNTRY_PATTERNS = [
    ("india", re.compile(r"\bindia(?:n)?\b", re.I)),
    ("japan", re.compile(r"\bjapan(?:ese)?\b", re.I)),
    ("vietnam", re.compile(r"\bvietnam(?:ese)?\b", re.I)),
    ("thailand", re.compile(r"\bthai(?:land)?\b", re.I)),
    ("singapore", re.compile(r"\bsingapore(?:an)?\b", re.I)),
    ("malaysia", re.compile(r"\bmalaysia(?:n)?\b", re.I)),
    ("indonesia", re.compile(r"\bindonesia(?:n)?\b", re.I)),
    ("poland", re.compile(r"\bpol(?:and|ish)\b", re.I)),
    ("germany", re.compile(r"\bgerman(?:y)?\b", re.I)),
    ("france", re.compile(r"\bfr(?:ance|ench)\b", re.I)),
    ("spain", re.compile(r"\bspain\b|\bspanish\b", re.I)),
    ("italy", re.compile(r"\bital(?:y|ian)\b", re.I)),
    ("portugal", re.compile(r"\bportug(?:al|uese)\b", re.I)),
    ("israel", re.compile(r"\bisrael(?:i)?\b", re.I)),
    ("austria", re.compile(r"\baustria(?:n)?\b", re.I)),
    ("brazil", re.compile(r"\bbrazil(?:ian)?\b", re.I)),
    ("mexico", re.compile(r"\bmexico\b|\bmexican\b", re.I)),
    ("canada", re.compile(r"\bcanad(?:a|ian)\b", re.I)),
    ("argentina", re.compile(r"\bargentin(?:a|e|ian)\b", re.I)),
    ("belgium", re.compile(r"\bbelgi(?:um|an)\b", re.I)),
    ("netherlands", re.compile(r"\bnetherlands\b|\bdutch\b", re.I)),
    ("united kingdom", re.compile(r"\bunited\s+kingdom\b", re.I)),
    ("united states", re.compile(r"\bunited\s+states\b", re.I)),
    ("russia", re.compile(r"\brussia(?:n)?\b", re.I)),
    ("korea", re.compile(r"\bkorea(?:n)?\b", re.I)),
    ("australia", re.compile(r"\baustrali(?:a|an)\b", re.I)),
    ("new zealand", re.compile(r"\bnew\s+zealand\b", re.I)),
    ("sweden", re.compile(r"\bswed(?:en|ish)\b", re.I)),
    ("denmark", re.compile(r"\bdenmark\b|\bdanish\b", re.I)),
    ("czech republic", re.compile(r"\bczech\b", re.I)),
    ("saudi arabia", re.compile(r"\bsaudi\b", re.I)),
    ("uae", re.compile(r"\bUAE\b|\bUnited\s+Arab\s+Emirates\b", re.I)),
    ("finland", re.compile(r"\bfinland\b|\bfinnish\b", re.I)),
    ("norway", re.compile(r"\bnorway\b|\bnorwegian\b", re.I)),
]

# ─── Non-English filename markers ────────────────────────────────────────────
# Polish/foreign-language document titles that identify country without English
# keywords.  Matched against filenames (case-insensitive).
_FOREIGN_LANG_FN_MARKERS: dict[str, str] = {
    "regulamin pracy": "poland",  # "Work Regulations"
    "regulamin wynagradzania": "poland",  # "Remuneration Regulations"
    "regulamin": "poland",  # Generic Polish regulation document
    "kodeks pracy": "poland",  # Polish Labour Code
    "règlement intérieur": "france",  # French internal rules
    "betriebsvereinbarung": "germany",  # German works agreement
    "convenio colectivo": "spain",  # Spanish collective agreement
}


def _detect_chunk_text_country(text: str, target_variants: list) -> str | None:
    """Detect if a chunk's text is about a specific country OTHER than the target.

    Scans the text for unambiguous country-name mentions.  If the target
    country is found the chunk is considered relevant and ``None`` is returned.
    If only non-target countries are found, the most-mentioned one is returned
    so the caller can reclassify the chunk as 'other'.
    """
    if not text:
        return None
    other_counts: dict[str, int] = {}
    for country, pattern in _TEXT_COUNTRY_PATTERNS:
        matches = pattern.findall(text)
        if not matches:
            continue
        # Check if this country matches the target region
        if country in target_variants or any(v in country for v in target_variants):
            return None  # Target country found → chunk is relevant
        other_counts[country] = len(matches)
    if other_counts:
        return max(other_counts, key=other_counts.get)
    return None


def _get_region_variants(target_region: str, broad_region: str = None) -> list:
    """Expand target_region to include all name variants and the parent broad region.

    Args:
        target_region: Country or region name (e.g. "India", "APAC").
        broad_region: Optional BQ-sourced EMPLOYING_REGION (APAC/EMEA/Americas).
            When provided, used as the primary broad-region mapping instead of
            the static REGION_GROUPS dict.  The dict is kept as fallback.
    """
    variants = [target_region.lower()]
    # Primary: use BQ-sourced broad_region if provided
    # Fallback: use the static REGION_GROUPS dict
    group = None
    if broad_region:
        group = broad_region.lower()
    else:
        group = REGION_GROUPS.get(target_region.lower())
    if group and group != target_region.lower():
        variants.append(group)
    iso_map = {
        "in": "india",
        "us": "usa",
        "gb": "uk",
        "pl": "poland",
        "pt": "portugal",
        "jp": "japan",
        "vn": "vietnam",
        "be": "belgium",
        "de": "germany",
        "cn": "china",
        "sg": "singapore",
        "au": "australia",
        "aus": "australia",
    }
    reverse_map = {v: k for k, v in iso_map.items()}
    reverse_map.update({"united states": "us", "united kingdom": "gb", "america": "us"})
    tl = target_region.lower()
    if tl in iso_map:
        variants.append(iso_map[tl])
    elif tl in reverse_map:
        variants.append(reverse_map[tl])
    if any(v in variants for v in ["united states", "usa", "us"]):
        for v in ["us", "usa", "united states", "america"]:
            if v not in variants:
                variants.append(v)
    if any(v in variants for v in ["united kingdom", "uk", "gb"]):
        for v in ["uk", "gb", "united kingdom"]:
            if v not in variants:
                variants.append(v)
    return variants


def _country_word_match(needle: str, haystack: str) -> bool:
    """Check if *needle* appears in *haystack* as a whole word (not substring).

    Prevents false positives like ``"in" in "vietnam"`` or ``"us" in "australia"``.
    Short ISO codes (2–3 chars) require word-boundary checks; longer names
    use simple equality against the full haystack value.
    """
    if not needle or not haystack:
        return False
    # Exact equality — covers 90% of cases (country field = "india", variant = "india")
    if needle == haystack:
        return True
    # For multi-word names, check if needle is contained as a full word
    # e.g. "united states" in "united states of america" — but the Firestore
    # country field is typically a single canonical name, so equality is usually
    # sufficient.  Use word-boundary regex for safety.
    if len(needle) >= 4:
        # Long enough that substring collision is rare; still use word boundary
        pat = re.compile(rf"(?<![a-zA-Z]){re.escape(needle)}(?![a-zA-Z])", re.I)
        return bool(pat.search(haystack))
    # Short codes (2-3 chars like "in", "us", "uk", "jp") — ONLY match if the
    # entire haystack equals the code.  These codes should never match as a
    # substring of a country name.
    return False


def _country_matches_variants(chunk_country: str, region_variants: list) -> bool:
    """Check if a chunk's country matches any of the target region variants.

    Uses word-boundary matching to prevent substring false positives.
    """
    if not chunk_country:
        return False
    for v in region_variants:
        if _country_word_match(v, chunk_country):
            return True
    return False


def rerank_and_filter(
    results: list,
    target_region: str,
    search_query: str = "",
    history_text_en: str = "",
    is_followup: bool = False,
    priority_collections: Set[str] = None,
    priority_categories: Set[str] = None,
    apply_region_filter: bool = True,
    broad_region: str = None,
) -> dict:
    """
    Full post-retrieval pipeline: HV filter → index priority boost → holiday/pcard
    boost → region filter → sort.

    Args:
        results: Raw retrieval results (each dict has 'collection', 'rank_score', etc.)
        target_region: The user's target region for filtering.
        search_query: The English search query text.
        history_text_en: English conversation history (for follow-up detection).
        is_followup: Whether this is a follow-up question.
        priority_collections: Set of Firestore collection names whose results
            should receive a score boost.  Passed by the orchestrator based on
            the active agent variant's ``priority_index_groups``.  When None
            or empty, no boost is applied.
        priority_categories: Set of ``category`` field values (e.g. "P Card",
            "Bulk Expense") whose results should receive a score boost.
            Allows finer-grained variant boosting — e.g. a pcard variant can
            boost ``category="P Card"`` results from the general servicenow_kb
            collection.
        apply_region_filter: Whether to apply region-based boost/penalty.
            Controlled by ``REGION_FILTER_MODE`` in config.  When False, all
            results are ranked purely by semantic relevance + variant boosts
            (no region boost, no other-region exclusion).
        broad_region: Optional BQ-sourced EMPLOYING_REGION (APAC/EMEA/Americas).
            When provided, used as primary broad-region mapping for the target
            instead of the static REGION_GROUPS dict.

    Returns: {filtered_results: [...], opco_context_note: str, context_text: str}
    """
    if not results:
        return {
            "filtered_results": [],
            "opco_context_note": "",
            "context_text": "No relevant context found.",
        }

    # 1. HV filter
    results = filter_hv_results(results)

    # 1.5  Index priority boost — elevate results from the variant's
    #       specialty indexes so they rank above generic ServiceNow results
    #       of similar semantic relevance.
    if priority_collections:
        boosted_count = 0
        for r in results:
            if r.get("collection") in priority_collections:
                r["rank_score"] = r.get("rank_score", 0.0) + INDEX_PRIORITY_BOOST
                boosted_count += 1
        if boosted_count:
            logger.info(
                f"[reranking] INDEX_PRIORITY_BOOST: +{INDEX_PRIORITY_BOOST} applied "
                f"to {boosted_count}/{len(results)} results from {priority_collections}"
            )

    # 1.6  Category boost — finer-grained variant boosting using the
    #       ``category`` field from Firestore metadata.  This catches
    #       relevant results that live in shared collections (e.g. a P-Card
    #       KB article in the servicenow_kb collection with category=
    #       "P Card") that wouldn't be caught by collection-only boost.
    if priority_categories:
        cat_boosted = 0
        for r in results:
            chunk_cat = r.get("category", "")
            if chunk_cat and chunk_cat in priority_categories:
                # Only boost if not already boosted by collection priority
                if (
                    not priority_collections
                    or r.get("collection") not in priority_collections
                ):
                    r["rank_score"] = r.get("rank_score", 0.0) + INDEX_PRIORITY_BOOST
                    cat_boosted += 1
        if cat_boosted:
            logger.info(
                f"[reranking] CATEGORY_BOOST: +{INDEX_PRIORITY_BOOST} applied "
                f"to {cat_boosted}/{len(results)} results with categories={priority_categories}"
            )

    # 2. Holiday / PCard priority
    is_holiday_ctx = is_holiday_query(search_query) or (
        is_followup and is_holiday_query(history_text_en)
    )
    if is_holiday_ctx:
        results = prioritize_holiday_results(results)

    # 3. Region filtering — only applied when apply_region_filter is True.
    #    Controlled by REGION_FILTER_MODE env var (see config.py).
    #    When disabled, all results are kept and sorted purely by rank_score.
    if not apply_region_filter:
        logger.info(
            "[reranking] Region filter DISABLED — skipping region boost/penalty"
        )
        # No region classification — just sort by score and take top results
        results.sort(
            key=lambda x: x.get("rank_score", x.get("distance", 0.0)), reverse=True
        )
        if is_p_card_query(search_query):
            results = prioritize_pcard_results(results)

        # Holiday isolation still applies (it's content-based, not region-based)
        if is_holiday_ctx and target_region.lower() != "global":
            dedicated = [
                r
                for r in results
                if is_holiday_result(r) and "faq" not in r.get("source", "").lower()
            ]
            if dedicated:
                # Same KB preference as the region-enabled path
                kb_dedicated = [
                    r
                    for r in dedicated
                    if _is_kb_source(r) or r.get("collection") in _NON_KB_COLLECTIONS
                ]
                if kb_dedicated:
                    filtered = kb_dedicated
                    logger.info(
                        f"[reranking] Holiday KB preference (no-region): kept "
                        f"{len(kb_dedicated)} KB, dropped "
                        f"{len(dedicated) - len(kb_dedicated)} non-KB"
                    )
                else:
                    filtered = dedicated
            else:
                filtered = results[:15]
        else:
            filtered = results[:15]

        opco_note = build_opco_context_note(filtered)
        ctx = (
            "\n\n".join(
                [
                    f"SOURCE: {r['source']}"
                    + (
                        f"\nCOUNTRY: {r.get('country', 'N/A')}"
                        if r.get("country")
                        else ""
                    )
                    + f"\nCONTENT: {r['text']}"
                    for r in filtered
                ]
            )
            if filtered
            else "No relevant context found."
        )
        return {
            "filtered_results": filtered,
            "opco_context_note": opco_note,
            "context_text": ctx,
        }

    # ── Region filter is ENABLED — apply boost + other-region penalty ────
    region_variants = _get_region_variants(target_region, broad_region=broad_region)
    variant_patterns = []
    for v in region_variants:
        v_esc = re.escape(v).replace(r"\ ", r"[\s_-]")
        if v == "in":
            variant_patterns.append((re.compile(r"(?<![a-zA-Z])IN(?![a-zA-Z])"), False))
            variant_patterns.append(
                (re.compile(r"(?<![a-zA-Z])india(?![a-zA-Z])", re.I), True)
            )
        elif v == "us":
            variant_patterns.append(
                (re.compile(r"(?<![a-zA-Z])us(?![a-zA-Z])", re.I), True)
            )
        else:
            variant_patterns.append(
                (re.compile(rf"(?<![a-zA-Z]){v_esc}(?![a-zA-Z])", re.I), True)
            )

    regional = []
    global_matches = []
    topic_matches = []  # Results that are neither regional nor global but may be topically relevant
    for res in results:
        source = res["source"]
        fn = os.path.basename(source)
        fn_lower = fn.lower()
        opco = get_opco_entity(source)

        # ── Region detection priority ──────────────────────────────
        # 1. Firestore ``region`` field (APAC/EMEA/Americas) — most reliable
        # 2. Firestore ``country`` field (e.g. "India")
        # 3. Filename regex (legacy fallback)
        # 4. Chunk text content (last resort)

        is_regional = False

        # 1. Check the ``region`` field from Firestore (new-schema docs)
        chunk_region = (res.get("region") or "").lower()
        chunk_country_raw = (res.get("country") or "").lower()
        if chunk_region:
            # Direct match: user target is a broad region (e.g. "apac")
            if chunk_region in region_variants:
                # IMPORTANT: If the chunk has a SPECIFIC country that doesn't
                # match the target, the broad-region match alone should NOT
                # classify it as "regional".  E.g. target=Poland, chunk has
                # region=EMEA + country=United Kingdom → not regional for Poland.
                if chunk_country_raw and chunk_country_raw not in (
                    "global",
                    "",
                    target_region.lower(),
                ):
                    # Check if the country actually matches the target.
                    # IMPORTANT: use word-boundary or equality checks, NOT
                    # substring ``in`` — "in" (India ISO) is a substring of
                    # "vietnam", "indonesia", "china", "singapore" etc.
                    country_matches_target = _country_matches_variants(
                        chunk_country_raw, region_variants
                    )
                    if not country_matches_target:
                        # Different specific country in same broad region —
                        # don't mark as regional from region field alone.
                        is_regional = False
                    else:
                        is_regional = True
                else:
                    # Country is GLOBAL/empty/matches target — region match is valid
                    is_regional = True

        # 2. Check filename for region keywords
        if not is_regional:
            is_regional = any(
                p.search(fn_lower if ul else fn) for p, ul in variant_patterns
            )

        # 3. Check the Firestore "country" field (e.g. ServiceNow chunks
        # may have generic filenames like "ServiceNow_KB_KB00XXXXX.html"
        # but a country field set to "India")
        if not is_regional:
            chunk_country = res.get("country", "").lower()
            if chunk_country and chunk_country != "global":
                is_regional = _country_matches_variants(chunk_country, region_variants)

        # 4. Last resort: check the chunk TEXT for region keywords.
        # ServiceNow KB holiday calendars have country=GLOBAL but contain
        # region-specific tables (e.g. "📅United States") inside the text.
        if not is_regional:
            chunk_text_lower = res.get("text", "").lower()
            if chunk_text_lower:
                is_regional = any(
                    p.search(chunk_text_lower if ul else res.get("text", ""))
                    for p, ul in variant_patterns
                )

        # 4b. Foreign-language filename detection — e.g. "Regulamin Pracy" → Poland
        _foreign_country = None
        for marker, country in _FOREIGN_LANG_FN_MARKERS.items():
            if marker in fn_lower:
                _foreign_country = country
                if country in region_variants:
                    is_regional = True
                break

        # ── Detect results belonging to OTHER regions ──────────────
        is_other = False
        if target_region.lower() != "global":
            # Check if chunk's ``region`` field tags it to a different broad region
            if chunk_region and chunk_region not in region_variants:
                is_other = True

            # Check foreign-language filename marker (highest confidence)
            if (
                not is_other
                and _foreign_country
                and _foreign_country not in region_variants
            ):
                is_other = True

            if not is_other:
                is_other_fn = any(
                    r in fn_lower
                    for r in KNOWN_REGIONS
                    if r not in region_variants and r != target_region.lower()
                )
                # Also check filename-only region markers (ANZ, CEE, etc.)
                if not is_other_fn:
                    for marker, marker_countries in _FN_REGION_MARKERS.items():
                        if marker in fn_lower:
                            # If target is in this marker's countries, it's regional
                            if not any(
                                tc in region_variants for tc in marker_countries
                            ):
                                is_other_fn = True
                                break
                is_other_country = False
                chunk_country = res.get("country", "").lower()
                if chunk_country and chunk_country != "global":
                    # Use word-boundary matching to avoid substring false
                    # positives (e.g. "us" matching "a-us-tralia").
                    is_other_country = any(
                        _country_word_match(r, chunk_country)
                        for r in KNOWN_REGIONS
                        if r not in region_variants and r != target_region.lower()
                    )
                is_other = is_other_fn or is_other_country

        is_global = (
            "global" in opco.lower()
            or "global" in fn_lower
            or res.get("country", "").upper() == "GLOBAL"
            # New-schema: null region means GLOBAL
            or (not chunk_region and not res.get("country"))
        ) and not is_other

        # 5. Text-based other-country detection: catch documents that have
        #    no metadata (classified as "global") but clearly reference a
        #    different specific country in their text content.
        if is_global and not is_regional and target_region.lower() != "global":
            other_country = _detect_chunk_text_country(
                res.get("text", ""), region_variants
            )
            if other_country:
                is_other = True
                is_global = False
                logger.debug(
                    f"[reranking] Text-based reclassification: "
                    f"{res.get('source', '?')} → other ({other_country})"
                )

        if is_regional and not is_other:
            regional.append(res)
        elif is_global:
            global_matches.append(res)
        elif not is_other and res.get("rank_score", 0) > 0:
            # Keep topically relevant results that aren't tagged to another region
            topic_matches.append(res)

    # ── Country-specific → Global → Fallback cascade ─────────────────
    # When targeting a specific country, country-specific results take
    # absolute priority.  Global results fill remaining slots.  Topic
    # matches are a last resort when results are very sparse.
    is_holiday = is_holiday_query(search_query) or any(
        "holiday" in v for v in region_variants
    )
    BOOST = 0.20
    COUNTRY_EXACT_BOOST = 0.15  # Extra boost for exact country match on top of BOOST
    target_lower = target_region.lower()
    for r in regional:
        r["rank_score"] = r.get("rank_score", 0.0) + BOOST
        # Extra boost when the chunk's country metadata exactly matches the target
        chunk_c = (r.get("country") or "").lower()
        if chunk_c and _country_matches_variants(chunk_c, [target_lower]):
            r["rank_score"] += COUNTRY_EXACT_BOOST
    regional.sort(
        key=lambda x: x.get("rank_score", x.get("distance", 0.0)), reverse=True
    )
    global_matches.sort(
        key=lambda x: x.get("rank_score", x.get("distance", 0.0)), reverse=True
    )

    if target_region.lower() != "global" and regional:
        # Cascade: country-specific first, then global supplement
        max_results = 15
        filtered = regional[:max_results]
        remaining = max_results - len(filtered)
        if remaining > 0:
            filtered.extend(global_matches[:remaining])
        if len(filtered) < 3 and topic_matches:
            topic_matches.sort(
                key=lambda x: x.get("rank_score", x.get("distance", 0.0)),
                reverse=True,
            )
            remaining = max_results - len(filtered)
            filtered.extend(topic_matches[:remaining])
        logger.info(
            f"[reranking] Cascade: {len(regional)} country-specific + "
            f"{max(0, len(filtered) - len(regional[:max_results]))} global "
            f"(target={target_region})"
        )
    else:
        # No specific country or no regional results — combine all
        combined = regional + global_matches
        if len(combined) < 5 and topic_matches:
            combined.extend(topic_matches)
        combined.sort(
            key=lambda x: x.get("rank_score", x.get("distance", 0.0)), reverse=True
        )
        filtered = combined

    if is_p_card_query(search_query):
        filtered = prioritize_pcard_results(filtered)

    # Holiday isolation — keep ALL chunks from holiday files to preserve full calendar
    if is_holiday_ctx and target_region.lower() != "global":
        dedicated = [
            r
            for r in filtered
            if is_holiday_result(r) and "faq" not in r.get("source", "").lower()
        ]
        if dedicated:
            # Prefer ServiceNow KB results over old-schema non-KB sources.
            # Only APAC payroll, P-Card, and bulk expense should have non-KB
            # holiday sources; other non-KB holiday PDFs (e.g. "APAC Region
            # Holiday Calendar.pdf" from the old main index) are legacy data
            # that should be superseded by KB articles.
            kb_dedicated = [
                r
                for r in dedicated
                if _is_kb_source(r) or r.get("collection") in _NON_KB_COLLECTIONS
            ]
            if kb_dedicated:
                filtered = kb_dedicated
                logger.info(
                    f"[reranking] Holiday KB preference: kept {len(kb_dedicated)} KB "
                    f"results, dropped {len(dedicated) - len(kb_dedicated)} non-KB results"
                )
            else:
                filtered = dedicated
        else:
            filtered = filtered[:15]
    else:
        filtered = filtered[:15]

    opco_note = build_opco_context_note(filtered)
    ctx = (
        "\n\n".join(
            [
                f"SOURCE: {r['source']}"
                + (f"\nCOUNTRY: {r.get('country', 'N/A')}" if r.get("country") else "")
                + f"\nCONTENT: {r['text']}"
                for r in filtered
            ]
        )
        if filtered
        else "No relevant context found."
    )

    return {
        "filtered_results": filtered,
        "opco_context_note": opco_note,
        "context_text": ctx,
    }


reranking_agent = Agent(
    name="reranking_agent",
    model="gemini-2.0-flash",
    description="Re-ranks and filters retrieval results by region, OPCO, holiday/pcard priority.",
    instruction="""You are the Reranking Agent.
Use rerank_and_filter to post-process retrieved results.
Return the filtered results, OPCO context note, and context text.""",
    tools=[rerank_and_filter],
)
