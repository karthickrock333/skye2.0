# Bugs Found and Fixes Applied

## Bug 1 (CRITICAL): Access Control - home_loc Override Ignores Explicit Region

**File**: `agents/orchestrator.py` (lines 389-393)

**Symptom**: Asking "what are the holidays in USA?" with `data_scope="regional"` returns:
> "You do not have permission to access HR policies for India. You have access to: Global."

**Root Cause**: A two-part failure chain:

1. The LLM in `_combined_analysis()` is called with `home_location="Global"` (hardcoded at orchestrator.py:360 because the real home_loc hasn't resolved yet from BQ). Without the real home_location context, the LLM sometimes returns `target_region: "Global"` even when the user explicitly mentions "USA".

2. In orchestrator.py:389-393, there's an override that says: if `target_region == "Global"` AND `home_loc != "Global"` AND (holiday query OR detailed intent), replace `target_region` with `home_loc`. Since the user is based in India (per BQ), `home_loc = "India"`, and `target_region` gets wrongly set to "India".

3. The access check then runs with `target_region="India"`, but the user only has access to Global — so it denies with the India message.

**Fix**: Added a `_query_mentions_specific_region()` check that scans the English query for country names/abbreviations. The override only fires when the user didn't mention any specific region. This way, "holidays in USA" keeps `target_region` as-is (or the LLM's extraction), and only truly ambiguous queries like "what are the holidays?" fall back to home_loc.

```python
# Before (broken):
if target_region.lower() == "global" and home_loc.lower() != "global":
    if _is_holiday_q(query_en) or intent == "detailed":
        target_region = home_loc  # WRONG: replaces USA with India

# After (fixed):
if target_region.lower() == "global" and home_loc.lower() != "global":
    if _is_holiday_q(query_en) or intent == "detailed":
        if not _query_mentions_specific_region(query_en):
            target_region = home_loc  # Only for truly ambiguous queries
```

---

## Bug 2 (HIGH): Broken Retry Loop in `_combined_analysis()`

**File**: `agents/query_understanding_agent.py` (lines 237-261)

**Symptom**: LLM call never retries on failure, increasing the chance of returning fallback defaults (`target_region: "Global"`) — which then triggers Bug 1.

**Root Cause**: The fallback `return` statement was indented inside the `for` loop but outside the `try/except`. After the first failed attempt, the code falls through to this return immediately without ever executing the second attempt.

```python
# Before (broken):
for attempt in range(2):
    try:
        ...
        return { ... }       # success path
    except Exception as e:
        ...
        if attempt == 0:
            _time.sleep(0.5)
    return { ... }            # BUG: inside for loop, runs after first failure

# After (fixed):
for attempt in range(2):
    try:
        ...
        return { ... }
    except Exception as e:
        ...
        if attempt == 0:
            _time.sleep(0.5)

# All retries exhausted — return safe defaults
return { ... }                # Correctly outside the for loop
```

---

## Bug 3 (MEDIUM): Dead Code in `understand_query()`

**File**: `agents/query_understanding_agent.py` (lines 320-324)

**Symptom**: No runtime impact, but confusing and creates a false sense of safety.

**Root Cause**: The fallback logic `if target_region == "global" and home_location != "global"` inside `understand_query()` was meant to override target_region with the user's home location. However, `home_location` is ALWAYS passed as `"Global"` from the orchestrator (line 360), making this code dead — it never fires.

The same logic is (correctly) duplicated in `orchestrator.py:389-393` where the real `home_loc` is available. So this dead code was just a redundant copy that never executed.

**Fix**: Replaced with a comment explaining the design decision.

---

## Bug 4 (HIGH): Cross-Role Semantic Cache Leakage

**File**: `agents/orchestrator.py` (lines 415-426)

**Symptom**: A VP's cached answer could be returned to a regular employee for a similar query. Different roles have different policies (executive travel vs standard travel), so this is a correctness issue.

**Root Cause**: The semantic cache lookup looped through ALL role buckets:
```python
for rk in ["employee", "manager", "vp", "executive"]:
    cached_answer = redis_cache.find_similar_cached(...)
```

This means if a VP asked about "travel policy" and got an executive-level answer cached, a regular employee asking the same question would get that VP-specific answer.

