# HD SKYE Agent — Complete Flow Documentation

> **Last updated**: 2026-03-30
> **Covers**: All 14 agents, 5 tool modules, variant system, caching, and full pipeline

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Agent Variant System](#agent-variant-system)
3. [Infrastructure: Indexes, Firestore & Endpoints](#infrastructure)
4. [Entry Points (main.py)](#entry-points)
5. [Pipeline Phases](#pipeline-phases)
   - [Phase 0: Cache Check](#phase-0-cache-check)
   - [Phase 1: Understanding ‖ Retrieval ‖ Home Location (Parallel)](#phase-1-parallel)
   - [Phase 2: Guardrails](#phase-2-guardrails)
   - [Phase 3: Access Control](#phase-3-access-control)
   - [Phase 4: Reranking & Filtering](#phase-4-reranking)
   - [Phase 5: Generation](#phase-5-generation)
   - [Phase 6: Post-Processing (Parallel)](#phase-6-post-processing)
6. [Caching Architecture](#caching-architecture)
7. [Tool Modules](#tool-modules)
8. [Firestore Schema Reference](#firestore-schema-reference)
9. [Error Handling & Fallbacks](#error-handling)

---

## Architecture Overview

SKYE is an agentic HR RAG (Retrieval-Augmented Generation) system with **aggressive parallelism** — speculative retrieval starts at t=0 alongside query understanding, and post-processing tasks run concurrently.

```
Request
  │
  ▼
┌─────────────────────────────────────────────────────┐
│  Phase 0: Exact-match answer cache check            │
│  (variant-scoped, returns immediately if hit)       │
└───────────────┬─────────────────────────────────────┘
                │ cache miss
                ▼
┌─────────────────────────────────────────────────────┐
│  Phase 1: PARALLEL                                  │
│  ┌──────────────────┐ ┌──────────────────┐          │
│  │ Query             │ │ Vector Search    │          │
│  │ Understanding     │ │ (speculative,    │          │
│  │ (LLM analysis,    │ │  all variant     │          │
│  │  translation,     │ │  index groups)   │          │
│  │  intent, opco)    │ │                  │          │
│  └──────────────────┘ └──────────────────┘          │
│  ┌──────────────────┐                               │
│  │ Home Location    │                               │
│  │ (BQ user profile │                               │
│  │  + role matrix)  │                               │
│  └──────────────────┘                               │
└───────────────┬─────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────┐
│  Phase 2: Guardrails                                │
│  (HV blocking, P-Card gating, greeting detection)   │
└───────────────┬─────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────┐
│  Phase 3: Access Control                            │
│  (role-based filtering, location resolution)        │
└───────────────┬─────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────┐
│  Phase 4: Reranking & Filtering                     │
│  Step 1: Vertex AI semantic reranker                │
│  Step 1.5: Index priority boost                     │
│  Step 1.6: Category boost                           │
│  Step 2: Region boost                               │
│  Step 3: Holiday/PCard context boost                │
│  Step 4: Other-region penalty                       │
│  Step 5: Top-K selection                            │
└───────────────┬─────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────┐
│  Phase 5: Generation                                │
│  (Gemini LLM with master HR prompt)                 │
└───────────────┬─────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────┐
│  Phase 6: PARALLEL                                  │
│  ┌──────────────────┐ ┌──────────────────┐          │
│  │ Translation      │ │ Source           │          │
│  │ (if non-English  │ │ Validation &     │          │
│  │  user locale)    │ │ ServiceNow URLs  │          │
│  └──────────────────┘ └──────────────────┘          │
│  ┌──────────────────┐ ┌──────────────────┐          │
│  │ Suggested        │ │ Cache answer     │          │
│  │ Follow-ups       │ │ for future hits  │          │
│  └──────────────────┘ └──────────────────┘          │
└─────────────────────────────────────────────────────┘
```

### Key Design Principles

- **Speculative retrieval**: Vector search fires at t=0 using the raw query, before understanding completes. If understanding produces a refined/translated query, a second retrieval fires and results merge.
- **Variant scoping**: Each variant (main, pcard, bulk_expense, payroll) searches only its configured index groups and applies boosting to priority collections/categories.
- **3-layer caching**: Exact-match answer cache → semantic similarity cache (cosine ≥ 0.95) → vector search result cache. All variant-scoped.
- **Graceful degradation**: Every agent has try/except with fallback defaults. If vector search fails, the system still generates a "no context" response.

---

## Agent Variant System

### Variants

| Variant | Description | Index Groups | Priority Collections | Priority Categories |
|---------|-------------|-------------|---------------------|-------------------|
| `main` | Default HR assistant | all 5 groups | none | none |
| `pcard` | P-Card policy specialist | `servicenow_kb` + `pcard` | `p-card_policy` | `P Card` |
| `bulk_expense` | Bulk expense specialist | `servicenow_kb` + `bulk_exp` | `bulk-expense` | `Bulk Expense` |
| `payroll` | APAC payroll specialist | `apac_payroll` | `apac-payroll-chunks` | none |

### Index Groups → Physical Resources

| Index Group | Vector Search Index | Endpoint | Firestore DB | Firestore Collection |
|------------|-------------------|----------|-------------|---------------------|
| `main` | `MAIN_INDEX_ID` | main endpoint | `hd-skye-db` / `(default)` | `hd-skye-2-0-chunks` |
| `servicenow_kb` | `SERVICENOW_KB_INDEX_ID` | ServiceNow endpoint | `hd-skye-db-servicenow` | `servicenow_kb` |
| `pcard` | `PCARD_INDEX_ID` | ServiceNow endpoint | `hd-skye-db-servicenow` | `p-card_policy` |
| `bulk_exp` | `BULK_EXP_INDEX_ID` | ServiceNow endpoint | `hd-skye-db-servicenow` | `bulk-expense` |
| `apac_payroll` | `APAC_PAYROLL_INDEX_ID` | main endpoint | `hd-skye-db-servicenow` | `apac-payroll-chunks` |

### How Variants Affect the Pipeline

1. **Retrieval** (`retrieval_agent.py`): `search_vectors()` accepts `index_groups` parameter. Only the variant's configured groups are searched. Cache keys include variant name.
2. **Reranking** (`reranking_agent.py`): Receives `priority_collections` and `priority_categories`. Step 1.5 boosts chunks from priority collections by `+0.15`. Step 1.6 boosts chunks matching priority categories by `+0.10`.
3. **Caching** (`orchestrator.py`): Answer cache keys are prefixed with variant name, e.g., `answer_cache:pcard:{query_hash}`.
4. **Endpoints** (`main.py`): Each variant has a dedicated endpoint (`/pcard/chat`, etc.) that sets the variant before calling the orchestrator.

---

## Infrastructure

### Two Physical Vector Search Endpoints

**Main Endpoint** (`ENDPOINT_ID` / `DEPLOYED_INDEX_ID`):
- Hosts: `main` index, `apac_payroll` index
- Firestore DB: `hd-skye-db` (for main), `hd-skye-db-servicenow` (for APAC payroll)

**ServiceNow Endpoint** (`SERVICENOW_ENDPOINT_ID` / `SERVICENOW_DEPLOYED_INDEX_ID`):
- Hosts: `servicenow_kb` index, `pcard` index, `bulk_exp` index
- Firestore DB: `hd-skye-db-servicenow` (all three)

### Firestore Databases

| DB Name | Collections | Schema |
|---------|------------|--------|
| `hd-skye-db` (or `(default)`) | `hd-skye-2-0-chunks` | Old schema: `text`, `source`, `metadata` (nested) |
| `hd-skye-db-servicenow` | `servicenow_kb`, `p-card_policy`, `bulk-expense`, `apac-payroll-chunks` | New schema: rich top-level fields |

See [Firestore Schema Reference](#firestore-schema-reference) for full field details.

---

## Entry Points

### `main.py` — FastAPI Application

**Standard Endpoints:**
- `POST /chat` — Main chat (variant from request body, defaults to `main`)
- `POST /pcard/chat` — P-Card variant (hardcoded `variant="pcard"`)
- `POST /bulk-expense/chat` — Bulk Expense variant (hardcoded `variant="bulk_expense"`)
- `POST /payroll/chat` — Payroll variant (hardcoded `variant="payroll"`)
- `GET /variants` — Returns available variants with their configuration

**Utility Endpoints:**
- `POST /feedback` — Submit user feedback to Firestore
- `POST /cache/clear` — Clear Redis caches
- `GET /health` — Health check

**Request Schema** (`QueryRequest`):
```python
{
    "query": str,             # User's question
    "session_id": str,        # Conversation session ID
    "user_email": str,        # User's email for BQ profile lookup
    "user_name": str | None,  # Display name
    "user_locale": str = "en",# Language locale (en, ja, etc.)
    "variant": str = "main"   # Agent variant
}
```

**Response Schema** (`QueryResponse`):
```python
{
    "answer": str,                    # Generated answer (markdown)
    "translated_answer": str | None,  # Translated if non-English locale
    "sources": list[dict],            # Source documents with URLs
    "query_understanding": dict,      # Intent, entities, analysis
    "access_control": dict,           # User's role/permissions
    "suggested_questions": list[str], # Follow-up suggestions
    "timing": dict,                   # Per-phase timing breakdown
    "cache_hit": bool,                # Whether answer came from cache
    "cached_at": str | None,          # Timestamp of cached answer
    "variant": str                    # Which variant served this
}
```

---

## Pipeline Phases

### Phase 0: Cache Check

**File**: `orchestrator.py` → `process_query()` entry point

1. Hash the query: `query_hash = hashlib.md5(query.lower().strip())`
2. Check Redis for exact-match answer: key = `answer_cache:{variant}:{query_hash}`
3. If hit: return cached response immediately with `cache_hit=True`
4. If miss: proceed to Phase 1

### Phase 1: Parallel — Understanding ‖ Retrieval ‖ Home Location

Three tasks run concurrently via `asyncio.gather()`:

#### 1A. Query Understanding (`query_understanding_agent.py`)

**Purpose**: Analyze the user's query to extract intent, entities, language, and produce a refined search query.

**Steps**:
1. **Language detection**: Detect input language via LLM
2. **Translation**: If non-English, translate to English for downstream processing
3. **OPCO detection** (`opco_tools.py`): Extract operating company mentions (e.g., "GlobalLogic", "Hitachi Vantara") from query text using keyword matching
4. **LLM analysis**: Send query to Gemini with structured prompt requesting:
   - `intent`: The user's goal (e.g., "policy_inquiry", "process_question", "benefits_question")
   - `entities`: Named entities (people, departments, policies)
   - `refined_query`: Optimized query for vector search
   - `is_greeting`: Boolean — is this a greeting/small-talk?
   - `is_followup`: Boolean — continuation of previous conversation?
   - `requires_personal_data`: Boolean — needs user-specific info?
   - `query_category`: High-level category (hr_policy, benefits, payroll, etc.)
5. **Return**: `QueryUnderstanding` dict with all extracted fields

#### 1B. Speculative Retrieval (`retrieval_agent.py`)

**Purpose**: Start vector search immediately using the raw query, before understanding completes.

**`search_vectors(query, index_groups)` flow**:
1. **Embed query**: `tools/embedding_tools.py` → Vertex AI `text-embedding-004`, dimension 768
2. **Check vector search cache**: key = `vector_cache:{variant}:{query_hash}`
3. **If cache miss**: For each index group in the variant's config:
   a. Determine which physical endpoint to use (main vs ServiceNow)
   b. Call `aiplatform.MatchingEngineIndexEndpoint.find_neighbors()`
   c. Receive `(id, distance)` pairs
4. **Firestore hydration**: For each result ID:
   a. Look up document in the index group's Firestore collection + DB
   b. Extract all available fields (text, source, metadata, and new-schema fields)
   c. Attach `_index_group` and `_collection` metadata for downstream boosting
5. **Merge results** from all index groups
6. **Cache results** in Redis
7. **Return**: List of chunk dicts with text + metadata

**After understanding completes**: If `refined_query` differs from raw query, a second retrieval fires with the refined query, and results are deduplicated by chunk ID and merged.

#### 1C. Home Location (`bq_tools.py`)

**Purpose**: Look up the user's profile from BigQuery for region-aware filtering.

**Two parallel BQ queries**:
1. **User profile**: `SELECT * FROM employee_directory WHERE email = ?`
   - Returns: `home_country`, `home_city`, `department`, `job_title`, `manager_email`, `opco`
2. **Role/reportees**: `SELECT role, reportee_count FROM employee_roles WHERE email = ?`
   - Returns: `role` (IC, Manager, VP, etc.), `reportee_count`

**Returns**: `UserProfile` dict with location, role, and org info.

### Phase 2: Guardrails

**File**: `agents/guardrails_agent.py`

**Purpose**: Gate certain queries before they reach generation.

**Checks (in order)**:
1. **Greeting detection**: If `query_understanding.is_greeting == True`, return a canned greeting response immediately. Skip all remaining phases.
2. **Hitachi Vantara blocking**: If OPCO detection found "Hitachi Vantara" (HV) references, block the query with a message directing the user to HV's own HR portal. HV has a separate system and SKYE should not answer HV-specific questions.
3. **P-Card gating** (for non-pcard variants): If `opco_tools.is_pcard_query()` returns True and the current variant is not `pcard`, the system may add a note suggesting the user try the P-Card specialist.

**If blocked**: Return immediately with a gating response (no generation).

### Phase 3: Access Control

**File**: `agents/access_control_agent.py`

**Purpose**: Determine what information the user is allowed to see based on their role and location.

**Steps**:
1. **Resolve user location**: From BQ profile → `home_country`, `home_city`. Fall back to `opco` mapping if location fields are empty.
2. **Determine region**: Map country → region (`APAC`, `EMEA`, `Americas`) using `BROAD_REGION_COUNTRIES` mapping.
3. **Build role matrix**: Based on user's role (IC, Manager, VP, HR, Admin):
   - **IC (Individual Contributor)**: Can see general HR policies + own-region content
   - **Manager**: Can see general + own-region + team management policies
   - **VP+**: Can see general + all-region summaries + executive policies
   - **HR**: Can see all content including sensitive HR procedures
   - **Admin**: Unrestricted access
4. **Location-based access tags**: Generate tags like `region:APAC`, `country:Japan`, `opco:GlobalLogic` that are used in reranking.

**Returns**: `AccessControl` dict with `role`, `region`, `country`, `access_tags`, `allowed_content_types`.

### Phase 4: Reranking & Filtering

**File**: `agents/reranking_agent.py` → `rerank_and_filter()`

**Purpose**: Re-score and filter the raw vector search results using semantic relevance, region affinity, variant boosting, and contextual signals.

**Input**: Raw chunks from retrieval, user's query, access control info, variant config, `apply_region_filter` flag.

**Region Filter Gate** (`apply_region_filter` parameter):
- Controlled by `REGION_FILTER_MODE` env var (see `config.py`)
- Resolved per-request in `orchestrator.py` via `should_apply_region_filter(roles)`
- Modes: `all` (default — everyone gets region filtering), `managers_up`, `vp_up`, `none`
- When `apply_region_filter=False`: Steps 2 and 4 (region boost + other-region penalty) are **skipped entirely**. Results are ranked purely by semantic relevance + variant boosts. Holiday isolation (content-based) is still applied.
- When `apply_region_filter=True`: Full pipeline including region boost/penalty (current default behavior).
- This does **not** affect access control (Phase 3) — a user denied access by `check_access` is still denied regardless.

**Step 1: Vertex AI Semantic Reranking**
- Send chunks to Vertex AI Ranking API (`google.cloud.discoveryengine`)
- Model: `semantic-ranker-512` (handles up to 512 tokens per chunk)
- Returns normalized relevance scores [0, 1]

**Step 1.5: Index Priority Boost** (NEW — variant system)
- If `priority_collections` is set (e.g., `["p-card_policy"]` for pcard variant):
- For each chunk, check if `chunk._collection` is in `priority_collections`
- If yes: `score += 0.15`
- This ensures chunks from the variant's primary collection rank higher

**Step 1.6: Category Boost** (NEW — variant system)
- If `priority_categories` is set (e.g., `["P Card"]` for pcard variant):
- For each chunk, check if `chunk.category` matches any priority category
- If yes: `score += 0.10`
- Works with the new-schema `category` field (title case, space-separated)

**Step 2: Region Boost**
- 4-level region detection cascade for each chunk:
  1. **`region` field** (new schema): Direct match if present (values: `APAC`, `EMEA`, `Americas`, `null`=GLOBAL)
  2. **Filename regex**: Pattern matching on `source` field (e.g., `APAC_Benefits.pdf` → APAC)
  3. **`country` field** (new schema): Map country name → region via `BROAD_REGION_COUNTRIES`
  4. **Text content**: Scan chunk text for region/country keywords
- If chunk's detected region matches user's region: `score += 0.10`
- If chunk appears to be GLOBAL (no region detected): no adjustment

**Step 3: Holiday/PCard Context Boost**
- Uses `opco_tools.is_holiday_query()` and `opco_tools.is_pcard_query()`
- If query is about holidays and chunk mentions holidays: `score += 0.05`
- If query is about P-Card and chunk mentions P-Card: `score += 0.05`

**Step 4: Other-Region Penalty**
- If a chunk's detected region does NOT match the user's region AND is not GLOBAL:
- `score -= 0.15` (significant penalty to push irrelevant-region content down)
- Uses the same 4-level region detection from Step 2

**Step 5: Top-K Selection**
- Sort by final score descending
- Take top `TOP_K_RESULTS` (configurable, default 10)
- Return as reranked chunk list

**Note**: Reranking happens up to 3 times in the pipeline:
1. On speculative retrieval results (before understanding completes)
2. On refined-query retrieval results (after understanding)
3. On merged results (final reranking before generation)

### Phase 5: Generation

**File**: `agents/generation_agent.py`

**Purpose**: Generate a comprehensive HR answer using Gemini LLM with retrieved context.

**Steps**:
1. **Build context window**: Concatenate top reranked chunks into a context string, each prefixed with source info
2. **Build system prompt**: Master HR assistant prompt that instructs the model to:
   - Answer based ONLY on provided context (no hallucination)
   - Use markdown formatting with headers, bullets, tables
   - Cite sources inline
   - Acknowledge when information is insufficient
   - Be professional, empathetic, and helpful
   - Consider the user's region/country for region-specific policies
3. **Build user prompt**: Includes the query, conversation history (from Redis), and user profile context
4. **Call Gemini**: `gemini-2.0-flash` (configurable) with temperature 0.1 for factual accuracy
5. **Return**: Generated markdown answer

### Phase 6: Post-Processing (Parallel)

Four tasks run concurrently after generation:

#### 6A. Translation (`translation_agent.py`)

- Only runs if `user_locale != "en"`
- Translates the markdown answer to the user's locale
- Uses Gemini with a translation-specific prompt that preserves markdown formatting
- Handles edge cases: code blocks, URLs, proper nouns stay in English

#### 6B. Source Validation & URL Mapping (`post_validation_agent.py`)

- Validates that cited sources actually exist in the retrieved chunks
- Maps internal source references to ServiceNow portal URLs
- Uses `kb_renderer.py` for ServiceNow KB article URL mapping
- Builds the `sources` list in the response with `title`, `url`, `snippet`

#### 6C. Suggested Follow-ups

- Generated by Gemini in the same generation call (or a lightweight follow-up call)
- 3 contextually relevant follow-up questions
- Based on the answer content and user's likely next questions

#### 6D. Cache Answer

- Store the full response in Redis with key `answer_cache:{variant}:{query_hash}`
- TTL: `CACHE_TTL` (configurable, default 24 hours)
- Includes timestamp for `cached_at` field in response

---

## Caching Architecture

**File**: `tools/cache_tools.py` → `RedisCache`

### Three Cache Layers

| Layer | Key Pattern | Purpose | TTL |
|-------|-----------|---------|-----|
| Answer Cache | `answer_cache:{variant}:{md5(query)}` | Exact-match full response | 24h |
| Semantic Cache | `semantic_cache:{variant}:{embedding}` | Similar query detection (cosine ≥ 0.95) | 24h |
| Vector Cache | `vector_cache:{variant}:{md5(query)}` | Raw retrieval results | 1h |

### Semantic Similarity Cache

1. On every query, compute the embedding
2. Scan recent semantic cache entries
3. Compute cosine similarity between query embedding and cached embeddings
4. If any similarity ≥ 0.95: return the cached answer (treat as equivalent query)
5. If miss: proceed with full pipeline, then store embedding + answer

### Conversation History

**File**: `agents/caching_agent.py`

- Stored in Redis as a list per session: key = `conversation:{session_id}`
- Each entry: `{"role": "user"|"assistant", "content": str, "timestamp": str}`
- Used by generation agent for context continuity
- Max history: configurable (default 10 turns)
- TTL: 2 hours (conversation timeout)

---

## Tool Modules

### `tools/opco_tools.py` — Operating Company Detection

- `detect_opco(query)`: Keyword scan for company names (GlobalLogic, Hitachi Vantara, Hitachi Energy, etc.)
- `is_hv_query(query)`: Returns True if query specifically targets Hitachi Vantara
- `is_pcard_query(query)`: Detects P-Card related queries via keyword matching
- `is_holiday_query(query)`: Detects holiday/leave-related queries
- Maintains a mapping of OPCO names → aliases for fuzzy matching

### `tools/bq_tools.py` — BigQuery User Profile

- `get_user_profile(email)`: Two parallel BQ queries for employee data + role
- `get_user_reportees(email)`: Query for direct/indirect reportees (for manager access)
- Uses `google-cloud-bigquery` client with project-scoped credentials
- Tables: `employee_directory`, `employee_roles` (configurable via env vars)

### `tools/cache_tools.py` — Redis Cache

- `RedisCache` class with `get`, `set`, `delete`, `scan` methods
- Connection: `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` env vars
- Key namespacing via `REDIS_KEY_PREFIX` (default `skye:`)
- Automatic JSON serialization/deserialization
- Connection pooling with max connections configurable

### `tools/embedding_tools.py` — Vertex AI Embeddings

- `get_embedding(text)`: Returns 768-dim float vector
- Model: `text-embedding-004` (configurable via `EMBEDDING_MODEL`)
- Used for both query embedding (vector search) and semantic cache similarity

### `tools/kb_renderer.py` — ServiceNow KB Rendering

- `get_kb_article_url(servicenow_number)`: Maps KB article number to portal URL
- `render_kb_markdown(gcs_uri)`: Fetches markdown from GCS and renders as HTML
- URL pattern: `https://hitachivantara.service-now.com/hrportal?id=kb_article&number={KB_NUM}`
- Handles table formatting, header cleanup, CSS stripping

---

## Firestore Schema Reference

### Old Schema (`hd-skye-2-0-chunks` in `hd-skye-db`)

```
{
  "text": str,           # Chunk text content
  "source": str,         # Source filename (used for region regex matching)
  "metadata": {          # Nested metadata dict
    "filename": str,
    "language": str,
    "access_level": str,
    "document_id": str
  },
  "parent_id": str,      # Parent chunk ID (for hierarchical chunking)
  "type": str,           # Chunk type
  "timestamp": timestamp
}
```

### New Schema (ServiceNow collections in `hd-skye-db-servicenow`)

Collections: `servicenow_kb`, `p-card_policy`, `bulk-expense`, `apac-payroll-chunks`

```
{
  "text": str,              # Chunk text content
  "source": str,            # Source identifier
  "country": str,           # Country name (e.g., "Japan", "United States")
  "region": str | null,     # APAC, EMEA, Americas, or null (=GLOBAL). ~54% populated
  "category": str | null,   # "P Card", "Bulk Expense", or null. ~5% populated
  "category_id": str,       # hr_policy, security, compliance, procurement, etc. 100% populated
  "policy_domain": str,     # hr, security, compliance, finance, general, it. 100% populated
  "language": str,          # Content language
  "is_table": bool,         # Whether chunk contains tabular data
  "chunk_title": str,       # Title of the chunk
  "section_title": str,     # Parent section title
  "servicenow_url": str,    # Original ServiceNow article URL
  "servicenow_number": str, # KB article number (e.g., "KB0012345")
  "gcs_uri": str,           # GCS path to source markdown
  "tenant_id": str,         # Multi-tenant identifier
  "embedding_model": str,   # Model used for embedding (e.g., "text-embedding-004")
  "parent_id": str,
  "type": str,
  "timestamp": timestamp
}
```

### Field Usage in Pipeline

| Field | Used In | Purpose |
|-------|---------|---------|
| `region` | Reranking (Step 2, 4) | Primary region detection (level 1 of cascade) |
| `country` | Reranking (Step 2) | Secondary region detection (level 3 of cascade) |
| `category` | Reranking (Step 1.6) | Category boost for variant-specific results |
| `_collection` | Reranking (Step 1.5) | Index priority boost (attached during retrieval) |
| `_index_group` | Reranking | Tracks which index group produced the chunk |
| `servicenow_number` | Post-validation | Maps to portal URL |
| `servicenow_url` | Post-validation | Direct source link |
| `is_table` | Generation | Context formatting hints |
| `chunk_title` | Generation | Section context |
| `policy_domain` | Future use | Available for domain-based filtering |
| `category_id` | Future use | Available for fine-grained category filtering |

---

## Error Handling

### Per-Agent Fallbacks

Every agent wraps its core logic in try/except:

| Agent | Fallback Behavior |
|-------|-------------------|
| Query Understanding | Returns raw query as-is, intent="unknown", is_greeting=False |
| Retrieval | Returns empty chunk list (generation will produce "no context" answer) |
| Home Location (BQ) | Returns empty profile (region filtering disabled) |
| Guardrails | Passes through (no blocking) |
| Access Control | Returns default IC role with no region restrictions |
| Reranking | Returns chunks in original order (skip reranking) |
| Generation | Returns "I'm unable to answer right now" message |
| Translation | Returns English answer (skip translation) |
| Post-validation | Returns sources as-is without URL mapping |

### Timeout Handling

- Vector search: 30s timeout per endpoint call
- BQ queries: 15s timeout
- LLM calls (Gemini): 60s timeout
- Redis operations: 5s timeout
- Overall request: 120s timeout (configured in Cloud Run)

### Logging

- Structured JSON logging throughout
- Every phase logs entry/exit with timing
- Error logs include stack traces
- Request ID propagated through all phases for tracing
