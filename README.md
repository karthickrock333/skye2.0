# HD SKYE Agent 2.0

**Agentic HR Policy RAG System** — A multi-agent, retrieval-augmented generation (RAG) chatbot that answers HR policy questions with role-aware, region-specific, multilingual responses powered by Google Gemini.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Agents](#agents)
- [Tools](#tools)
- [Agent Variants](#agent-variants)
- [Pipeline Flow](#pipeline-flow)
- [Caching Strategy](#caching-strategy)
- [Frontend](#frontend)
- [API Endpoints](#api-endpoints)
- [Environment Variables (.env)](#environment-variables-env)
- [Setup & Installation](#setup--installation)
- [Makefile Commands](#makefile-commands)
- [Deployment](#deployment)
- [Infrastructure](#infrastructure)

---

## Overview

HD SKYE Agent 2.0 is an enterprise-grade HR policy assistant built on a **multi-agent agentic architecture** using Google's Agent Development Kit (ADK). It retrieves relevant HR policy documents from vectorized knowledge bases and generates contextual, accurate answers using Google Gemini LLM.

### Key Capabilities

- **Multilingual Support** — Detects user language, translates queries to English for retrieval, translates answers back (supports German, Japanese, Tamil, and more)
- **Role-Aware Access Control** — Resolves user identity from BigQuery, builds role matrix (IC / Manager / VP / Executive / Super Admin), gates sensitive content (e.g., P-Card policies restricted to VP+)
- **Region-Specific Filtering** — Filters HR policy results based on user's home country/location and role level
- **Multi-Variant System** — Single codebase serves 4 specialized variants: Main HR, P-Card, Bulk Expense, and APAC Payroll
- **3-Layer Caching** — Exact-match → Semantic similarity (cosine ≥ 0.95) → Vector search, all backed by Redis
- **Speculative Retrieval** — Begins vector search at t=0 with the raw query while query understanding runs in parallel
- **Graceful Degradation** — Every pipeline stage has fallbacks; partial failures don't crash the system

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                   React SPA Frontend (Vite)                  │
│               Served from same origin (FastAPI)              │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
          ┌────────────────────────────────┐
          │    FastAPI Backend (main.py)   │
          │   Port 8000 (Cloud Run/Local)  │
          └────────────────┬───────────────┘
                           │
      ┌────────────────────┼────────────────────┐
      │                    │                    │
      ▼                    ▼                    ▼
 Main Orchestrator   P-Card Orchestrator   Variant Router
 (main, bulk_exp,    (Dedicated pipeline)  (Routes to correct
  payroll variants)                         orchestrator)
      │
      └──► 15 Agents (ADK-based) ──► Tools & External Services
           • Query Understanding       • Vertex AI (Gemini LLM + Embeddings)
           • Guardrails                • Matching Engine (Vector Search)
           • Access Control            • Firestore (5 databases)
           • Retrieval                 • BigQuery (user/role data)
           • Reranking                 • Redis (caching layer)
           • Generation                • GCS (document storage)
           • Translation               • Cloud Translation API
           • Post-Validation           • Document AI (OCR)
           • Caching                   • ServiceNow Portal
           • Feedback
           • Observability
           • Embedding, Parsing, Indexing, Ingestion
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend Framework** | FastAPI + Uvicorn |
| **Agent Framework** | Google ADK (Agent Development Kit) `>=0.4.0` |
| **LLM** | Google Gemini 2.0 Flash (via Vertex AI) |
| **Embeddings** | Vertex AI `text-embedding-004` (768-dim) |
| **Vector Search** | Vertex AI Matching Engine (2 physical endpoints, 5 deployed indexes) |
| **Document Store** | Google Cloud Firestore (5 databases) |
| **User Data** | Google BigQuery (employee snapshots, manager hierarchy, VP/exec roles) |
| **Cache** | Redis (conversation history, semantic cache, session/profile cache) |
| **Object Storage** | Google Cloud Storage (HR documents, ServiceNow KB articles) |
| **OCR** | Google Document AI (PDF/form parsing) |
| **Translation** | Gemini LLM-based + Google Cloud Translation API |
| **Frontend** | React 19 + Vite 7 + Bun |
| **Containerization** | Docker (multi-stage: Bun builder → Python 3.11-slim) |
| **Deployment** | Google Cloud Run |
| **Language** | Python 3.11 (backend), JavaScript/JSX (frontend) |

### Python Dependencies

| Package | Purpose |
|---------|---------|
| `google-adk>=0.4.0` | Google Agent Development Kit |
| `google-cloud-aiplatform` | Vertex AI (LLM, embeddings, vector search) |
| `google-cloud-firestore` | Document chunk storage |
| `google-cloud-bigquery` | User/role data queries |
| `google-cloud-storage` | Document file access & signed URLs |
| `google-cloud-translate` | Language translation |
| `google-cloud-documentai` | OCR & form parsing |
| `google-cloud-logging` | Structured logging |
| `fastapi` | Web framework |
| `uvicorn[standard]` | ASGI server |
| `pydantic>=2.0.0` | Request/response validation |
| `redis` | Cache client |
| `pypdf` | PDF text extraction |
| `python-docx` | DOCX text extraction |
| `langdetect` | Language detection |
| `markdown` | Markdown rendering |
| `numpy` | Numerical operations (cosine similarity) |
| `httpx` | Async HTTP client |
| `python-dotenv` | Environment variable loading |

### Frontend Dependencies

| Package | Purpose |
|---------|---------|
| `react@^19.2.0` | UI framework |
| `react-dom@^19.2.0` | DOM rendering |
| `react-markdown@^10.1.0` | Markdown rendering in chat |
| `remark-gfm@^4.0.1` | GitHub Flavored Markdown (tables, strikethrough) |
| `uuid@^13.0.0` | Session ID generation |
| `vite@^7.2.4` | Build tool & dev server |
| `eslint@^9.39.1` | Code linting |

---

## Project Structure

```
SKYE/
├── main.py                    # FastAPI app entry point & route definitions
├── config.py                  # All configuration & environment variable loading
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Multi-stage Docker build (Bun + Python)
├── deploy.config.yaml         # Cloud Run deployment configuration
├── Makefile                   # Dev commands (run, dev, redis, install)
│
├── agents/                    # All 15 agents (ADK-based)
│   ├── __init__.py
│   ├── agent.py               # Agent registry (name → agent mapping)
│   ├── orchestrator.py        # Main pipeline orchestrator (4 variants)
│   ├── pcard_orchestrator.py  # Dedicated P-Card pipeline
│   ├── access_control_agent.py
│   ├── query_understanding_agent.py
│   ├── guardrails_agent.py
│   ├── embedding_agent.py
│   ├── retrieval_agent.py
│   ├── reranking_agent.py
│   ├── generation_agent.py
│   ├── translation_agent.py
│   ├── post_validation_agent.py
│   ├── caching_agent.py
│   ├── feedback_agent.py
│   ├── observability_agent.py
│   ├── parsing_chunking_agent.py
│   ├── indexing_agent.py
│   └── ingestion_agent.py
│
├── tools/                     # Shared utility modules
│   ├── __init__.py
│   ├── bq_tools.py            # BigQuery queries (user profile, roles)
│   ├── gcs_tools.py           # GCS operations (upload, signed URLs)
│   ├── embedding_tools.py     # Vertex AI embedding generation
│   ├── cache_tools.py         # Redis wrapper (semantic cache, sessions)
│   ├── opco_tools.py          # OPCO classification & filtering
│   └── kb_renderer.py         # ServiceNow KB article renderer
│
├── frontend/                  # React SPA
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   ├── public/                # Static assets (logo, icons)
│   └── src/
│       ├── App.jsx            # Main chat component
│       ├── App.css            # Styles
│       ├── main.jsx           # React entry point
│       └── assets/            # Images
│
├── docs/                      # Documentation
│   ├── FLOW.md                # Detailed pipeline flow documentation
│   ├── KT_ARCHITECTURE.md     # Knowledge transfer & architecture guide
│   ├── CONFIGURATION.md       # Environment variable reference
│   ├── BUGS_AND_FIXES.md      # Bug tracking & fixes
│   └── FLOW_ANALYSIS.md       # Flow analysis
│
├── env/                       # Environment configs (per deployment)
│   ├── prod.yaml              # Production Cloud Run config
│   └── prod-poc.yaml          # POC environment config
│
├── scripts/                   # Testing & utility scripts
│   ├── run_tests.py           # Test runner
│   ├── run_uat_excel.py       # UAT test execution
│   ├── evaluate_uat.py        # UAT evaluation
│   └── ...
│
└── Testing Prompts/           # Test question sets
    ├── Testing_Prompts.txt
    ├── PCard_questions.txt
    ├── Payroll_questions.txt
    └── ...
```

---

## Agents

The system uses **15 modular agents**, each handling a single responsibility in the pipeline:

| # | Agent | Purpose |
|---|-------|---------|
| 1 | **Query Understanding** | Detects language, translates to English, extracts intent/entities, handles follow-ups, rewrites queries, expands abbreviations |
| 2 | **Guardrails** | Pre-flight checks: greeting detection (canned response), Hitachi Vantara blocking (out-of-scope), P-Card permission gating (VP/Exec only) |
| 3 | **Access Control** | Resolves user identity from BigQuery, builds role matrix (`is_manager`, `is_vp`, `is_executive`, `is_super_admin`), determines allowed regions |
| 4 | **Embedding** | Generates 768-dim vector embeddings using Vertex AI `text-embedding-004` for query and semantic cache operations |
| 5 | **Retrieval** | Parallel vector search across multiple Vertex AI Matching Engine indexes (configured per variant), hydrates chunk IDs from Firestore |
| 6 | **Reranking** | Re-ranks results with index priority boost, category boost, region-based filtering, OPCO entity classification, holiday/p-card prioritization |
| 7 | **Generation** | Synthesizes final answer using Gemini LLM with master prompt, few-shot examples, regional context, OPCO labeling, and formatting rules |
| 8 | **Translation** | Translates English answer to user's detected language using Gemini (preserves Markdown formatting) |
| 9 | **Post-Validation** | LLM-based source attribution — determines which retrieved documents actually contributed to the generated answer |
| 10 | **Caching** | Manages conversation history & response caching in Redis (last 10 turns per session) |
| 11 | **Feedback** | Stores user feedback (thumbs up/down + comments) to Firestore |
| 12 | **Observability** | Structured logging of user context, agent pipeline steps, and timing metrics |
| 13 | **Parsing & Chunking** | Document parsing (PDF/DOCX) and intelligent text chunking for ingestion |
| 14 | **Indexing** | Manages Vertex AI Matching Engine index lifecycle (create, update, deploy) |
| 15 | **Ingestion** | End-to-end document ingestion: parse → chunk → embed → Firestore → vector index upsert |

All agents are registered in `agents/agent.py` under the `AGENT_REGISTRY` dictionary.

---

## Tools

Shared utility modules used by agents:

| Tool Module | Description |
|-------------|-------------|
| **bq_tools.py** | BigQuery queries: fetch user profile (location, roles, reportees), check manager status, search employees globally. Uses parallel queries with 24h caching. |
| **gcs_tools.py** | GCS operations: upload/download blobs, generate signed URLs for document access. Smart lookup for ServiceNow KB articles (tries extracted text first, then PDFs). |
| **embedding_tools.py** | Vertex AI text-embedding-004 wrapper: generates 768-dim embeddings with batching + retry logic. Singleton model instance reused across requests. |
| **cache_tools.py** | Redis wrapper with JSON serialization, connection pooling, key namespacing, semantic similarity caching (cosine ≥ 0.95), and pattern-based key lookup. |
| **opco_tools.py** | Operating company (OPCO) classification: detects HDS, GlobalLogic, HD, HV entities from text. Holiday/p-card detection. Filters HV-specific sources. Builds OPCO context notes. |
| **kb_renderer.py** | Renders ServiceNow KB articles for display in frontend. |

---

## Agent Variants

The system supports **4 agent variants** — same codebase, different index groups and behaviors:

| Variant | Endpoint | Index Groups | Priority Boost | Special Behavior |
|---------|----------|-------------|----------------|------------------|
| **main** | `POST /chat` | `servicenow_kb`, `main`, `apac_payroll`, `pcard`, `bulk_exp` | None (all equal) | Full pipeline with access control + region filtering |
| **pcard** | `POST /pcard/chat` | `servicenow_kb`, `pcard` | `pcard` (+0.20) | Dedicated P-Card orchestrator, strict gold-source rule, VP+ only |
| **bulk_expense** | `POST /bulk-expense/chat` | `servicenow_kb`, `bulk_exp` | `bulk_exp` (+0.20) | Bulk expense policy focus |
| **payroll** | `POST /payroll/chat` | `apac_payroll` | `apac_payroll` (+0.20) | APAC payroll specialist |

Each variant maps to specific:
- Vector Search endpoint + deployed index IDs
- Firestore database + collection
- Priority boost rules for reranking

---

## Pipeline Flow

The main orchestrator runs a **6-phase pipeline**:

```
Phase 0: Cache Check
  └─ Exact-match cache → Semantic similarity cache → Cache miss

Phase 1: Parallel Startup
  ├─ Query Understanding (language detect, translate, intent)
  ├─ Speculative Retrieval (raw query vector search at t=0)
  ├─ Home Location Lookup (BigQuery)
  └─ Query Embedding

Phase 2: Guardrails
  └─ Greeting? → canned response | HV question? → block | P-Card? → VP+ gate

Phase 3: Access Control
  └─ Resolve role matrix → Determine allowed regions → Build role context

Phase 4: Retrieval + Reranking
  └─ Collect speculative results → Merge with refined search → Apply boosts → Region filter → Top-K

Phase 5: Generation
  └─ Gemini LLM with master prompt + context + few-shot examples → Answer

Phase 6: Post-Processing (Parallel)
  ├─ Translation (if non-English)
  ├─ Source Validation (LLM-based attribution)
  ├─ Suggested Follow-up Questions
  └─ Cache Storage (answer + conversation history)
```

**P-Card Pipeline** (dedicated orchestrator):
- No access control or region filtering
- Strict gold-source rule: only `PCard_Allowable_NonAllowable.png` + conditional PDFs
- Custom LLM prompt (Procurement Card Policy Expert)
- Fallback email: `CorporateCard@hitachidigital.com`

---

## Caching Strategy

Three-layer caching backed by Redis:

| Layer | Match Type | TTL | Description |
|-------|-----------|-----|-------------|
| **Layer 1** | Exact match | 3 hours | Normalized query string → cached answer |
| **Layer 2** | Semantic similarity | 3 hours | Cosine similarity ≥ 0.95 between query embeddings |
| **Layer 3** | Vector search results | 1 hour | Cached retrieval results (skip vector search) |

Additional cache entries:

| Key | TTL | Purpose |
|-----|-----|---------|
| Conversation history | 24 hours | Last 10 turns per session |
| Session context | 24 hours | Session-level state |
| User profile | 24 hours | Resolved user info from BigQuery |
| Resolved country | 30 days | User's home country mapping |
| Semantic counter | 7 days | Cache hit tracking |

---

## Frontend

React 19 SPA served from the same FastAPI origin.

### Features
- Real-time chat interface with message history
- Markdown rendering with GFM support (tables, strikethrough, lists)
- Source document linking with fallback URLs
- Suggested follow-up questions (clickable)
- Feedback mechanism (thumbs up/down with optional comments)
- Session management via UUID
- Auto-scrolling chat window
- Loading state during API calls
- Error handling with user-friendly messages

### Dev Server
```bash
cd frontend
bun install     # or npm install
bun run dev     # Starts Vite dev server with proxy to backend
```

The Vite dev server proxies API calls (`/chat`, `/feedback`, `/new-chat`, `/documents`, `/healthz`, `/agents`) to `http://localhost:8080`.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | Main chat endpoint (default: `main` variant) |
| `POST` | `/pcard/chat` | P-Card policy specialist |
| `POST` | `/bulk-expense/chat` | Bulk Expense specialist |
| `POST` | `/payroll/chat` | APAC Payroll specialist |
| `POST` | `/new-chat` | Clear conversation history for a session |
| `POST` | `/feedback` | Submit user feedback |
| `GET` | `/healthz` | Health check |
| `GET` | `/documents/{filename}` | Serve/render documents (KB articles or signed GCS URLs) |
| `GET` | `/agents` | List all registered agents |
| `GET` | `/variants` | List all available agent variants |
| `GET` | `/cache/status` | Redis cache overview |
| `GET` | `/cache/session/{session_id}` | Get cached session data |
| `GET` | `/cache/user/{email}` | Get cached user profile |
| `GET` | `/` | Serve React SPA |

### Chat Request

```json
{
  "question": "What is the travel policy?",
  "session_id": "abc-123",
  "teams_metadata": { "name": "John", "email": "john@company.com" },
  "data_scope": "regional",
  "region": "APAC",
  "variant": "main"
}
```

### Chat Response

```json
{
  "answer": "**Travel Policy Overview**\n\nAccording to the HR policy...",
  "response_time_seconds": 2.34,
  "variant": "main",
  "sources": [
    { "title": "Travel Policy 2024", "chunk_id": "...", "score": 0.87 }
  ],
  "source_links": {
    "Travel Policy 2024.pdf": "/documents/Travel%20Policy%202024.pdf"
  },
  "suggested_questions": [
    "What is the per diem rate?",
    "How do I submit a travel reimbursement?"
  ]
}
```

---

## Environment Variables (.env)

The application loads environment variables from `.env` files. Set `SKYE_ENV` to load a specific profile (e.g., `SKYE_ENV=poc` loads `.env.poc`).

### .env File Structure

```env
# =============================================================================
# Google Cloud Platform
# =============================================================================
PROJECT_ID=your-gcp-project-id
REGION=us-central1
GCS_BUCKET_NAME=your-gcs-bucket
GCS_DOCUMENTS_PREFIX=hd-skye-2.0/Documents/

# =============================================================================
# ServiceNow Integration
# =============================================================================
SERVICENOW_PORTAL_URL=https://your-instance.service-now.com/asknow
GCS_SERVICENOW_KB_PREFIX=servicenow_kb_extraction/

# =============================================================================
# Vertex AI Vector Search - Main Index
# =============================================================================
INDEX_ID=your-main-index-id
INDEX_ENDPOINT_ID=projects/PROJECT_NUM/locations/REGION/indexEndpoints/ENDPOINT_ID
DEPLOYED_INDEX_ID=rag_hr_deployed_index

# =============================================================================
# Vertex AI Vector Search - ServiceNow KB Index
# =============================================================================
SERVICENOW_INDEX_ENDPOINT_ID=projects/PROJECT_NUM/locations/REGION/indexEndpoints/ENDPOINT_ID
SERVICENOW_DEPLOYED_INDEX_ID=hd_skye_agents_servicenow

# =============================================================================
# Vertex AI Vector Search - Specialist Indexes (share ServiceNow endpoint)
# =============================================================================
PCARD_DEPLOYED_INDEX_ID=your-pcard-deployed-index-id
BULK_EXP_DEPLOYED_INDEX_ID=your-bulk-exp-deployed-index-id
APAC_PAYROLL_DEPLOYED_INDEX_ID=your-apac-payroll-deployed-index-id

# =============================================================================
# Firestore Databases & Collections
# =============================================================================
FIRESTORE_DATABASE=(default)
FIRESTORE_COLLECTION=hr_policy_chunks

SERVICENOW_FIRESTORE_DB=hd-skye-db-servicenow
SERVICENOW_FIRESTORE_COLLECTION=hd-skye-chunks-servicenow

PCARD_FIRESTORE_DB=your-pcard-firestore-db
PCARD_FIRESTORE_COLLECTION=your-pcard-collection

BULK_EXP_FIRESTORE_DB=your-bulk-exp-firestore-db
BULK_EXP_FIRESTORE_COLLECTION=your-bulk-exp-collection

APAC_PAYROLL_FIRESTORE_DB=your-apac-payroll-firestore-db
APAC_PAYROLL_FIRESTORE_COLLECTION=your-apac-payroll-collection

# =============================================================================
# BigQuery Tables (User & Role Data)
# =============================================================================
BQ_CREDENTIALS_PATH=path/to/service-account.json
BQ_USER_SNAPSHOT_TABLE=project.dataset.user_snapshot_table
BQ_MANAGER_SNAPSHOT_TABLE=project.dataset.manager_snapshot_table
BQ_GBLC_LIFECYCLE_TABLE=project.dataset.lifecycle_table
BQ_GBLC_JOB_MAP_TABLE=project.dataset.job_map_table

# =============================================================================
# Document AI (OCR & Form Parsing)
# =============================================================================
DOCUMENT_AI_OCR_ID=your-ocr-processor-id
DOCUMENT_AI_FORM_PARSER_ID=your-form-parser-id
DOCUMENT_AI_LOCATION=us

# =============================================================================
# Redis Cache
# =============================================================================
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_KEY_PREFIX=skye:

# =============================================================================
# Cache TTLs (in seconds)
# =============================================================================
CACHE_TTL_SEARCH=3600          # 1 hour  - exact-match search cache
CACHE_TTL_ANSWER=10800         # 3 hours - answer / semantic cache
CACHE_TTL_HISTORY=86400        # 24 hours - conversation history
CACHE_TTL_SESSION=86400        # 24 hours - session context
CACHE_TTL_PROFILE=86400        # 24 hours - user profile
CACHE_TTL_COUNTER=604800       # 7 days  - semantic cache counter
CACHE_TTL_COUNTRY=2592000      # 30 days - resolved country
SIMILARITY_THRESHOLD=0.95      # Cosine similarity for semantic cache hit

# =============================================================================
# LLM & Embedding Models
# =============================================================================
LLM_MODEL=gemini-2.0-flash
EMBEDDING_MODEL=text-embedding-004
LLM_THINKING_BUDGET=0          # Set >0 for Gemini 2.5 models

# =============================================================================
# Agent Variant Configuration
# =============================================================================
DEFAULT_VARIANT=main
INDEX_PRIORITY_BOOST=0.20      # Score boost for priority index groups

# Variant → Index Groups (comma-separated)
VARIANT_MAIN_INDEX_GROUPS=servicenow_kb,main,apac_payroll,pcard,bulk_exp
VARIANT_PCARD_INDEX_GROUPS=servicenow_kb,pcard
VARIANT_BULK_EXPENSE_INDEX_GROUPS=servicenow_kb,bulk_exp
VARIANT_PAYROLL_INDEX_GROUPS=apac_payroll

# Variant → Priority Groups (which index groups get score boost)
VARIANT_MAIN_PRIORITY_GROUPS=
VARIANT_PCARD_PRIORITY_GROUPS=pcard
VARIANT_BULK_EXPENSE_PRIORITY_GROUPS=bulk_exp
VARIANT_PAYROLL_PRIORITY_GROUPS=apac_payroll

# =============================================================================
# Access Control
# =============================================================================
REGION_FILTER_MODE=all         # Options: all | managers_up | vp_up | none
SUPER_ADMIN_EMAILS=admin1@company.com,admin2@company.com
```

---

## Setup & Installation

### Prerequisites

- Python 3.11+
- Redis server (local or remote)
- Google Cloud project with Vertex AI, Firestore, BigQuery, GCS enabled
- Bun or Node.js (for frontend build)
- GCP service account credentials

### Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/karthickrock333/skye2.0.git
cd skye2.0

# 2. Create virtual environment & install dependencies
make install
# OR manually:
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# 3. Create .env file (copy the structure above and fill in your values)
cp .env.example .env          # if .env.example exists
# OR create .env manually with the structure from the section above

# 4. Start Redis
make redis-start
# OR: redis-server --daemonize yes

# 5. Build frontend (optional - for serving SPA from backend)
cd frontend
bun install
bun run build
cd ..

# 6. Start the API server
make dev                      # Development mode with hot-reload
# OR:
make run                      # Production mode
# OR:
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
# Build
docker build -t skye-agent .

# Run
docker run -p 8000:8000 \
  --env-file .env \
  skye-agent
```

---

## Makefile Commands

| Command | Description |
|---------|-------------|
| `make run` | Start API server on port 8391 |
| `make dev` | Start API with hot-reload (development) |
| `make up` | Start Redis + API server (all-in-one) |
| `make redis-start` | Start Redis in background (port 6379) |
| `make redis-stop` | Stop Redis |
| `make redis-check` | Check Redis connection status |
| `make install` | Create virtual environment + install dependencies |
| `make venv` | Create virtual environment only |
| `make clean` | Remove virtual environment |
| `make help` | Show all available commands |

---

## Deployment

### Cloud Run Configuration

From `deploy.config.yaml`:

| Setting | Value |
|---------|-------|
| Service Name | `hd-hd1ai-skye-agent` |
| Port | 8000 |
| Memory | 2 Gi |
| CPU | 2 |
| Min Instances | 0 (scales to zero) |
| Max Instances | 10 |
| Request Timeout | 300s |
| Concurrency | 100 requests/instance |
| CPU Throttling | Disabled (always-on CPU) |
| Session Affinity | Enabled |
| Ingress | All traffic |

### Docker Build (Multi-Stage)

1. **Stage 1 — Frontend Builder** (`oven/bun:latest`): Builds React SPA with Vite
2. **Stage 2 — Production** (`python:3.11-slim`): Installs Python deps, copies built frontend, runs Uvicorn

### Health Check

```
GET /healthz → 200 OK
```

---

## Infrastructure

### Google Cloud Services Used

| Service | Purpose |
|---------|---------|
| **Vertex AI** | Gemini LLM, text-embedding-004, Matching Engine (vector search) |
| **Cloud Firestore** | 5 databases storing document chunks (main, ServiceNow, P-Card, Bulk Expense, APAC Payroll) |
| **BigQuery** | Employee data (user snapshots, manager hierarchy, VP/exec roles, job families) |
| **Cloud Storage (GCS)** | HR policy documents (PDFs, DOCX), ServiceNow KB extracted text |
| **Cloud Run** | Serverless container deployment |
| **Document AI** | OCR processor + form parser for document ingestion |
| **Cloud Logging** | Structured application logs |
| **Cloud Translation** | Backup translation service |
| **Redis (Memorystore)** | Caching layer (deployed as Memorystore for Redis in production) |

### Vector Search Architecture

- **2 Physical Endpoints**: Main index endpoint + ServiceNow endpoint
- **5 Deployed Indexes**: `main`, `servicenow_kb`, `pcard`, `bulk_exp`, `apac_payroll`
- **Index Groups**: Each group maps a friendly name → endpoint ID + deployed index ID + Firestore DB + collection

---

## Documentation

Detailed documentation is available in the `docs/` folder:

| Document | Description |
|----------|-------------|
| `docs/FLOW.md` | Complete pipeline flow with phase-by-phase breakdown |
| `docs/KT_ARCHITECTURE.md` | Knowledge transfer guide — full architecture, agents, data flow |
| `docs/CONFIGURATION.md` | Environment variable reference with all settings explained |
| `docs/BUGS_AND_FIXES.md` | Bug tracking and fixes log |
| `docs/FLOW_ANALYSIS.md` | Pipeline flow analysis |

---

## License

This project is proprietary and confidential.