**Fix**: Only search the user's own role bucket:
```python
role_key_for_cache = _get_role_key(access_info.get("roles", {}))
cached_answer = redis_cache.find_similar_cached(
    _query_embedding_cache, target_region.lower(), role_key_for_cache
)
```

---

## Bug 5 (MEDIUM): Conversation History Has No TTL

**File**: `agents/caching_agent.py` (line 43) / `tools/cache_tools.py`

**Symptom**: Session histories grow Redis memory unboundedly. If Redis fills up, old sessions stay forever unless explicitly cleared via `/new-chat`.

**Root Cause**: `lpush` was called without any TTL. While `ltrim(0, 9)` limits each session to 10 turns, the keys themselves never expire.

**Fix**: 
- Added `ttl` parameter to `RedisCache.lpush()` method
- Set 24-hour TTL on conversation history writes

---

## Bug 6 (LOW): Semantic Cache Counter Has No TTL

**File**: `tools/cache_tools.py` (line 186)

**Symptom**: `sem_cache_counter:{region}:{role_key}` keys accumulate forever in Redis since they use `INCR` without `EXPIRE`.

**Fix**: Added 7-day TTL after each increment.

---

## Remaining Considerations (Not Fixed)

### Semantic Cache Brute-Force Scan
`find_similar_cached()` iterates ALL entries in a bucket — O(n) with numpy dot products. This will degrade as more queries are cached. Consider using Redis Vector Search (RediSearch) or an approximate nearest neighbor approach for production scale.

### `home_location="Global"` Passed to LLM
The orchestrator passes `home_location="Global"` to `understand_query()` because the real home_loc hasn't resolved from BQ yet. This means the LLM has no context about the user's actual location when analyzing the query. A future optimization would be to wait for `fut_home` before calling `understand_query()`, or to restructure the pipeline so the LLM gets the real home_loc. This would improve target_region extraction accuracy.

---

## Bug 7 (CRITICAL): Cross-Country KB Source Contamination

**File**: `agents/post_validation_agent.py`

**Symptom**: India users receive KB article links from UK, Indonesia, Vietnam, Malaysia, Spain, France, Austria, Israel, and Thailand. For example, an India user asking "Where can I apply leave?" gets sources `KB0018923` (Indonesia) and `KB0018926` (Indonesia) which reference "Peoplepay" — the Indonesian leave system.

**Root Cause**: A multi-layer failure in the country detection pipeline:

1. **Vector search** (retrieval_agent) returns semantically similar docs regardless of country — by design, but wrong-country docs make it through.
2. **Reranking** (reranking_agent) has 5-layer region detection, but ServiceNow KB chunks with generic filenames (`ServiceNow_KB_KB0018923.html`) and NULL/empty metadata escape all detection because they contain no country names.
3. **Post-validation** (post_validation_agent) `_filter_mismatched_sources()` had these specific gaps:
   - **No KB→country mapping**: The 20+ most-problematic ServiceNow KBs have no country in their filename, no metadata fields set in Firestore, and no explicit country name in their text content.
   - **Single-source bypass**: `len(sources) <= 1` skipped filtering entirely.
   - **Safety net returned originals**: `return filtered if filtered else sources` kept wrong-country sources when ALL sources were wrong.
   - **Fallback path unfiltered**: `extract_unique_sources()` had no country filtering.
   - **No system-name detection**: Country-specific HR system names (Peoplepay=Indonesia, Darwin=UK) weren't recognized as country signals.

**Fix** (multi-stage, applied across two iterations):

### Iteration 1: Foundation fixes
- Replaced naive substring country detection with word-boundary-safe regex (`_detect_countries_in_text()`)
- Added `_get_source_country_from_metadata()` — uses Firestore country/region fields + chunk text scanning as fallback
- Enhanced `_filter_mismatched_sources()` — accepts `results` and `target_region` params
- Removed single-source bypass
- Changed safety net: return empty list when `target_region` is explicit and ALL sources are wrong-country
- Added country filtering to fallback path (`extract_unique_sources()`)
- Added chitchat/greeting detection to suppress sources on acknowledgment responses
- **Result**: Cross-country KB issues dropped from 14 → 11

