# HD SKYE Agent — Architecture & Knowledge Transfer Document

> **Prepared for KT Session** | Last Updated: April 2026
> **System:** HD SKYE Agentic HR RAG Agent v2.0
> **Purpose:** AI-powered HR policy assistant for Hitachi Digital employees

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Agent Variants](#3-agent-variants-main-pcard-bulk-expense-payroll)
4. [Agentic Pipeline — Main Orchestrator](#4-agentic-pipeline--main-orchestrator)
5. [P-Card Pipeline — Dedicated Orchestrator](#5-p-card-pipeline--dedicated-orchestrator)
6. [Individual Agent Breakdown](#6-individual-agent-breakdown)
7. [Tools Layer](#7-tools-layer)
8. [Data Architecture & Index Groups](#8-data-architecture--index-groups)
9. [ServiceNow Integration](#9-servicenow-integration)
10. [Document Ingestion Pipeline](#10-document-ingestion-pipeline)
11. [Caching Strategy](#11-caching-strategy)
12. [Access Control & Role-Based Architecture](#12-access-control--role-based-architecture)
13. [Infrastructure & Deployment](#13-infrastructure--deployment)
14. [API Endpoints](#14-api-endpoints)
15. [Key Configuration Reference](#15-key-configuration-reference)

---

## 1. System Overview

HD SKYE is an **agentic Retrieval-Augmented Generation (RAG)** system that serves as an HR policy assistant for Hitachi Digital employees. It answers HR-related questions (leave policies, payroll, travel, benefits, etc.) by:

1. **Understanding** the user's question (language, intent, region, role)
2. **Retrieving** relevant policy documents from vectorized knowledge bases
3. **Generating** contextual, role-aware, region-specific answers using Google Gemini LLM
4. **Translating** responses to the user's language (supports 15+ languages)

### Key Capabilities
- **Multi-language support**: Detects language, translates queries to English for processing, translates answers back
- **Role-aware responses**: Different answers for Employee, Manager, VP, Executive, HR/Finance
- **Region-specific policies**: Serves country-specific HR policies (India, Japan, US, Germany, etc.)
- **Multi-variant agents**: Specialized agents for P-Card, Bulk Expense, Payroll alongside the main HR agent
- **Conversation memory**: Multi-turn conversations with context preservation
- **Employee lookup**: Managers/VPs can ask about their reportees' policies
- **ServiceNow KB integration**: Ingests and serves ServiceNow knowledge base articles

### Tech Stack
| Component | Technology |
|-----------|-----------|
| **Backend** | Python / FastAPI |
| **Frontend** | React (Vite + Bun) |
| **LLM** | Google Gemini 2.0 Flash (Vertex AI) |
| **Vector DB** | Google Vertex AI Matching Engine |
| **Document Store** | Google Cloud Firestore (multi-database) |
| **Embeddings** | Vertex AI text-embedding-004 |
| **Cache** | Redis (conversation history + semantic cache) |
| **User Data** | BigQuery (hd-onedata-prod) |
| **Object Storage** | Google Cloud Storage (GCS) |
| **OCR** | Google Document AI |
| **Translation** | Google Cloud Translation API + Gemini LLM |
| **Agent Framework** | Google ADK (Agent Development Kit) |
| **Deployment** | Cloud Run (Docker, 2 CPU / 2 GB) |

---

## 2. High-Level Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                         FRONTEND (React SPA)                         │
│               Served from same origin via FastAPI static              │
└────────────────────────────────┬──────────────────────────────────────┘
                                 │ HTTP POST
                                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│                       FastAPI Backend (main.py)                       │
│                                                                       │
│   /chat ───────────────────► Main Orchestrator (process_query)        │
│   /pcard/chat ─────────────► P-Card Orchestrator (process_pcard_query)│
│   /bulk-expense/chat ──────► Main Orchestrator (variant=bulk_expense) │
│   /payroll/chat ───────────► Main Orchestrator (variant=payroll)      │
│   /feedback ───────────────► Feedback Agent                           │
│   /new-chat ───────────────► Cache Clear                              │
│   /documents/{file} ───────► GCS Signed URL / KB Renderer             │
└────────────────────────────────┬──────────────────────────────────────┘
                                 │
           ┌─────────────────────┼─────────────────────┐
           ▼                     ▼                     ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐
│  ORCHESTRATOR    │  │  AGENT MODULES   │  │  EXTERNAL SERVICES       │
│  (Pipeline Ctrl) │  │  (15 Agents)     │  │                          │
│                  │  │                  │  │  • Vertex AI (LLM)       │
│  • Phase control │  │  • Query Under.  │  │  • Matching Engine       │
│  • Thread pools  │  │  • Guardrails    │  │  • Firestore (5 DBs)    │
│  • Cache checks  │  │  • Access Ctrl   │  │  • BigQuery (user data)  │
│  • Role routing  │  │  • Retrieval     │  │  • Redis (cache)         │
│  • Fallback      │  │  • Reranking     │  │  • GCS (documents)       │
│                  │  │  • Generation    │  │  • Cloud Translation     │
│                  │  │  • Translation   │  │  • Document AI (OCR)     │
│                  │  │  • Post Valid.   │  │  • ServiceNow Portal     │
└──────────────────┘  │  • Caching       │  └──────────────────────────┘
                      │  • Feedback      │
                      │  • Observability │
                      │  • Embedding     │
                      │  • Ingestion     │
                      │  • Parsing/Chunk │
                      │  • Indexing      │
                      └──────────────────┘
```

---

## 3. Agent Variants (Main, PCard, Bulk Expense, Payroll)

SKYE supports **4 named agent variants**, each configured with a different combination of vector index groups. This allows specialized agents to focus on specific policy domains while sharing the same codebase.

### Variant Registry

| Variant | Display Name | Index Groups Searched | Priority Groups | Dedicated Route |
|---------|-------------|----------------------|-----------------|-----------------|
| **main** | Skye HR Agent | `servicenow_kb`, `main`, `apac_payroll`, `pcard`, `bulk_exp` | None (all equal) | `POST /chat` |
| **pcard** | Skye P-Card Agent | `servicenow_kb`, `pcard` | `pcard` (boosted) | `POST /pcard/chat` |
| **bulk_expense** | Skye Bulk Expense Agent | `servicenow_kb`, `bulk_exp` | `bulk_exp` (boosted) | `POST /bulk-expense/chat` |
| **payroll** | Skye Payroll Agent | `apac_payroll` | `apac_payroll` (boosted) | `POST /payroll/chat` |

### How Variants Work

```
Request arrives with variant="bulk_expense"
    │
    ├── 1. get_variant_config("bulk_expense")
    │       → Returns AgentVariantConfig with index_groups=["servicenow_kb", "bulk_exp"]
    │
    ├── 2. get_variant_index_groups("bulk_expense")
    │       → Resolves to actual IndexGroupConfig objects (endpoint IDs, Firestore DBs)
    │       → Only returns groups that exist in INDEX_GROUP_REGISTRY
    │
    ├── 3. Retrieval searches ONLY those 2 indexes
    │
    ├── 4. get_variant_priority_collections("bulk_expense")
    │       → Returns {"bulk-expense"} (Firestore collection name)
    │
    └── 5. Reranking applies INDEX_PRIORITY_BOOST (+0.20) to results
            from the "bulk-expense" collection
```

### Variant-Specific Behavior

- **Main variant**: Searches ALL indexes, no priority boost. Full pipeline with access control, role rewriting, region filtering.
- **P-Card variant**: Has its own **dedicated orchestrator** (`pcard_orchestrator.py`) with strict rules:
  - Gold-source filtering (only PNG table + conditionally included PDFs)
  - No access control, no region filtering
  - Custom LLM prompt (Procurement Card Policy Expert)
  - Fallback to `CorporateCard@hitachidigital.com`
- **Bulk Expense variant**: Uses main orchestrator with `variant="bulk_expense"`, prioritizes bulk expense index.
- **Payroll variant**: Uses main orchestrator with `variant="payroll"`, searches only APAC payroll index.

---

## 4. Agentic Pipeline — Main Orchestrator

The main orchestrator (`agents/orchestrator.py` → `process_query()`) uses **aggressive parallelization** with `ThreadPoolExecutor` to overlap slow operations. The pipeline is divided into 5 phases:

### Pipeline Flow Diagram

```
TIME ──────────────────────────────────────────────────────────────────────►

PHASE 1: Launch ALL independent work at t=0
┌─────────────────────────────────────────────────────────────────────────┐
│ Thread 1: Fetch conversation history     (instant, ~10ms)              │
│ Thread 2: Home-location access check     (runs in background)          │
│ Thread 3: Speculative vector retrieval   (runs in background, ~2-3s)   │
│ Thread 4: Generate query embedding       (for semantic cache, ~0.5s)   │
│                                                                         │
│ Main thread: Wait ONLY for history → run Query Understanding (~1-2s)  │
│    • Detect language, translate to English                              │
│    • Classify intent (concise/detailed)                                │
│    • Extract target region                                              │
│    • Detect employee names                                              │
│    • Expand abbreviations                                               │
│    • Rewrite follow-up questions                                        │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
         ┌── Translation-only request? → Return translated last answer (shortcut)
         │
         ▼
GUARDRAILS (inline, fast)
┌─────────────────────────────────────────────────────────────────────────┐
│ • Greeting/thank-you detection → canned response                       │
│ • Hitachi Vantara (HV) blocking → disclaimer                           │
│ • P-Card permission gating → VP/Exec/SuperAdmin only                   │
│ • If blocked → return early, cancel background threads                 │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
CACHE CHECK (exact match → semantic similarity)
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. Exact match: answer_cache:{variant}:{query_en}:{region}            │
│ 2. Semantic similarity: cosine sim ≥ 0.85 on cached embeddings         │
│    - Role-aware bucketing (employee/manager/vp/executive)              │
│    - Intent validation (reject if cached intent ≠ current intent)      │
│ 3. Skip denial/error cached answers (never serve stale denials)        │
│ • If cache hit → translate if needed → return early                    │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
PHASE 2: Access Control
┌─────────────────────────────────────────────────────────────────────────┐
│ • Employee lookup (if mentioned): find in reportees, validate access   │
│ • Access check: role-based region permission matrix                    │
│ • If denied → fall back to user's home region (soft deny)              │
│ • Role-based query rewriting (augment search with role terms)          │
│ • Region augmentation: append "in {target_region}" to search query     │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
PHASE 3: Collect Retrieval Results
┌─────────────────────────────────────────────────────────────────────────┐
│ • Collect speculative retrieval (launched at t=0, should be ready)     │
│ • If holiday/translated/region-specific: launch REFINED retrieval     │
│   with English search_query + region context → merge results           │
│ • Check if P-Card authorized → branch to P-Card sub-pipeline          │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
RERANKING + GENERATION (sequential — each depends on previous)
┌─────────────────────────────────────────────────────────────────────────┐
│ RERANKING:                                                              │
│   • HV source filtering                                                │
│   • Index priority boost (variant-specific)                            │
│   • Category boost (P-Card, Bulk Expense)                              │
│   • Holiday/PCard result prioritization                                │
│   • Region filtering (country matching, filename, metadata, text scan) │
│   • OPCO entity classification (HDS, GlobalLogic, HD, Global)          │
│                                                                         │
│ GENERATION (Gemini 2.0 Flash):                                         │
│   • 12-rule master prompt with role context, region, OPCO labels       │
│   • Concise vs Detailed mode based on intent                           │
│   • If "no info" fallback → retry with broader search (top_k=50)      │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
PHASE 5: Parallel Post-Processing
┌─────────────────────────────────────────────────────────────────────────┐
│ Thread 1: Translation — translate English answer to user's language    │
│ Thread 2: Post-Validation — attribute sources, detect no-info          │
│ Thread 3: Follow-up Suggestions — generate 3 suggested questions       │
│                                                                         │
│ Then:                                                                   │
│ • Build source links (ServiceNow URLs or /documents/ signed URLs)      │
│ • Cache answer (exact + semantic, 3-hour TTL)                          │
│ • Cache session context (24-hour TTL)                                  │
│ • Save conversation turn to Redis                                      │
└─────────────────────────────────────────────────────────────────────────┘
                    │
                    ▼
              RETURN RESPONSE
              {answer, sources, source_links, suggested_questions}
```

### Parallelization Strategy

The orchestrator achieves ~2x speedup by running independent operations in parallel:

| Sequential (old) | Parallel (current) | Savings |
|---|---|---|
| Understanding (2s) → Retrieval (3s) | Understanding ‖ Speculative Retrieval | ~3s saved |
| Translation → Validation → Suggestions | Translation ‖ Validation ‖ Suggestions | ~2-3s saved |
| History → Home-loc → Embedding | All at t=0 in threads | ~1s saved |

**Speculative Retrieval**: Retrieval starts at t=0 using the raw question, before understanding completes. If the question is non-English or region-specific, a refined retrieval runs after understanding completes with the corrected English query + region context, and results are merged.

---

## 5. P-Card Pipeline — Dedicated Orchestrator

The P-Card variant has its **own orchestrator** (`agents/pcard_orchestrator.py`) with fundamentally different logic:

### P-Card Pipeline Differences from Main

| Aspect | Main Pipeline | P-Card Pipeline |
|--------|-------------|-----------------|
| Access Control | Full role-based | **None** — open to all |
| Region Filtering | Country-specific | **None** — global policy |
| Role Rewriting | Role-aware query augmentation | **None** |
| Source Filtering | HV filter + region filter | **Strict gold-source only** |
| LLM Prompt | General HR policy expert | **Procurement Card Policy Expert** |
| Fallback | AskNow + HRBP redirect | `CorporateCard@hitachidigital.com` |
| Footnote Handling | N/A | `(*)` → Gift Policy, `(**)` → 3P Policy |

### P-Card Gold Source Rule

```
1. ONLY use PCard_Allowable_NonAllowable.png (the master table)
2. If the table has (*) marker → include Global Employee Gift Policy PDF
3. If the table has (**) marker → include Third Party Gifts/Travel Policy PDF
4. NO other sources allowed
5. If no PNG chunk found → return fallback email message
```

### P-Card Pipeline Phases

```
Phase 1: Translate ‖ History ‖ Language Detection (parallel)
    ↓
Greeting shortcut (if applicable)
    ↓
Phase 2: Speculative Retrieval ‖ Translation-request check (parallel)
    ↓
Cache check (exact match only, no semantic cache)
    ↓
Translation-request handling (retranslate previous answer)
    ↓
Phase 3: Collect retrieval results
    ↓
Phase 4: P-Card strict filtering (gold-source + footnote)
    ↓
Shared pipeline: Generation → Validation ‖ Translation ‖ Suggestions (parallel)
```

### P-Card Access from Main Pipeline

When a user sends a P-Card query to `/chat` (main route), the guardrails agent detects it and checks permissions:
- If user is **VP/Executive/SuperAdmin/HR-Finance** → P-Card sub-pipeline runs inside the main orchestrator
- If user is a **regular employee** → denied with a role-based message

---

## 6. Individual Agent Breakdown

### 6.1 Query Understanding Agent (`query_understanding_agent.py`)

**Purpose:** First stage — parses raw user input into structured understanding.

| Output Field | Description |
|---|---|
| `query_en` | English translation of the query |
| `response_language_code` | Target language for response (e.g., "ja", "ta") |
| `is_greeting` | True if query is a greeting/thank-you |
| `is_followup` | True if query depends on conversation history |
| `intent` | "concise" or "detailed" |
| `search_query` | Expanded/rewritten query for vector search |
| `target_region` | Country the query is about (e.g., "India", "Japan") |
| `mentioned_employee` | Employee name if asking about a reportee |

**Key Algorithms:**
- **Single combined LLM call**: Does follow-up detection + intent classification + query rewriting + geographic extraction + employee detection + translation correction in ONE Gemini call (previously 5+ calls)
- **Query expansion dictionary**: 150+ entries mapping HR abbreviations ("wfh" → "work from home", "pto" → "paid time off")
- **Follow-up rewriting**: Converts context-dependent queries to standalone form

### 6.2 Guardrails Agent (`guardrails_agent.py`)

**Purpose:** Pre-flight permission gate — early exit for blocked/greeting queries.

**Guards (sequential):**
1. **Greeting guard** → canned response, skip entire pipeline
2. **HV blocking** → Hitachi Vantara queries get disclaimer
3. **P-Card gating** → checks VP/Exec/SuperAdmin/HR-Finance role via BQ

### 6.3 Access Control Agent (`access_control_agent.py`)

**Purpose:** Role-based region access matrix.

**Access Matrix:**

| Role | Can Access | Employee Lookup |
|------|-----------|-----------------|
| Regular Employee | Global + own country | ❌ Denied |
| Manager | Global + own + direct reports' countries | ✅ 1 level deep |
| VP / Executive | Global + own + all reports' countries | ✅ 2 levels deep |
| Super Admin | ALL regions (bypass) | ❌ (unless also Manager/VP) |
| HR / Finance | ALL regions | Standard per other role |
| data_scope=global | ALL regions (override) | Standard per other role |

**Country Resolution:**
- Fast static map for 99% of cases (e.g., "Hyderabad" → "India")
- LLM fallback for unknown locations (cached for 30 days)

### 6.4 Retrieval Agent (`retrieval_agent.py`)

**Purpose:** Vector similarity search against Matching Engine indexes.

**Key Features:**
- **Multi-index parallel search**: Groups indexes by endpoint, searches all on each endpoint in parallel via ThreadPoolExecutor
- **Rich metadata extraction**: country, region, category, document_type, servicenow_url, etc.
- **Semantic re-ranking**: Uses Vertex AI Ranking API (`semantic-ranker-512@latest`) for secondary reranking
- **Variant-aware caching**: 1-hour TTL with variant fingerprints to prevent cross-variant cache leaks

### 6.5 Reranking Agent (`reranking_agent.py`)

**Purpose:** Post-retrieval multi-stage ranking and filtering.

**Stages:**
1. **HV Filtering** — Remove Hitachi Vantara results
2. **Index Priority Boost** — +0.20 score for variant's priority index groups
3. **Category Boost** — Finer-grained boost for specific Firestore categories (e.g., "P Card" in ServiceNow)
4. **Holiday/PCard Prioritization** — Topic-specific result sorting
5. **Region Filtering** — Multi-method country detection:
   - Firestore metadata → Filename patterns → Text scanning → HR system name mapping
   - Word-boundary matching to prevent false positives ("us" in "business")

### 6.6 Generation Agent (`generation_agent.py`)

**Purpose:** Final answer generation using Gemini with sophisticated prompt engineering.

**12 Critical Rules in Master Prompt:**
1. Answer primary question FIRST (binary questions answered immediately)
2. User region + entity prioritization
3. OPCO labeling for each policy (HDS, GlobalLogic, HD)
4. HV exclusion (only when explicitly queried)
5. No greetings in answers — go straight to content
6. Specific contacts over generic "contact HR"
7. Honest gaps with fallback (AskNow + HRBP redirect)
8. Professional tone
9. STRICT country filtering (never cross-list multi-country policies)
10. Answer in English (translation handled separately)
11. Answer PRECISION (match the specific question)
12. Source quality (prefer specific/recent documents)

**Modes:** Concise (ultra-brief) vs Detailed (thorough with definitions, eligibility, rules, dates, processes, exceptions)

### 6.7 Translation Agent (`translation_agent.py`)

**Purpose:** Bidirectional translation with Markdown preservation.

- **LLM-based translation**: Uses Gemini to translate while preserving Markdown formatting
- **Translation-only shortcut**: If user asks "translate to Tamil", retranslates previous answer without re-running pipeline
- **Fallback**: If LLM fails, falls back to Google Cloud Translation API

### 6.8 Post-Validation Agent (`post_validation_agent.py`)

**Purpose:** Source attribution and quality validation after generation.

**Multi-stage validation:**
1. Detect chitchat/greeting responses → no sources needed
2. Detect "no information" fallback phrases
3. Detect short redirect-only answers
4. **LLM source attribution**: Ask Gemini which documents contributed to the answer
5. **Country mismatch filtering**: Remove sources about wrong countries
6. Fallback source extraction (score-based) if LLM attribution fails

**Source URL Mapping:**
- ServiceNow articles → `servicenow_url` from Firestore or constructed portal link
- Regular documents → `/documents/{filename}` → GCS signed URL redirect

### 6.9 Supporting Agents

| Agent | Purpose |
|-------|---------|
| **Caching Agent** | Manages conversation history in Redis (save/retrieve/clear turns) |
| **Observability Agent** | Structured logging: execution timing, user context boxes, agent step logs |
| **Feedback Agent** | Stores user satisfaction ratings (helpful/unhelpful + comment) in Firestore |
| **Embedding Agent** | Wrapper around Vertex AI text-embedding-004 for vectorization |

---

## 7. Tools Layer

Tools are utility modules that interact with external services:

### 7.1 BigQuery Tools (`tools/bq_tools.py`)

**Critical for**: User identity, roles, org hierarchy.

| Function | Purpose |
|----------|---------|
| `get_user_profile()` | **Consolidated call** — roles + details + reports countries (2 parallel BQ queries, cached 24h) |
| `get_user_roles()` | Extracts role flags from profile |
| `get_reportees_for_user()` | Direct/2-level reports for employee lookup |
| `find_employee_in_reportees()` | Search by name or LDAP ID |
| `search_employee_globally()` | Search any employee (super admin only) |

**Key Tables (in `hd-onedata-prod`):**
- `hr_worker_snapshot_ds_hyperion_feed_hds_erp_vw` — Manager relationships, country/region
- `hr_worker_lifecycle_assignment_gblc_erp_vw` — Job title, level, department, HR/Finance flags

**Role Detection Logic:**
- VP: Job title contains "vice"/"vp" OR job level in E2-E5 range
- HR: Oracle Department ID ∈ {252, 994}
- Finance: Oracle Department ID ∈ {312, 994}

### 7.2 Embedding Tools (`tools/embedding_tools.py`)

- Uses Vertex AI `text-embedding-004` model
- Batches texts (100 per batch) with 3x retry and exponential backoff
- Task type: `RETRIEVAL_DOCUMENT`

### 7.3 GCS Tools (`tools/gcs_tools.py`)

- Upload/download/list blobs
- **Signed URL generation**: Smart path resolution for ServiceNow KB articles vs regular documents
- Cloud Run compatibility: IAM-based signing (signBlob API) instead of key-based

### 7.4 OPCO Tools (`tools/opco_tools.py`)

**Pure utility — no external dependencies.**

| Function | Purpose |
|----------|---------|
| `get_user_opco()` | Identify operating company from email domain |
| `is_hv_query()` / `is_hv_source()` | Detect Hitachi Vantara content |
| `is_holiday_query()` | Detect holiday calendar queries (with bereavement exclusions) |
| `is_p_card_query()` | Detect P-Card queries |
| `get_opco_entity()` | Classify document source (HDS, GL, HD, Global) |

### 7.5 Cache Tools (`tools/cache_tools.py`)

Redis wrapper with:
- Connection pooling, thread-safe operations
- Key-value, list, hash operations
- **Semantic similarity cache**: Stores (query, embedding, answer) triples, finds similar cached queries via cosine similarity (NumPy-accelerated)
- Cache stats introspection via `/cache/status` endpoint

### 7.6 KB Renderer (`tools/kb_renderer.py`)

Converts ServiceNow KB extracted markdown into styled HTML pages with Hitachi Digital branding. Handles:
- CSS stripping from extraction artifacts
- Table formatting (pipe-delimited → HTML tables)
- Deduplication of page-break fragments

---

## 8. Data Architecture & Index Groups

### Index Group Registry

Each index group maps to a **Vertex AI Matching Engine index** + a **Firestore database/collection** for metadata:

| Index Group | Matching Engine Index | Firestore DB | Firestore Collection | Content |
|-------------|----------------------|-------------|---------------------|---------|
| **main** | `hd_skye2_0_*` | `hd-skye-db` | `hd-skye-2-0-chunks` | Core HR policy documents (PDFs, DOCX) |
| **servicenow_kb** | `hd_skye_agents_servicenow` | `hd-skye-db-servicenow` | `hd-skye-chunks-servicenow` | ServiceNow KB articles |
| **apac_payroll** | `apac_payroll_deployed` | `hd-skye-db-servicenow` | `apac-payroll-chunks` | APAC region payroll docs |
| **pcard** | `p_card_*` | `hd-skye-db-servicenow` | `p-card_policy` | P-Card allowable/non-allowable table + policies |
| **bulk_exp** | `bulk_exp_*` | `hd-skye-db-servicenow` | `bulk-expense` | Bulk Expense policy docs |

### Data Flow

```
                 ┌─────────────────────────────────────────┐
                 │        Google Cloud Storage (GCS)        │
                 │   hd-skye-rag-us-central1 bucket         │
                 │                                          │
                 │  /hd-skye-2.0/Documents/   ← HR PDFs    │
                 │  /servicenow_kb_extraction/ ← SN KB md   │
                 └─────────────────┬───────────────────────┘
                                   │ Ingestion Pipeline
                                   ▼
                 ┌─────────────────────────────────────────┐
                 │         Ingestion Pipeline               │
                 │  Parse (PDF/DOCX/OCR) → Chunk → Embed   │
                 └──────────────┬─────────────┬────────────┘
                                │             │
                    ┌───────────▼──┐   ┌──────▼──────────────┐
                    │  Firestore   │   │  Matching Engine     │
                    │  (metadata)  │   │  (vector embeddings) │
                    │              │   │                      │
                    │  5 databases │   │  3 endpoints with    │
                    │  6 collections│  │  5 deployed indexes  │
                    └──────────────┘   └─────────────────────┘
                                ↑                ↑
                                │    Query time  │
                                └───────┬────────┘
                                        │
                                 Retrieval Agent
                               (parallel search)
```

### Firestore Document Structure (per chunk)

```json
{
  "id": "chunk_uuid",
  "text": "The leave policy for India states...",
  "source": "hd-skye-2.0/Documents/India_Leave_Policy.pdf",
  "chunk_title": "Section 3.1",
  "section_title": "Annual Leave Entitlement",
  "country": "India",
  "region": "APAC",
  "category": "",
  "category_id": "",
  "document_type": "policy",
  "language": "en",
  "is_table": false,
  "servicenow_url": "",
  "servicenow_number": ""
}
```

---

## 9. ServiceNow Integration

### How ServiceNow KB Articles are Ingested

1. **Extraction**: KB articles are extracted from ServiceNow and stored as markdown files in GCS under `servicenow_kb_extraction/` prefix
2. **Ingestion**: The ingestion pipeline processes them like regular documents (parse → chunk → embed → store)
3. **Separate Index**: ServiceNow KB articles go into their own Matching Engine index (`hd_skye_agents_servicenow`) and Firestore DB (`hd-skye-db-servicenow`)
4. **Cross-reference**: `scripts/sn_kb_crossref.json` maps KB numbers to metadata

### How ServiceNow KB Articles are Served

When a source is a ServiceNow KB article (pattern: `ServiceNow_KB_KB*.html`):

1. **Post-Validation** builds the ServiceNow portal URL: `https://hitachivantara.service-now.com/asknow?id=kb_article_view&sys_kb_id=...`
2. **KB Renderer** (`/documents/ServiceNow_KB_*`) fetches extracted markdown from GCS and renders styled HTML with Hitachi branding
3. Source links in the response point to either the ServiceNow portal URL or the rendered HTML page

### ServiceNow Category Boosting

For P-Card and Bulk Expense variants, ServiceNow KB articles tagged with matching categories get a score boost:
- P-Card queries → boost ServiceNow KB articles with `category="P Card"`
- Bulk Expense queries → boost ServiceNow KB articles with `category="Bulk Expense"`

---

## 10. Document Ingestion Pipeline

The ingestion pipeline (`agents/ingestion_agent.py`) handles end-to-end knowledge base population:

```
INPUT: PDF, DOCX, or text file (from GCS or local)
    │
    ├─ 1. PARSE (parsing_chunking_agent.py)
    │      • PDF: Document AI OCR (preferred) or pypdf fallback
    │      • DOCX: python-docx extraction
    │      • Large PDFs: Split into 15-page chunks for OCR
    │
    ├─ 2. DETECT LANGUAGE (langdetect library)
    │
    ├─ 3. CHUNK (intelligent chunking)
    │      • 1000 character chunks, 150 character overlap
    │      • Respects paragraph boundaries
    │      • Overlap prevents context loss at boundaries
    │
    ├─ 4. EMBED (Vertex AI text-embedding-004)
    │      • Batched: 100 texts per API call
    │      • 3x retry with exponential backoff
    │
    ├─ 5. STORE IN FIRESTORE
    │      • Chunk text + metadata + embedding vector
    │      • Target database/collection per index group
    │
    └─ 6. UPSERT TO MATCHING ENGINE (indexing_agent.py)
           • Vector datapoints added to production index
```

### Batch Ingestion

`ingest_from_gcs()` scans a GCS prefix, downloads each file, and runs the full pipeline. Used for bulk knowledge base updates.

---

## 11. Caching Strategy

SKYE uses Redis for multiple caching layers:

### Cache Layers

| Layer | Key Pattern | TTL | Purpose |
|-------|-----------|-----|---------|
| **Conversation History** | `history:{session_id}` | 24h | Multi-turn conversation memory |
| **Session Context** | `session:{session_id}:latest` | 24h | User profile, roles, last Q&A |
| **Exact Answer Cache** | `answer_cache:{variant}:{query}:{region}` | 3h | Exact-match response cache |
| **Semantic Similarity Cache** | `sem_cache:{region}:{role_key}:*` | 3h | Embedding-based similar query cache |
| **Search Cache** | `search:*` | 1h | Vector search result cache |
| **User Profile Cache** | `user_profile_v1:{email}` | 24h | BQ profile (roles, details) |
| **Reportees Cache** | `reportees_list_v3:{email}` | 1h | Manager's direct reports |
| **Country Resolution** | `resolved_country_v3:{location}` | 30d | Location → country mapping |

### Semantic Similarity Cache

For non-exact matches, SKYE checks semantic similarity:
1. Generate embedding for incoming query
2. Compare against cached query embeddings using cosine similarity (NumPy)
3. If similarity ≥ 0.85 → return cached answer
4. Bucketed by (region, role_key) to prevent cross-role leaks
5. Intent validation: reject if cached intent ≠ query intent

### What's NOT Cached

- Greeting/follow-up/employee-lookup responses
- Denial/error/fallback messages ("I don't have information...")
- P-Card content in non-P-Card cache buckets

---

## 12. Access Control & Role-Based Architecture

### User Identity Flow

```
Request with teams_metadata.email
    │
    ├─ get_user_profile(email)          [BigQuery, cached 24h]
    │   ├─ Parallel Query 1: Snapshot table → manager chain, country, region
    │   └─ Parallel Query 2: Lifecycle table → job title, level, department
    │
    ├─ Derive roles:
    │   ├─ is_manager: has direct reports in snapshot
    │   ├─ is_vp: title contains "vice"/"vp" OR level E2-E5
    │   ├─ is_executive: level E2 or above
    │   ├─ is_hr: department_id ∈ {252, 994}
    │   ├─ is_finance: department_id ∈ {312, 994}
    │   └─ is_super_admin: email in SUPER_ADMIN_EMAILS env var
    │
    ├─ Determine data_scope:
    │   ├─ "regional" (default): limited to allowed regions
    │   └─ "global": HR/GPS users, overrides region check
    │
    └─ Role-based query rewriting:
        ├─ HR/Finance: "HR finance travel expense policy cross-location"
        ├─ Executive: "executive travel business class leadership allowance"
        ├─ VP: "VP/senior leadership policies"
        ├─ Manager: "manager approval team leave workflow"
        └─ Employee: (no augmentation)
```

### Region Filtering

Controlled by `REGION_FILTER_MODE` env var:
- `"all"` (default): All users get region-filtered results
- `"managers_up"`: Only Manager+ roles get region filtering
- `"vp_up"`: Only VP+ roles
- `"none"`: Disable for everyone

**Smart Region Heuristics:**
- Personal queries ("my leave", "am I entitled") → always use home location
- Bereavement queries → use home location, not mentioned location
- Holiday queries → use home location for global-scope users
- Explicit global signals ("all countries", "compare") → keep global

---

## 13. Infrastructure & Deployment

### Docker Build (Multi-stage)

```dockerfile
Stage 1: Build React frontend with Bun
    → Produces /dist with static assets

Stage 2: Python 3.11-slim production image
    → pip install requirements
    → Copy built frontend + Python backend
    → Expose port 8000
    → Run: uvicorn main:app
```

### Cloud Run Configuration

| Setting | Value |
|---------|-------|
| CPU | 2 |
| Memory | 2 GB |
| Min instances | 0 |
| Max instances | 10 |
| Timeout | 300s |
| Concurrency | 100 per instance |
| CPU throttling | Disabled |
| Session affinity | Enabled |
| Authentication | Required (not unauthenticated) |

### GCP Project Structure

| GCP Project | Purpose |
|---|---|
| `hd-procurement-poc-gemini` | Application hosting (Cloud Run, Vertex AI, Firestore, GCS) |
| `hd-onedata-prod` | User data (BigQuery tables for HR worker snapshots) |

### Service Account

`hitachi-fin-service-account@hd-procurement-poc-gemini.iam.gserviceaccount.com`

Needs access to: Vertex AI, Firestore, GCS, Document AI, Cloud Translation, and cross-project BigQuery read access to `hd-onedata-prod`.

---

## 14. API Endpoints

### Chat Endpoints

| Method | Path | Description | Variant |
|--------|------|-------------|---------|
| POST | `/chat` | Main HR agent | `main` (default) or any via `variant` field |
| POST | `/pcard/chat` | P-Card dedicated pipeline | `pcard` |
| POST | `/bulk-expense/chat` | Bulk Expense agent | `bulk_expense` |
| POST | `/payroll/chat` | Payroll agent | `payroll` |

### Request Body (`QueryRequest`)

```json
{
  "question": "What is the leave policy in India?",
  "session_id": "user-123-session-456",
  "teams_metadata": {"email": "john.doe@hitachidigital.com"},
  "data_scope": "regional",
  "region": null,
  "variant": "main"
}
```

### Response Body

```json
{
  "answer": "In India, employees are entitled to...",
  "sources": ["India_Leave_Policy.pdf", "ServiceNow_KB_KB001234.html"],
  "source_links": {
    "India_Leave_Policy.pdf": "/documents/India_Leave_Policy.pdf",
    "ServiceNow_KB_KB001234.html": "https://hitachivantara.service-now.com/asknow?id=..."
  },
  "suggested_questions": ["What about sick leave?", "How do I apply for leave?", "..."],
  "show_feedback_prompt": true,
  "response_time_seconds": 4.23,
  "variant": "main"
}
```

### Utility Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Health check |
| POST | `/new-chat` | Clear session history |
| POST | `/feedback` | Submit user feedback |
| GET | `/documents/{filename}` | Serve document (signed URL or rendered KB) |
| GET | `/agents` | List all registered agents |
| GET | `/variants` | List all agent variants + config |
| GET | `/cache/status` | Redis cache overview |
| GET | `/cache/session/{session_id}` | Session cache detail |

---

## 15. Key Configuration Reference

### Environment Variables (Critical)

| Variable | Purpose | Example |
|----------|---------|---------|
| `PROJECT_ID` | GCP project | `hd-procurement-poc-gemini` |
| `REGION` | GCP region | `us-central1` |
| `LLM_MODEL` | Gemini model | `gemini-2.0-flash` |
| `EMBEDDING_MODEL` | Embedding model | `text-embedding-004` |
| `REDIS_HOST` | Redis endpoint | `10.180.68.37` |
| `SKYE_ENV` | Environment selector | `prod` / `poc` |
| `ENABLED_INDEX_GROUPS` | Active indexes | `servicenow_kb,main,apac_payroll` |
| `REGION_FILTER_MODE` | Region filter scope | `all` / `managers_up` / `none` |
| `DEFAULT_VARIANT` | Default agent variant | `main` |
| `INDEX_PRIORITY_BOOST` | Score boost for priority indexes | `0.20` |
| `SIMILARITY_THRESHOLD` | Semantic cache threshold | `0.95` |
| `LLM_THINKING_BUDGET` | Gemini 2.5 thinking tokens | `0` (disabled) |

### Environment Files

- `.env` — Local development defaults
- `.env.poc` — POC project resources
- `.env.prod` — Production project resources
- `env/prod.yaml` — Cloud Run production env vars
- `env/prod-poc.yaml` — Cloud Run POC env vars

---

## Appendix: File Structure Summary

```
SKYE/
├── main.py                     # FastAPI app + all HTTP endpoints
├── config.py                   # Centralized config, variant registry, index groups
├── Dockerfile                  # Multi-stage build (Bun frontend + Python backend)
├── deploy.config.yaml          # Cloud Run deployment config
├── requirements.txt            # Python dependencies
├── auth.json / auth2.json      # Service account credentials
│
├── agents/
│   ├── agent.py                # Agent registry (all 15 agents)
│   ├── orchestrator.py         # Main pipeline orchestrator (process_query)
│   ├── pcard_orchestrator.py   # P-Card dedicated pipeline (process_pcard_query)
│   ├── query_understanding_agent.py  # Language/intent/region/employee detection
│   ├── guardrails_agent.py     # Pre-flight permission gate
│   ├── access_control_agent.py # Role-based region access
│   ├── retrieval_agent.py      # Vector search + Firestore metadata
│   ├── reranking_agent.py      # Multi-stage result ranking/filtering
│   ├── generation_agent.py     # LLM answer generation (12-rule prompt)
│   ├── translation_agent.py    # Bidirectional translation
│   ├── post_validation_agent.py # Source attribution + quality validation
│   ├── caching_agent.py        # Conversation history management
│   ├── observability_agent.py  # Structured logging + timing
│   ├── feedback_agent.py       # User satisfaction feedback
│   ├── embedding_agent.py      # Vertex AI embedding wrapper
│   ├── ingestion_agent.py      # End-to-end document ingestion
│   ├── parsing_chunking_agent.py # PDF/DOCX parsing + chunking
│   └── indexing_agent.py       # Matching Engine index management
│
├── tools/
│   ├── bq_tools.py             # BigQuery user/role/org queries
│   ├── embedding_tools.py      # Vertex AI embedding generation
│   ├── gcs_tools.py            # Cloud Storage operations
│   ├── opco_tools.py           # OPCO classification + query detection
│   ├── cache_tools.py          # Redis cache + semantic similarity
│   └── kb_renderer.py          # ServiceNow KB → styled HTML
│
├── frontend/                   # React SPA (Vite + Bun)
├── scripts/                    # UAT evaluation scripts
├── test-results/               # Test output archives
├── Testing Prompts/            # QA test question sets
├── docs/                       # Documentation
└── env/                        # Per-environment Cloud Run configs
```
