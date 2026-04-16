# HD SKYE Agent - Configuration & Environment Variables Reference

Complete reference for all environment variables, settings, and configuration knobs that control which **agent variants** have access to which **vector indexes**, **Firestore collections/databases**, **BigQuery tables**, **Redis caches**, and **API endpoints**.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Google Cloud & Project Settings](#google-cloud--project-settings)
3. [Vector Search Index Configuration](#vector-search-index-configuration)
4. [Firestore Configuration](#firestore-configuration)
5. [Index Group Registry](#index-group-registry)
6. [Agent Variant System](#agent-variant-system)
7. [BigQuery Tables](#bigquery-tables)
8. [Redis Configuration](#redis-configuration)
9. [Cache TTLs & Thresholds](#cache-ttls--thresholds)
10. [Access Control & Super Admin](#access-control--super-admin)
11. [Application & Model Settings](#application--model-settings)
12. [Document AI](#document-ai)
13. [GCS (Cloud Storage)](#gcs-cloud-storage)
14. [ServiceNow](#servicenow)
15. [API Endpoints](#api-endpoints)
16. [Deploy Configuration](#deploy-configuration)
17. [Region & Category Metadata (Planned)](#region--category-metadata-planned)

---

## Architecture Overview

```
                  ┌──────────────────────────────────────┐
                  │         Agent Variants                │
                  │  (main, pcard, bulk_expense, payroll) │
                  └──────────┬───────────────────────────┘
                             │ each variant defines
                             ▼
                  ┌──────────────────────────────────────┐
                  │       Index Groups                    │
                  │  (main, servicenow_kb, pcard,        │
                  │   bulk_exp, apac_payroll)             │
                  └──────────┬───────────────────────────┘
                             │ each group maps to
                             ▼
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
   Vector Search       Firestore DB      Firestore Collection
   Index Endpoint      (database)         (chunks)
   + Deployed Index
```

**Flow**: Request hits an endpoint (e.g. `/pcard/chat`) -> variant is resolved -> variant's index groups are looked up -> each index group provides the Vector Search endpoint + Firestore DB/collection pair -> retrieval agent searches those indexes in parallel.

---

## Google Cloud & Project Settings

| Env Variable | Default | Description |
|---|---|---|
| `PROJECT_ID` | *(required)* | GCP project ID for Vertex AI, Firestore, etc. |
| `REGION` | `us-central1` | GCP region for Vertex AI endpoints |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | Path to GCP service account JSON (for Vertex AI, Firestore) |

**Config location**: `config.py:17-19`

---

## Vector Search Index Configuration

### Main HR Policy Index

| Env Variable | Default | Description |
|---|---|---|
| `INDEX_ID` / `HD_SKYE_INDEX_ID` | — | Vertex AI Matching Engine index ID |
| `INDEX_ENDPOINT_ID` / `HD_SKYE_INDEX_ENDPOINT_ID` | — | Full resource path of the index endpoint |
| `DEPLOYED_INDEX_ID` / `HD_SKYE_DEPLOYED_INDEX_ID` | `rag_hr_deployed_index` | Deployed index ID on the endpoint |

**Config location**: `config.py:32-38`

### ServiceNow KB Index (separate endpoint)

| Env Variable | Default | Description |
|---|---|---|
| `SERVICENOW_INDEX_ENDPOINT_ID` | `""` | Index endpoint for ServiceNow KB articles |
| `SERVICENOW_DEPLOYED_INDEX_ID` | `hd_skye_agents_servicenow` | Deployed index ID for ServiceNow |

**Config location**: `config.py:88-91`

### P-Card Index (shares ServiceNow endpoint)

| Env Variable | Default | Description |
|---|---|---|
| `PCARD_DEPLOYED_INDEX_ID` | `p-card` | Deployed index ID for P-Card policies |

**Config location**: `config.py:98`

### Bulk Expense Index (shares ServiceNow endpoint)

| Env Variable | Default | Description |
|---|---|---|
| `BULK_EXP_DEPLOYED_INDEX_ID` | `Bulk_Exp` | Deployed index ID for Bulk Expense policies |

**Config location**: `config.py:103`

### APAC Payroll Index (shares main endpoint)

| Env Variable | Default | Description |
|---|---|---|
| `APAC_PAYROLL_DEPLOYED_INDEX_ID` | `apac_payroll_deployed` | Deployed index ID for APAC Payroll |

**Config location**: `config.py:179-181`

---

## Firestore Configuration

### Firestore Databases

| Env Variable | Default | Used By |
|---|---|---|
| `HD_SKYE_FIRESTORE_DB` | `(default)` | Main HR policy chunks, APAC Payroll chunks |
| `SERVICENOW_FIRESTORE_DB` | `hd-skye-db-servicenow` | ServiceNow KB, P-Card, Bulk Expense chunks |
| `PCARD_FIRESTORE_DB` | `hd-skye-db-servicenow` | P-Card (same DB as ServiceNow) |
| `BULK_EXP_FIRESTORE_DB` | `hd-skye-db-servicenow` | Bulk Expense (same DB as ServiceNow) |

### Firestore Collections (chunk storage)

| Env Variable | Default | Index Group | Description |
|---|---|---|---|
| `HD_SKYE_FIRESTORE_COLLECTION` / `FIRESTORE_COLLECTION` | `hr_policy_chunks` | `main` | Main HR policy document chunks |
| `SERVICENOW_FIRESTORE_COLLECTION` | `hd-skye-chunks-servicenow` | `servicenow_kb` | ServiceNow KB article chunks |
| `PCARD_FIRESTORE_COLLECTION` | `p-card_policy` | `pcard` | P-Card policy chunks |
| `BULK_EXP_FIRESTORE_COLLECTION` | `bulk-expense` | `bulk_exp` | Bulk Expense policy chunks |
| `APAC_PAYROLL_FIRESTORE_COLLECTION` | `apac-payroll-chunks` | `apac_payroll` | APAC Payroll policy chunks |

### Chunk Document Schema (Firestore)

**Two schema versions coexist** — the retrieval agent handles both transparently.

#### New Schema (ServiceNow-endpoint collections: `hd-skye-chunks-servicenow`, `p-card_policy`, `bulk-expense`)

| Field | Type | Coverage | Description |
|---|---|---|---|
| `text` / `content` | string | 100% | The chunk text content |
| `source` / `doc_filename` | string | 100% | Original document filename |
| `country` | string | 100% | Country (e.g. "India", "GLOBAL") |
| `region` | string | ~54% (null=GLOBAL) | Geographic region: `APAC`, `EMEA`, `Americas` |
| `category_id` | string | 100% | **Primary category field**: `hr_policy`, `security`, `compliance`, `benefits_policy`, `leave_policy`, `procurement`, `travel_expense`, `general`, `it_support`, `p_card_policy`, `bulk_expense` |
| `category` | string | ~5% (mostly null) | Human-readable label (e.g. "P Card", "Bulk Expense") — sparse, prefer `category_id` |
| `policy_domain` | string | 100% | Domain: `hr`, `security`, `compliance`, `finance`, `general`, `it` |
| `document_type` | string | varies | Document classification |
| `language` | string | 100% | Document language |
| `is_table` | boolean | varies | Whether chunk contains tabular data |
| `chunk_title` | string | varies | Chunk-level heading |
| `section_title` | string | varies | Parent section heading |
| `policy_section` | string | varies | Policy section path |
| `policy_subsection` | string | varies | Policy subsection path |
| `servicenow_url` | string | ServiceNow only | Direct KB article URL |
| `servicenow_number` | string | ServiceNow only | KB article number |
| `gcs_uri` | string | varies | GCS URI of source document |
| `tenant_id` | string | 100% | Tenant identifier |
| `embedding_model` | string | 100% | Model used for embedding |

#### Old Schema (Main-endpoint collections: `hd-skye-2-0-chunks`, `apac-payroll-chunks`)

| Field | Type | Description |
|---|---|---|
| `text` | string | Chunk text content |
| `source` | string | Source filename |
| `metadata.filename` | string | Nested filename |
| `metadata.language` | string | Nested language |
| `metadata.access_level` | string | Nested access level |
| `metadata.document_id` | string | Nested document ID |
| `parent_id` | string | Parent chunk reference |
| `type` | string | Chunk type |
| `timestamp` | datetime | When indexed |

**Key Insight**: Old-schema collections have NO `country`, `region`, `category_id`, or `policy_domain` fields. Region filtering for these relies entirely on filename regex matching.

**Config location**: `config.py:44-107`, `retrieval_agent.py:206-234`

---

## Index Group Registry

Index groups are the core abstraction that maps a friendly name to a complete Vector Search + Firestore configuration. Groups are built at import time from env vars.

### How It Works

1. `_build_index_group_registry()` in `config.py:161-213` creates `IndexGroupConfig` objects
2. Groups whose `endpoint_id` is empty are silently excluded (partial dev setups)
3. `ENABLED_INDEX_GROUPS` controls which registered groups are available at query time

### Index Group → Resource Mapping

| Group Name | Vector Endpoint | Deployed Index ID | Firestore DB | Firestore Collection |
|---|---|---|---|---|
| `main` | `INDEX_ENDPOINT_ID` | `DEPLOYED_INDEX_ID` | `HD_SKYE_FIRESTORE_DB` | `HD_SKYE_FIRESTORE_COLLECTION` |
| `servicenow_kb` | `SERVICENOW_INDEX_ENDPOINT_ID` | `SERVICENOW_DEPLOYED_INDEX_ID` | `SERVICENOW_FIRESTORE_DB` | `SERVICENOW_FIRESTORE_COLLECTION` |
| `pcard` | `SERVICENOW_INDEX_ENDPOINT_ID` | `PCARD_DEPLOYED_INDEX_ID` | `PCARD_FIRESTORE_DB` | `PCARD_FIRESTORE_COLLECTION` |
| `bulk_exp` | `SERVICENOW_INDEX_ENDPOINT_ID` | `BULK_EXP_DEPLOYED_INDEX_ID` | `BULK_EXP_FIRESTORE_DB` | `BULK_EXP_FIRESTORE_COLLECTION` |
| `apac_payroll` | `INDEX_ENDPOINT_ID` | `APAC_PAYROLL_DEPLOYED_INDEX_ID` | `HD_SKYE_FIRESTORE_DB` | `APAC_PAYROLL_FIRESTORE_COLLECTION` |

### Enabling/Disabling Index Groups

| Env Variable | Default | Description |
|---|---|---|
| `ENABLED_INDEX_GROUPS` | `""` (all) | Comma-separated list of group names to enable. Empty = all registered groups. |

Example:
```bash
# Only enable ServiceNow and P-Card
ENABLED_INDEX_GROUPS="servicenow_kb,pcard"

# Enable all (leave empty or omit)
ENABLED_INDEX_GROUPS=""
```

**Config location**: `config.py:220-234`

---

## Agent Variant System

Variants define named agent configurations, each with its own set of index groups and priority boosting rules.

### Variant Configuration Env Vars

Each variant has two env vars:

| Pattern | Description |
|---|---|
| `VARIANT_{NAME}_INDEX_GROUPS` | Comma-separated index group names this variant searches |
| `VARIANT_{NAME}_PRIORITY_GROUPS` | Comma-separated index groups whose results get a score boost |

### Defined Variants

| Variant | Display Name | Index Groups | Priority Groups | Dedicated Endpoint |
|---|---|---|---|---|
| `main` | Skye HR Agent | `servicenow_kb,main,apac_payroll,pcard,bulk_exp` | *(none)* | `POST /chat` |
| `pcard` | Skye P-Card Agent | `servicenow_kb,pcard` | `pcard` | `POST /pcard/chat` |
| `bulk_expense` | Skye Bulk Expense Agent | `servicenow_kb,bulk_exp` | `bulk_exp` | `POST /bulk-expense/chat` |
| `payroll` | Skye Payroll Agent | `servicenow_kb,apac_payroll` | `apac_payroll` | `POST /payroll/chat` |

### Variant Env Vars (Full List)

| Env Variable | Default | Description |
|---|---|---|
| `VARIANT_MAIN_INDEX_GROUPS` | `servicenow_kb,main,apac_payroll,pcard,bulk_exp` | Index groups for main variant |
| `VARIANT_MAIN_PRIORITY_GROUPS` | `""` | Priority boost groups for main (none = equal weight) |
| `VARIANT_PCARD_INDEX_GROUPS` | `servicenow_kb,pcard` | Index groups for P-Card variant |
| `VARIANT_PCARD_PRIORITY_GROUPS` | `pcard` | Priority boost for P-Card results |
| `VARIANT_BULK_EXPENSE_INDEX_GROUPS` | `servicenow_kb,bulk_exp` | Index groups for Bulk Expense variant |
| `VARIANT_BULK_EXPENSE_PRIORITY_GROUPS` | `bulk_exp` | Priority boost for Bulk Expense results |
| `VARIANT_PAYROLL_INDEX_GROUPS` | `servicenow_kb,apac_payroll` | Index groups for Payroll variant |
| `VARIANT_PAYROLL_PRIORITY_GROUPS` | `apac_payroll` | Priority boost for APAC Payroll results |
| `DEFAULT_VARIANT` | `main` | Fallback variant when none specified |
| `INDEX_PRIORITY_BOOST` | `0.20` | Score boost applied to priority group results during reranking |

### How Variant Selection Works

1. **Dedicated endpoint**: `/pcard/chat` forces `variant="pcard"` regardless of request body
2. **Request body**: `POST /chat` with `{"variant": "pcard"}` in the body
3. **Default**: If no variant specified, uses `DEFAULT_VARIANT` (default: `main`)

### How Priority Boosting Works

During reranking (`reranking_agent.py`):

1. **Collection-based boost**: Results from priority collections get `+INDEX_PRIORITY_BOOST` added to their `rank_score`. This causes variant-specific results to float above generic ServiceNow results of similar semantic relevance.

2. **Category boost** (new): Results matching the variant's priority `category` values also get boosted, even if they come from shared collections like `servicenow_kb`. This catches P-Card KB articles in the ServiceNow collection that have `category="P Card"`.

   | Variant | Priority Collections | Priority Categories |
   |---|---|---|
   | `main` | *(none)* | *(none)* |
   | `pcard` | `p-card_policy` | `"P Card"` |
   | `bulk_expense` | `bulk-expense` | `"Bulk Expense"` |
   | `payroll` | `apac-payroll-chunks` | *(none — isolated by collection/DB)* |

3. Both boosts are applied BEFORE region filtering and holiday/pcard prioritization.
4. A result already boosted by collection priority is NOT double-boosted by category.

**Config location**: `config.py:237-420`, `main.py:108-167`

---

## BigQuery Tables

| Env Variable | Default | Description |
|---|---|---|
| `BQ_CREDENTIALS_PATH` / `BIGQUERY_APPLICATION_CREDENTIALS` | `auth2.json` | Path to BQ service account JSON |
| `BQ_USER_SNAPSHOT_TABLE` | `hd-onedata-prod.hd1d_consumption_hds_hr.hr_worker_snapshot_ds_hyperion_feed_hds_erp_vw` | Worker snapshot (manager check, reportees, countries) |
| `BQ_MANAGER_SNAPSHOT_TABLE` | `hd-onedata-prod.hd1d_consumption_hds_hr.hr_worker_snapshot_manager_okta_hds_erp_vw` | Manager snapshot view |
| `BQ_GBLC_LIFECYCLE_TABLE` | `hd-onedata-prod.hd1d_consumption_gblc_hr.hr_worker_lifecycle_assignment_gblc_erp_vw` | Worker lifecycle (VP/exec check, user details) |
| `BQ_GBLC_JOB_MAP_TABLE` | `hd-onedata-prod.hd1d_mdm.hr_hinext_gblc_job_family_map` | Job family mapping |

### What Each Table Provides

| Table | Used For | Fields Used |
|---|---|---|
| Worker Snapshot | Manager detection, reportee list, direct report countries | `MANAGER_EMAIL`, `EMPLOYEE_NAME`, `EMAIL_ADDRESS`, `EMPLOYING_COUNTRY`, `WORK_LOCATION_NAME`, `LDAP_ID` |
| Worker Lifecycle | VP/exec role detection, user details (country, title, dept) | `Email Address`, `First Name`, `Last Name`, `Work Country Desc`, `Title`, `HR Job Level Code`, `Oracle Location`, `Oracle Department` |

**Config location**: `config.py:51-66`, `tools/bq_tools.py:24-30`

---

## Redis Configuration

| Env Variable | Default (Dev) | Default (Prod) | Description |
|---|---|---|---|
| `REDIS_HOST` | `localhost` | `10.180.68.37` | Redis server host |
| `REDIS_PORT` | `6379` | `6379` | Redis server port |
| `REDIS_PASSWORD` | `""` | `""` | Redis auth password |
| `REDIS_KEY_PREFIX` | `skye:` | `skye:` | Namespace prefix for all Redis keys |

**Config location**: `config.py:73-78`

---

## Cache TTLs & Thresholds

| Env Variable | Default | Description |
|---|---|---|
| `CACHE_TTL_SEARCH` | `3600` (1h) | Vector search result cache |
| `CACHE_TTL_ANSWER` | `10800` (3h) | Answer cache (exact + semantic) |
| `CACHE_TTL_HISTORY` | `86400` (24h) | Conversation history |
| `CACHE_TTL_SESSION` | `86400` (24h) | Session context |
| `CACHE_TTL_PROFILE` | `86400` (24h) | User profile cache |
| `CACHE_TTL_COUNTER` | `604800` (7d) | Semantic cache counter |
| `CACHE_TTL_COUNTRY` | `2592000` (30d) | Resolved country name cache |
| `SIMILARITY_THRESHOLD` | `0.95` | Cosine similarity threshold for semantic cache hit |

### Cache Key Patterns

| Key Pattern | Includes Variant? | Description |
|---|---|---|
| `answer_cache:{variant}:{query_en}:{region}` | Yes | Exact-match answer cache |
| `search:{tenant}:{variant_tag}:{query}:{top_k}` | Yes | Vector search result cache |
| `sem_cache:{region}:{role_key}:{idx}` | No (role-bucketed) | Semantic similarity cache entries |
| `history:{session_id}` | No | Conversation history |
| `session:{session_id}:latest` | No | Full session context |
| `user_profile_v1:{email}` | No | Cached BQ user profile |
| `reportees_list_v3:{email}:{is_vp}` | No | Cached reportee list |
| `resolved_country_v3:{raw}` | No | Resolved country name |

**Config location**: `config.py:109-134`

---

## Access Control & Super Admin

| Env Variable | Default | Description |
|---|---|---|
| `SUPER_ADMIN_EMAILS` | `""` | Comma-separated list of super admin emails (bypass all access control) |
| `REGION_FILTER_MODE` | `all` | Controls which users get region-based result filtering during reranking. See below. |

### Region Filter Mode

The `REGION_FILTER_MODE` env var controls whether region-based boosting and penalties are applied during reranking. This is **separate from access control** — a user denied access to a region by `check_access` is still denied regardless of this setting.

| Mode | Who Gets Region Filtering | Description |
|---|---|---|
| `all` | Everyone | All users see region-boosted results (default) |
| `managers_up` | Manager, VP, Executive, Super Admin | Only leadership roles get region-scoped results; regular employees see all regions equally |
| `vp_up` | VP, Executive, Super Admin | Only senior leadership gets region filtering |
| `none` | Nobody | Disable region filtering entirely — all results ranked by semantic relevance only |

**When region filtering is DISABLED for a user:**
- No region boost (+0.15) is applied to chunks matching the user's region
- No other-region penalty / exclusion is applied
- Results are ranked purely by semantic relevance + variant-specific boosts
- Holiday isolation still applies (it's content-based, not region-based)

**When region filtering is ENABLED (current behavior):**
- Chunks matching the user's region get +0.15 score boost
- Chunks from other regions are excluded from the result set
- Global chunks (no region tag) are always included

**Implementation**: `config.py` → `should_apply_region_filter(roles)`, called from `orchestrator.py` after access control resolves the user's roles. The boolean is passed to `rerank_and_filter()`.

**Config location**: `config.py`, `agents/orchestrator.py`, `agents/reranking_agent.py`

### Access Control Matrix

| User Type | Home Country | Allowed Locations | Employee Lookup |
|---|---|---|---|
| Regular Employee | From BQ/Teams | Global + own country | DENIED |
| Manager | From BQ/Teams | Global + own + reports' countries | Own reportees (1 level) |
| VP/Executive | From BQ/Teams | Global + own + reports' countries | Reportees (2 levels) |
| Super Admin | Any | ALL (bypass) | Normal role rules apply* |
| `data_scope=global` | Any | ALL (bypass) | Normal role rules apply |

*Super admins get full POLICY access across all regions but employee lookup still follows role-based rules.

**Config location**: `config.py:80-85`, `agents/access_control_agent.py`

---

## Application & Model Settings

| Env Variable | Default | Description |
|---|---|---|
| `TENANT` | `hd-skye` | Tenant identifier (used in cache keys) |
| `LLM_MODEL` | `gemini-2.0-flash` | Primary LLM model for generation, understanding, etc. |
| `EMBEDDING_MODEL` | `text-embedding-004` | Vertex AI embedding model |

**Config location**: `config.py:137-139`

---

## Document AI

| Env Variable | Default | Description |
|---|---|---|
| `DOCUMENT_AI_OCR_ID` | — | Document AI OCR processor ID |
| `DOCUMENT_AI_FORM_PARSER_ID` | — | Document AI form parser ID |
| `DOCUMENT_AI_LOCATION` | `us` | Document AI processor location |

**Config location**: `config.py:68-71`

---

## GCS (Cloud Storage)

| Env Variable | Default | Description |
|---|---|---|
| `GCS_BUCKET_NAME` / `HD_SKYE_GCS_BUCKET_NAME` | — | GCS bucket for RAG documents |
| `HD_SKYE_DOCUMENTS_PREFIX` | `hd-skye-2.0/Documents/` | GCS prefix for document storage |
| `HD_SKYE_SERVICENOW_KB_PREFIX` | `servicenow_kb_extraction/` | GCS prefix for ServiceNow KB extractions |

**Config location**: `config.py:20-21, 29-31`

---

## ServiceNow

| Env Variable | Default | Description |
|---|---|---|
| `SERVICENOW_PORTAL_URL` | `https://hitachivantara.service-now.com/asknow` | Base URL for KB article links |

**Config location**: `config.py:25-28`

---

## API Endpoints

### Chat Endpoints

| Method | Path | Variant | Description |
|---|---|---|---|
| `POST` | `/chat` | Request body (`variant` field, default: `main`) | Main chat — searches based on variant |
| `POST` | `/pcard/chat` | `pcard` (hardcoded) | P-Card specialist agent |
| `POST` | `/bulk-expense/chat` | `bulk_expense` (hardcoded) | Bulk Expense specialist agent |
| `POST` | `/payroll/chat` | `payroll` (hardcoded) | Payroll specialist agent |

### Request Body (all chat endpoints)

```json
{
  "question": "string (required)",
  "session_id": "string (default: 'default')",
  "teams_metadata": {
    "email": "user@example.com",
    "country": "IN",
    "usageLocation": "IN"
  },
  "data_scope": "regional | global",
  "region": "string (optional)",
  "variant": "main | pcard | bulk_expense | payroll"
}
```

### Other Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/new-chat` | Clear conversation history for a session |
| `POST` | `/feedback` | Submit feedback (helpful/not, comment) |
| `GET` | `/healthz` | Health check |
| `GET` | `/documents/{filename}` | Serve document (ServiceNow KB HTML or GCS signed URL) |
| `GET` | `/agents` | List all registered agents |
| `GET` | `/variants` | List all variant configurations and their active index groups |
| `GET` | `/cache/status` | Redis cache overview (all key categories) |
| `GET` | `/cache/session/{session_id}` | Session-specific cache data |
| `GET` | `/cache/user/{email}` | User-specific cache data |

---

## Deploy Configuration

### Files

| File | Purpose |
|---|---|
| `deploy.config.yaml` | Base deployment config (Cloud Run settings) |
| `env/prod.yaml` | Production environment overrides |
| `env/prod-poc.yaml` | POC/staging environment overrides |

### Cloud Run Settings

| Setting | prod | prod-poc |
|---|---|---|
| `service_name` | `hd-hd1ai-skye-agent` | `hd-hd1ai-skye-agent-ui` |
| `memory` | `2Gi` | `2Gi` |
| `cpu` | `2` | `2` |
| `min_instances` | `1` | `1` |
| `max_instances` | `10` | `10` |
| `timeout` | `300s` | `300s` |
| `port` | `8000` | `8000` |

---

## Region & Category Metadata

### Status: IMPLEMENTED (retrieval + reranking agents updated)

The ML engineering team has added rich metadata fields to Firestore chunk documents in the new-schema collections (ServiceNow KB, P-Card, Bulk Expense). The retrieval and reranking agents now propagate and use these fields.

### Key Fields

| Field | Primary Use | Reliability |
|---|---|---|
| `category_id` | Variant-specific boosting, topic classification | 100% populated in new-schema |
| `region` | Geographic filtering (complements `country`) | ~54% populated (null = GLOBAL) |
| `policy_domain` | Domain-level categorization | 100% populated in new-schema |
| `country` | Country-level filtering | 100% populated |

### `category` — The Variant Boosting Field

The `category` field in the `servicenow_kb` collection identifies chunks that belong to a specific policy domain. It is used for **variant-specific boosting** — when the P-Card or Bulk Expense variant is active, results from `servicenow_kb` with a matching `category` get score-boosted.

Known `category` values in `servicenow_kb`:
- `"P Card"` — P-Card policy chunks
- `"Bulk Expense"` — Bulk Expense policy chunks
- `None` — general HR/IT/security/compliance content (majority of chunks)

**Note**: Payroll is in a separate index/Firestore DB (`apac-payroll-chunks`) so it doesn't need category-based boosting — it's already isolated by the variant's index group selection.

### `category_id` — Topic Classification

`category_id` is 100% populated in new-schema collections and provides finer-grained topic classification. Currently propagated by the retrieval agent but not used for filtering/boosting.

Known `category_id` values:
- `hr_policy`, `security`, `compliance`, `benefits_policy`, `leave_policy`
- `procurement`, `travel_expense`, `general`, `it_support`
- `p_card_policy` (P-Card collection)
- `bulk_expense` (Bulk Expense collection)

### `region` — Geographic Region Field

Values: `APAC`, `EMEA`, `Americas`, or null (= GLOBAL).

The reranking agent now uses `region` as the **highest-priority signal** for geographic filtering, before falling back to `country` field, filename regex, and text content matching.

**Region detection priority in reranking_agent.py**:
1. Firestore `region` field (most reliable)
2. Firestore `country` field
3. Filename regex matching
4. Chunk text content scanning

### `policy_domain` — Domain Classification

Values: `hr`, `security`, `compliance`, `finance`, `general`, `it`.

Currently propagated but not used for filtering. Could be used in the future for domain-specific agent routing.

### TODO: Region-Based Access/Filtering for Workers

> **NEEDS CONFIRMATION**: The logic for region-based drill downs/filters is pending:
> - Should region-based filtering apply to ALL employees or only managers/VPs?
> - Should an employee in APAC region automatically get APAC-specific policy results?
> - Should managers see policies for all regions where their reportees are located?
> - How does region filtering interact with the existing country-based access control?
>
> **Action**: Confirm the region-based filtering business logic before implementation.

### What's Implemented vs What's Pending

| Component | Status | Details |
|---|---|---|
| Firestore metadata fields | Done (by ML team) | `region`, `category_id`, `policy_domain`, etc. populated in new-schema collections |
| `retrieval_agent.py` — field propagation | Done | Extracts 10+ new fields from Firestore docs into result dicts |
| `reranking_agent.py` — `region` field for filtering | Done | Uses `region` as top-priority signal in geographic classification |
| `reranking_agent.py` — `category` boosting | Done | Variant-specific boosting by `category` field ("P Card", "Bulk Expense") |
| `config.py` — `INDEX_GROUP_CATEGORIES` map | Done | Maps index groups to their `category` values |
| `config.py` — `get_variant_priority_categories()` | Done | Resolves variant → category value set |
| `orchestrator.py` — passes `priority_categories` | Done | All 3 `rerank_and_filter` call sites updated |
| Region-based access control for workers | **PENDING** | Blocked on business logic confirmation (see TODO above) |
| Old-schema collections — metadata backfill | **NOT STARTED** | `hd-skye-2-0-chunks` and `apac-payroll-chunks` still use old schema |