### Iteration 2: KB→country override map
- Added `_KB_COUNTRY_OVERRIDES` dict mapping 20 known problematic KB numbers to their correct country:
  - Indonesia: KB0018923, KB0018926, KB0018925, KB0018922
  - UK: KB0018733, KB0018757, KB0018734, KB0019772, KB0018339, KB0017452
  - Vietnam: KB0018378
  - Malaysia: KB0018389
  - Spain: KB0018330, KB0017921
  - Israel: KB0018410
  - Austria: KB0018405
  - Thailand: KB0018342
  - France: KB0018574
  - Irrelevant: KB0018614
- Added `_KB_NUM_RE` regex to extract KB numbers from both `ServiceNow_KB_KB*.html` and `ServiceNow KB KB*.html` filename patterns
- KB override is checked as **Priority 0** in `_get_source_country_from_metadata()`, before Firestore metadata and text scanning
- Added `_SYSTEM_COUNTRY_MAP` for country-specific HR system name detection (Peoplepay→Indonesia, Darwin→UK, Sodexo→India)
- Added `include_system_names` parameter to `_detect_countries_in_text()` — enabled for source/chunk text, disabled for answer text (to prevent the answer mentioning wrong-country systems from being interpreted as "wanted" countries)
- **Result**: Cross-country KB issues dropped from 11 → 0

### Test Results Progression

| Metric | Baseline | After Iter 1 | After Iter 2 |
|--------|----------|-------------|-------------|
| CROSS_COUNTRY_KB | 14 | 11 | **0** |
| Total issues | 32 | 22 | **3** |
| Failed questions | 22/71 | 16/71 | **3/71** |
| Pass rate | 31% | 35% | **47%** |

---

## Bug 8 (LOW): Test Prompt File Header Parsed as Question

**File**: `scripts/run_tests.py`, `Testing Prompts/UAT_*.txt`

**Symptom**: The first line of each UAT test prompt file (e.g., "UAT Feedback Questions - April 3, 2026") was parsed as Q1, causing an off-by-one error in all evaluation checks.

**Root Cause**: `parse_questions()` didn't have a skip pattern for `UAT` header lines, and the header exceeded the 15-character minimum length threshold, so it was treated as a valid question.

**Fix**:
- Removed header lines from all 3 test prompt files
- Added `r"^(UAT\s|Focused Test|Access Control Questions)"` to the skip pattern list in `parse_questions()`

---

## Remaining Issues (Not Fixed)

### "Peoplepay" in Answer Text (Q3)
When an India user asks "Where can I apply leave?", the answer still mentions "Peoplepay" (Indonesia leave system) even though the Indonesia KB sources are now filtered out. This is because the LLM generates the answer from retrieved chunks BEFORE source filtering — the wrong-country content is already in the generated text. Fixing this requires either:
1. Pre-filtering retrieved chunks by country BEFORE passing to the generation LLM
2. Adding a post-generation content validation step that checks for country-specific system names

### Leave Balance Query Limitation
"How many leave days remaining?" returns a no-info response because the agent cannot access individual leave balance data from any HR system.

---

## Bug 9 (CRITICAL): Poland User Holiday Query — Multi-Layer Failure

**Files**: `agents/access_control_agent.py`, `agents/reranking_agent.py`, `agents/post_validation_agent.py`, `agents/orchestrator.py`

**Symptom**: Two reported bugs:
1. With `teams_metadata` containing Poland user (Country: "Poland"), agent says "I don't have info on Poland policies"
2. Without `teams_metadata` location info, agent returns Global holidays instead of Poland holidays, with non-KB sources ("APAC Region holidays PDF")

**Root Cause**: A chain of 6 interconnected issues:

### 9a: teams_metadata Key Casing Bug (access_control_agent.py)

The code used `teams_metadata.get('country')` (lowercase 'c'), but Microsoft Teams sends `"Country"` (uppercase 'C'). Python dict keys are case-sensitive, so the country value was **never read** from Teams metadata. It only worked incidentally when `usageLocation: "PL"` was present and tokenized by `_resolve_country()`.

```python
# Before (broken):
meta = f"Country: {teams_metadata.get('country', 'None')}"  # Always "None"

# After (fixed):
_tm_country = (
    teams_metadata.get("Country") or teams_metadata.get("country") or "None"
)
```

