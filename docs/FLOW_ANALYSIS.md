# HD SKYE Agent - Complete Flow Analysis

## Architecture Overview

The SKYE agent is an HR policy RAG (Retrieval-Augmented Generation) system built with Google ADK (Agent Development Kit), FastAPI, Vertex AI, Redis, BigQuery, and Firestore. It answers HR policy questions with access control, multi-language support, and source attribution.

## File Structure

```
skye-agent/
├── main.py                          # FastAPI: /chat, /new-chat, /feedback, /cache/* endpoints
├── config.py                        # Centralized env-var loading (GCP, Redis, BQ, etc.)
├── .env                             # Local dev environment variables
├── Dockerfile                       # Multi-stage: Bun frontend build + Python backend
├── Makefile                         # Build/deploy scripts
├── env/
│   ├── prod.yaml                    # Cloud Run prod config
│   └── prod-poc.yaml                # Cloud Run POC config
├── agents/
│   ├── agent.py                     # AGENT_REGISTRY: central agent discovery
│   ├── orchestrator.py              # MASTER pipeline: process_query()
│   ├── access_control_agent.py      # Region/location ACL logic
│   ├── query_understanding_agent.py # LLM-based query analysis, translation, intent
│   ├── guardrails_agent.py          # Pre-flight: HV blocking, P-Card gating, greetings
│   ├── caching_agent.py             # Redis conversation history management
│   ├── retrieval_agent.py           # Vertex AI Matching Engine vector search + Firestore
│   ├── reranking_agent.py           # Region-aware reranking, OPCO filtering, holidays
│   ├── generation_agent.py          # Gemini LLM answer generation
│   ├── translation_agent.py         # Response translation (LLM-based, Markdown-preserving)
│   ├── post_validation_agent.py     # Source attribution + no-info fallback detection
│   ├── observability_agent.py       # Structured logging, timing decorators
│   ├── feedback_agent.py            # Firestore feedback submission
│   ├── embedding_agent.py           # Vertex AI text-embedding-004 wrapper
│   ├── parsing_chunking_agent.py    # Document AI / pypdf parsing + text chunking
│   ├── indexing_agent.py            # Matching Engine index management
│   └── ingestion_agent.py          # End-to-end doc ingestion pipeline
├── tools/
│   ├── cache_tools.py               # RedisCache class: get/set/hash/list + semantic cache
│   ├── bq_tools.py                  # BigQuery user/manager/roles/reportees lookups
│   ├── opco_tools.py                # OPCO detection, HV filtering, holiday/pcard detection
│   ├── embedding_tools.py           # generate_embeddings() with batching + retries
│   └── gcs_tools.py                 # GCS upload/download/signed URL generation
└── frontend/                        # React SPA (served by FastAPI)
```

---

## Complete Request-Response Flow

### Entry Point: `POST /chat` (main.py)

```
Request: { question, session_id, teams_metadata: { email }, data_scope }
```

- `data_scope` defaults to `"regional"` — enforces region-based access control
- `data_scope = "global"` — disables region restrictions

### Orchestrator Pipeline (`orchestrator.py::process_query()`)

The pipeline is heavily parallelized using `ThreadPoolExecutor(5)`:

```
                    t=0
                     │
     ┌───────────────┼───────────────────────────────────┐
     │               │                                   │
   History      Home-loc Access              Speculative Retrieval
   (instant)    (BQ lookups)                 (Vertex AI ~10s)
     │               │                                   │
     ▼               │                                   │
  Understanding      │                                   │
  (Gemini ~8s)       │                                   │
     │               │                                   │
     ▼               ▼                                   │
  ┌─ target_region fixup ─┐                              │
  │  (home_loc override)  │                              │
  └───────────────────────┘                              │
     │                                                   │
     ├── Cache Check (exact + semantic) ─→ early return? │
     │                                                   │
     ▼                                                   │
  ┌──────────────────┐                                   │
  │  PHASE 2 (parallel)                                  │
  │  ├─ Guardrails   │                                   │
  │  └─ Access Check │                                   │
  └──────────────────┘                                   │
     │                                                   │
     ▼                                                   ▼
  Gate Checks ────────────────────────→ Collect Retrieval
     │                                       │
     ▼                                       ▼
  Role-Based Query Rewrite ──────────→ Reranking & Filter
                                             │
                                             ▼
                                        Generation
                                         (Gemini)
                                             │
                                             ▼
                                    Fallback Retry (if needed)
                                             │
                                             ▼
                                ┌────────────┼────────────┐
                                │            │            │
                            Translation  Validation  Suggestions
                                │            │            │
                                └────────────┼────────────┘
                                             │
                                             ▼
                                   Cache + Save + Return
```

### Phase-by-Phase Breakdown

#### PHASE 1 - Parallel Launch (t=0)

Four tasks launch simultaneously:
1. **History retrieval** (Redis `lrange`) - instant, needed for understanding
2. **Home-loc access check** (BigQuery) - resolves user's home country
3. **Speculative vector search** (Vertex AI Matching Engine, top_k=40) - ~10s
4. **Query embedding generation** (Vertex AI text-embedding-004) - for semantic cache

Main thread blocks ONLY on history, then immediately runs:
- **Translation** — detects language via Cloud Translation API, translates to English
- **Explicit language detection** — regex patterns like "answer in Tamil"
- **Small talk check** — regex for greetings (early return if greeting)
- **Combined LLM analysis** — single Gemini call for: followup detection, intent, query rewriting, geography extraction, employee name detection

Then collects home_loc result and applies target_region fixup.

#### Translation Shortcut

If user asks to translate the previous response (e.g., "translate to Hindi"), short-circuits before any retrieval.

#### Cache Check

1. **Exact match** — `answer_cache:{query_en}:{region}` (Redis GET, <10ms)
2. **Semantic similarity** — cosine similarity against cached embeddings (threshold: 0.92)

#### PHASE 2 - Guardrails + Access (parallel)

Two tasks in parallel:
1. **Guardrails** — HV (Hitachi Vantara) blocking, P-Card gating, greeting handling
2. **Access control pipeline** — employee lookup (if mentioned) + full access check + user details from BQ

Gates: if guardrails block OR access denied, returns early with denial message.

#### PHASE 3 - Collect Retrieval + Reranking

- Collects speculative retrieval results
- For holiday queries or translated queries, also collects refined retrieval (top_k=100)
- **Reranking**: HV filter, holiday priority, region filtering, OPCO labeling
- Pre-generation fallback: if reranking returned empty, retry with broader "Global" search

#### PHASE 4 - Generation

- Gemini with comprehensive prompt including role context, OPCO labels, region
- If answer contains "I don't have information", retries with broader search (top_k=30)

#### PHASE 5 - Post-processing (parallel)

Three tasks in parallel:
1. **Translation** — if non-English, Markdown-preserving translation via Gemini
2. **Post-validation** — LLM source attribution + no-info fallback detection
3. **Follow-up suggestions** — Gemini generates 3 contextual suggestions

#### Final Steps

- Cache answer (exact + semantic)
- Save session context (user profile + access + Q&A)
- Save conversation turn to history
- Return response

---

## Access Control Flow

### How User Permissions Are Determined

1. `get_user_allowed_locations(email, teams_metadata)` (`access_control_agent.py`):
   - Starts with `["Global"]`
   - Checks if user is a manager via `check_is_manager(email)` (BQ lookup)
   - If manager: gets country from `get_manager_details_from_bq(email)`
   - If not found: gets country from `get_user_details_from_bq(email)`
   - If still not found: extracts from `teams_metadata` (country, usageLocation)
   - Each raw country string resolved via `_resolve_country_llm()` (Gemini call)
   - If manager: also adds all countries of direct reports

2. `check_access(email, target_region, teams_metadata, data_scope)`:
   - Gets user roles (manager/VP/executive)
   - Gets allowed locations
   - If `data_scope == "global"`, forces `allowed_locs = ["Global"]`
   - Derives `home_loc` = first non-Global location
   - If `data_scope != "global"` and target_region not in allowed_locs: **DENIED**

3. `is_location_allowed(target_region, allowed_locations)`:
   - Normalizes using a variants map (usa -> united states, uk -> united kingdom, etc.)
   - Returns True if target matches any allowed location

### data_scope Parameter

- `"regional"` (default): Full access control enforcement
- `"global"`: Forces `allowed_locs = ["Global"]` and skips access denial

---

## Caching Architecture

### Cache Infrastructure
- **Redis** with connection pooling (max 20 connections)
- Local dev: `localhost:6379` (no password)
- Prod: `10.180.68.37:6379` (internal VPC IP)
- JSON serialization for all values

### What Gets Cached

| Cache Key Pattern | What | TTL |
|---|---|---|
| `history:{session_id}` | Conversation turns (list, max 10) | 24 hours |
| `answer_cache:{query_en}:{region}` | Full answer data | 3 hours |
| `sem_cache:{region}:{role_key}:{idx}` | Semantic cache entries with embeddings | 3 hours |
| `sem_cache_index:{region}:{role_key}` | Index set of semantic cache entries | 3 hours |
| `sem_cache_counter:{region}:{role_key}` | Auto-increment counter | 7 days |
| `session:{session_id}:latest` | Full session context | 24 hours |
| `user_details:{email}` | BQ user details | 3 days |
| `user_roles_v4:{email}` | User roles | 3 days |
| `manager_details:{email}` | BQ manager details | 3 days |
| `reports_countries:{email}` | Direct reports' countries | 3 days |
| `reportees_list_v2:{email}:{is_vp}` | Full reportee list | 3 days |
| `resolved_country_v2:{raw}` | LLM-resolved country name | 30 days |
| `search:{tenant}:{query}:{top_k}` | Vector search results | 1 hour |

### Semantic Cache (Similarity-Based)

Custom implementation in `cache_tools.py`:
- Stores query embeddings alongside cached answers
- Organized by `{region}:{role_key}` buckets (role-isolated to prevent cross-role leakage)
- On lookup: fetches ALL entries in matching bucket, computes cosine similarity using numpy
- Returns best match if similarity >= 0.92
- Lazy cleanup of expired entries

---

## Model Versions

| Agent | Model |
|---|---|
| Query understanding | gemini-2.0-flash (via `LLM_MODEL`) |
| Country resolution | gemini-2.5-flash (hardcoded) |
| Generation | gemini-2.0-flash |
| Translation | gemini-2.0-flash |
| Post-validation | gemini-2.0-flash |
| Follow-up suggestions | gemini-2.0-flash |

---

## Key Design Patterns

1. **Speculative Retrieval**: Vector search starts at t=0, runs in parallel with query understanding (~8s). This effectively halves wall-clock time since retrieval (~10s) overlaps with understanding.

2. **Singleton Initialization**: All LLM model instances and API clients are lazy-loaded singletons (module-level globals with `_get_*()` functions). Safe for concurrent requests.

3. **Aggressive Parallelism**: Uses `ThreadPoolExecutor` throughout. Three parallel phases with 3-5 concurrent tasks each.

4. **Role-Based Query Rewriting**: Augments search queries with role-specific terms (executive travel policies, manager team management, etc.) to improve retrieval relevance.

5. **Multi-Level Fallback**: If initial retrieval + generation fails, retries with broader search parameters. Pre-generation fallback with global scope, then post-generation fallback with expanded top_k.