**Fixed in**: `get_user_allowed_locations()` (line 275) and `check_access()` (line 360).

### 9b: Same-Broad-Region Cross-Country Contamination (reranking_agent.py)

The region detection classified UK/Spain/Italy/France articles as "regional" for a Poland user because they all share the "EMEA" broad region. The classification logic checked `is_regional` before `is_other`, so even when a result was detected as belonging to a different country, `is_regional=True` took priority.

```python
# Before (broken):
# chunk_region="emea" matched region_variants=["poland","emea","pl"] → is_regional=True
# Even though country="United Kingdom" → is_other=True
# But: "if is_regional" checked first → goes into regional bucket

# After (fixed):
# 1. Region field check now also verifies the country field
if chunk_country_raw and chunk_country_raw not in ("global", "", target_region.lower()):
    country_matches_target = any(v in chunk_country_raw for v in region_variants)
    if not country_matches_target:
        is_regional = False  # UK article NOT regional for Poland

# 2. Classification prioritizes is_other over is_regional
if is_regional and not is_other:
    regional.append(res)
```

### 9c: Missing Broad Region Tags (post_validation_agent.py)

Filenames like "APAC Region Holiday Calendar.pdf" contained "APAC" but `_detect_countries_in_text()` didn't recognize "APAC", "EMEA", or "Americas" as region tags. So these sources had "no country signal" and were kept as "likely global."

**Fix**: Added APAC/EMEA/Americas to `_SHORT_COUNTRY_TAGS` with word-boundary regex:
```python
"apac": re.compile(r"\bapac\b", re.IGNORECASE),
"emea": re.compile(r"\bemea\b", re.IGNORECASE),
"americas": re.compile(r"\bamericas\b", re.IGNORECASE),
```

Also replaced hardcoded country→region aliases with systematic `_COUNTRY_BROAD_REGION` mapping (covers all ~40 countries) and `_SUB_REGION_COUNTRIES` mapping (ANZ, CEE, DACH, etc.).

### 9d: Non-KB Holiday Sources Not Filtered (reranking_agent.py)

Old-schema documents (legacy PDFs from the `main` index group / `hr_policy_chunks` collection) had no `country`/`region` metadata and would appear alongside ServiceNow KB articles. The user correctly observed that only APAC payroll, P-Card, and bulk expense should have non-KB sources.

**Fix**: Added `_is_kb_source()` helper and KB-preference logic in holiday isolation:
```python
kb_dedicated = [
    r for r in dedicated
    if _is_kb_source(r) or r.get("collection") in _NON_KB_COLLECTIONS
]
if kb_dedicated:
    filtered = kb_dedicated  # Drop legacy PDFs when KB articles are available
```

### 9e: Refined Retrieval Missing Region Augmentation (orchestrator.py)

The refined holiday retrieval launched at line ~716 used the raw `search_query` (e.g., "what are the holidays i have?") WITHOUT the target country. The region augmentation (`search_query + " in Poland"`) happened later at line ~923, AFTER retrieval was already launched in the background. This meant vector search never included "Poland" in the query, returning generic/global holiday results instead of Poland-specific ones.

**Fix**: Augment the refined retrieval query with `target_region` before launching:
```python
_refined_query = search_query
if target_region.lower() != "global" and target_region.lower() not in search_query.lower():
    _refined_query = f"{search_query} in {target_region}"
```

### 9f: Incomplete Fallback Phrase Detection (orchestrator.py)

The generation fallback retry checked for "i don't have information" but NOT "i don't have specific information" or "i don't have a specific list." The LLM's country filtering prompt (rule 9) generated these variations when context lacked the target country's data, but the fallback detection missed them.

**Fix**: Added broader phrases:
```python
"i don't have specific",
"i don't have a specific",
```

### Test Results

| Scenario | Before Fix | After Fix |
|----------|-----------|-----------|
| Poland user + teams_metadata "Country" | Blocked / "no info" | Full 16-holiday calendar from KB0018280 |
| Poland user + BQ lookup only | Global holidays + APAC PDF source | Full 16-holiday calendar from KB0018280 |
| India user (regression check) | Works | Still works — complete regional calendars from KB0019419 |
