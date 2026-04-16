# HD SKYE — Parallel & Async Execution Patterns Reference Guide

> **Purpose**: Reusable patterns for parallelism, concurrency, batching, and caching used in the HD SKYE Agentic System. Copy this structure into any new project for fast, non-blocking agent execution.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Agent List & Roles](#2-agent-list--roles)
3. [Pattern 1 — ThreadPoolExecutor Parallel Phases (orchestrator.py)](#3-pattern-1--threadpoolexecutor-parallel-phases)
4. [Pattern 2 — Parallel Multi-Index Vector Search (retrieval_agent.py)](#4-pattern-2--parallel-multi-index-vector-search)
5. [Pattern 3 — Speculative Execution (orchestrator.py)](#5-pattern-3--speculative-execution)
6. [Pattern 4 — Single Combined LLM Call (query_understanding_agent.py)](#6-pattern-4--single-combined-llm-call)
7. [Pattern 5 — Batch Processing with Retry (embedding_tools.py)](#7-pattern-5--batch-processing-with-retry)
8. [Pattern 6 — Redis Caching Layer (cache_tools.py)](#8-pattern-6--redis-caching-layer)
9. [Pattern 7 — Semantic Reranking via API (retrieval_agent.py)](#9-pattern-7--semantic-reranking-via-api)
10. [Pattern 8 — Agent Timing & Profiling (orchestrator.py)](#10-pattern-8--agent-timing--profiling)
11. [Full Pipeline Flow Diagram](#11-full-pipeline-flow-diagram)
12. [How to Reuse in Another Project](#12-how-to-reuse-in-another-project)

---

## 1. Architecture Overview

```
FastAPI (main.py)
  └── process_query() in orchestrator.py
        ├── PHASE 1 (Parallel): History + Home-loc + Speculative Retrieval + Query Understanding
        ├── PHASE 2 (Parallel): Guardrails ‖ Access Control
        ├── PHASE 3 (Sequential): Collect Retrieval Results
        ├── PHASE 4 (Sequential): Reranking → Generation
        └── PHASE 5 (Parallel): Translation ‖ Post-Validation ‖ Follow-up Suggestions
```

**Key Principle**: Independent agents that don't depend on each other's output run **in parallel** using `ThreadPoolExecutor`. Dependent agents run **sequentially**.

---

## 2. Agent List & Roles

| # | Agent Name | File | Role |
|---|-----------|------|------|
| 1 | `hd_skye_orchestrator` | `agents/orchestrator.py` | Master coordinator — runs the full pipeline |
| 2 | `query_understanding_agent` | `agents/query_understanding_agent.py` | Translates, detects intent, extracts geography, identifies employees |
| 3 | `guardrails_agent` | `agents/guardrails_agent.py` | Pre-processing guardrails (block/redirect/allow) |
| 4 | `access_control_agent` | `agents/access_control_agent.py` | Checks user permissions and location-based access |
| 5 | `retrieval_agent` | `agents/retrieval_agent.py` | Vector search across multiple indices in parallel |
| 6 | `reranking_agent` | `agents/reranking_agent.py` | Filters and reranks results by region/OPCO/priority |
| 7 | `generation_agent` | `agents/generation_agent.py` | Generates final answer using Gemini with master prompt |
| 8 | `translation_agent` | `agents/translation_agent.py` | Translates response to user's language |
| 9 | `post_validation_agent` | `agents/post_validation_agent.py` | Validates answer and attributes sources |
| 10 | `caching_agent` | `agents/caching_agent.py` | Manages conversation history and response caching (Redis) |
| 11 | `embedding_agent` | `agents/embedding_agent.py` | Generates vector embeddings (Vertex AI) |
| 12 | `ingestion_agent` | `agents/ingestion_agent.py` | End-to-end document ingestion pipeline |
| 13 | `parsing_chunking_agent` | `agents/parsing_chunking_agent.py` | Parses PDF/DOCX and chunks text |
| 14 | `indexing_agent` | `agents/indexing_agent.py` | Manages vector index operations |
| 15 | `observability_agent` | `agents/observability_agent.py` | Logs user context and agent steps |
| 16 | `feedback_agent` | `agents/feedback_agent.py` | Handles user feedback submission |

### Support Tools

| Tool File | Purpose |
|-----------|---------|
| `tools/cache_tools.py` | Redis cache singleton (thread-safe) |
| `tools/embedding_tools.py` | Embedding generation with batching + retry |
| `tools/bq_tools.py` | BigQuery user details and role lookups |
| `tools/gcs_tools.py` | Google Cloud Storage operations |
| `tools/opco_tools.py` | OPCO/region/holiday detection utilities |

---

## 3. Pattern 1 — ThreadPoolExecutor Parallel Phases

**File**: `agents/orchestrator.py`  
**What it does**: Runs independent agents in parallel using `concurrent.futures.ThreadPoolExecutor`.

### PHASE 1 — Fire-and-Forget + Selective Wait

```python
from concurrent.futures import ThreadPoolExecutor

pool = ThreadPoolExecutor(max_workers=4)

# Define independent tasks as functions
def _fetch_history():
    t = time.time()
    return get_conversation_history(session_id), time.time() - t

def _fetch_home_access():
    t = time.time()
    return check_access(user_email, "Global", teams_metadata, data_scope), time.time() - t

def _speculative_retrieval():
    t = time.time()
    return search_vectors(question, top_k=40), time.time() - t

# Submit all 3 at the same time (t=0)
fut_hist   = pool.submit(_fetch_history)
fut_home   = pool.submit(_fetch_home_access)   # runs in background
fut_spec   = pool.submit(_speculative_retrieval)  # runs in background

# Wait ONLY for the fast one (history is instant)
hist, t_hist = fut_hist.result()

# Continue with query understanding on the main thread
# while home_loc + retrieval keep running in background...
understanding = understand_query(question=question, ...)

# NOW collect home_loc (should be ready by now — ran in parallel)
access_info, t_home = fut_home.result()
```

**Key Insight**: Submit all independent tasks immediately, but only `.result()` (block) when you actually need that task's output. This overlaps slow operations.

### PHASE 2 — Guardrails ‖ Access Control

```python
def _guardrails_task():
    t = time.time()
    r = apply_guardrails(query_en=query_en, user_email=user_email, ...)
    return r, time.time() - t

def _employee_access_task():
    t_total = time.time()
    # ... employee lookup + access check + user details (sequential within this task)
    return { ... , "total_time": time.time() - t_total}

# Both run at the same time
fut_guard  = pool.submit(_guardrails_task)
fut_access = pool.submit(_employee_access_task)

# Collect both
guardrail, t_guard  = fut_guard.result()
access_bundle       = fut_access.result()
```

### PHASE 5 — Translation ‖ Validation ‖ Suggestions

```python
def _translate_task():
    t = time.time()
    r = translate_response(answer_en, lang_code)
    return r, time.time() - t

def _validate_task():
    t = time.time()
    r = validate_and_attribute(answer_en, filtered_results)
    return r, time.time() - t

def _suggest_task():
    t = time.time()
    r = _generate_followup_questions(query_en, answer_en, {...}, lang_code)
    return r, time.time() - t

with ThreadPoolExecutor(max_workers=3) as pool_c:
    fut_trans   = pool_c.submit(_translate_task)
    fut_valid   = pool_c.submit(_validate_task)
    fut_suggest = pool_c.submit(_suggest_task) if not is_greeting else None

    answer, t_trans        = fut_trans.result()
    validation, t_valid    = fut_valid.result()
    if fut_suggest:
        suggested_qs, t_suggest = fut_suggest.result()
```

### The Rule for When to Parallelize

```
CAN parallelize:  Tasks whose inputs are already available and don't depend on each other.
CANNOT parallelize: Task B needs Task A's output → must be sequential.
```

---

## 4. Pattern 2 — Parallel Multi-Index Vector Search

**File**: `agents/retrieval_agent.py`  
**What it does**: Searches multiple vector indices simultaneously.

```python
from concurrent.futures import ThreadPoolExecutor

indices = [
    {"deployed_index_id": DEPLOYED_INDEX_ID, "collection": FIRESTORE_COLLECTION},
    {"deployed_index_id": "apac_payroll_deployed", "collection": "apac-payroll-chunks"},
]

def _search_single_index(idx_info):
    """Search a single index and retrieve Firestore metadata."""
    dep_id = idx_info["deployed_index_id"]
    col = idx_info["collection"]
    results = []

    # Skip if index not deployed
    if not any(d.id == dep_id for d in endpoint.deployed_indexes):
        return results

    response = endpoint.find_neighbors(
        deployed_index_id=dep_id,
        queries=[query_vector],
        num_neighbors=top_k,
    )

    if response:
        col_ref = db.collection(col)
        doc_refs = [col_ref.document(n.id) for n in response[0]]
        docs = db.get_all(doc_refs)  # Batch Firestore read

        for neighbor, doc in zip(response[0], docs):
            if doc.exists:
                info = doc.to_dict()
                results.append({
                    "id": neighbor.id,
                    "distance": neighbor.distance,
                    "text": info.get("text") or info.get("content") or "[Empty]",
                    "source": info.get("source") or "Unknown",
                    "collection": col,
                })
    return results

# Search ALL indices in parallel
with ThreadPoolExecutor(max_workers=len(indices)) as idx_pool:
    futures = [idx_pool.submit(_search_single_index, idx) for idx in indices]
    for fut in futures:
        all_results.extend(fut.result())
```

**Key Insight**: Each index search is independent → run them all in parallel. Also uses `db.get_all(doc_refs)` for batch Firestore reads instead of individual `.get()` calls.

---

## 5. Pattern 3 — Speculative Execution

**File**: `agents/orchestrator.py`  
**What it does**: Starts retrieval with the raw query BEFORE understanding is complete, so retrieval runs in parallel with query understanding.

```
Timeline:
  t=0  ─── Submit speculative retrieval (raw query) ───────────────────┐
  t=0  ─── Start query understanding (8 seconds) ──────────────┐      │
  t=8  ─── Understanding done                                  │      │
  t=10 ─── Speculative retrieval done ──────────────────────────┘──────┘
  t=10 ─── Use results (instead of t=18 if sequential!)
```

```python
# At t=0: fire retrieval with the RAW question (no understanding yet)
fut_spec = pool.submit(_speculative_retrieval)

# Main thread: run query understanding (takes ~8 seconds)
understanding = understand_query(question=question, ...)

# At t=8: understanding done. If the query was translated/rewritten,
# optionally launch a REFINED retrieval with the better search query
if is_holiday_ctx or is_translated:
    fut_refined = pool.submit(_refined_retrieval)  # uses search_query from understanding

# At t=10: collect speculative results (already done or nearly done)
speculative_results, t_spec = fut_spec.result()

# Use refined results if available, otherwise use speculative
if fut_refined is not None:
    raw_results, t_refined = fut_refined.result()
else:
    raw_results = speculative_results
```

**Key Insight**: Don't wait for the "perfect" query — start searching immediately with what you have. Refine later if needed. This saves ~8-10 seconds on every request.

---

## 6. Pattern 4 — Single Combined LLM Call

**File**: `agents/query_understanding_agent.py`  
**What it does**: Replaces 5+ separate LLM calls with ONE combined prompt that returns a JSON object.

### Before (Slow — 5 sequential LLM calls):
```python
is_followup    = llm_call("Is this a follow-up?")        # ~2s
intent         = llm_call("Classify intent")               # ~2s
rewritten      = llm_call("Rewrite as standalone")          # ~2s
region         = llm_call("Extract region")                 # ~2s
employee       = llm_call("Find employee name")             # ~2s
# Total: ~10 seconds
```

### After (Fast — 1 combined LLM call):
```python
def _combined_analysis(query_en: str, history_text_en: str, home_location: str) -> dict:
    prompt = f"""You are an HR query analyzer. Analyze the user query and return a JSON object.

{history_block}

User query: "{query_en}"
User home location: {home_location}

Return ONLY a JSON object with these exact keys:
{{
  "is_followup": true/false,
  "intent": "concise" or "detailed",
  "rewritten_query": "standalone version of the query",
  "target_region": "country or region name",
  "mentioned_employee": "employee name" or null
}}
Return ONLY valid JSON, no markdown."""

    model = GenerativeModel(LLM_MODEL)
    text = model.generate_content(prompt).text.strip()
    text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    result = json.loads(text)
    return result
# Total: ~2 seconds (1 call instead of 5)
```

**Key Insight**: If multiple analyses use the same input context, combine them into one LLM call that returns structured JSON. This cuts latency by 5x.

---

## 7. Pattern 5 — Batch Processing with Retry

**File**: `tools/embedding_tools.py`  
**What it does**: Processes embeddings in batches of 100 with exponential backoff retry.

```python
def generate_embeddings(texts: List[str], model=None) -> List[List[float]]:
    BATCH_SIZE = 100
    all_embeddings: List[List[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = [
            TextEmbeddingInput(text=t, task_type="RETRIEVAL_DOCUMENT")
            for t in texts[i : i + BATCH_SIZE]
        ]

        for attempt in range(3):  # Retry up to 3 times
            try:
                embeddings = model.get_embeddings(batch)
                all_embeddings.extend([e.values for e in embeddings])
                break
            except (ServiceUnavailable, DeadlineExceeded, Exception) as e:
                if attempt < 2:
                    time.sleep(2 * (2 ** attempt))  # Exponential backoff: 2s, 4s
                else:
                    print(f"Embedding failed after 3 attempts: {e}")
                    return []
    return all_embeddings
```

**Key Insight**: APIs have rate limits and size limits. Batch your inputs, and retry with exponential backoff on transient failures.

---

## 8. Pattern 6 — Redis Caching Layer

**File**: `tools/cache_tools.py`  
**What it does**: Thread-safe Redis cache singleton used across all agents.

```python
import redis
import json

class RedisCache:
    """Thread-safe Redis cache wrapper with JSON serialization."""

    def __init__(self):
        self.client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT,
            password=REDIS_PASSWORD, decode_responses=True,
        )

    def get(self, key: str):
        data = self.client.get(key)
        return json.loads(data) if data else None

    def set(self, key: str, value, ttl: int = 3600):
        self.client.set(key, json.dumps(value), ex=ttl)

    def lpush(self, key: str, value):
        self.client.lpush(key, json.dumps(value))
        self.client.ltrim(key, 0, 9)  # Keep only last 10 entries

    def lrange(self, key: str):
        data = self.client.lrange(key, 0, -1)
        return [json.loads(d) for d in data][::-1]

    def delete(self, key: str):
        self.client.delete(key)

# Global singleton — imported and shared by all agents
cache = RedisCache()
```

### Usage across agents:

```python
# retrieval_agent.py — Cache search results (1 hour TTL)
cache_key = f"search:{TENANT}:{query_text}:{top_k}"
cached = cache.get(cache_key)
if cached:
    return cached
# ... do work ...
cache.set(cache_key, all_results, ttl=3600)

# orchestrator.py — Cache full answers (3 hours TTL)
answer_cache_key = f"answer_cache:{query_en.strip().lower()}:{target_region.lower()}"
cached_answer = redis_cache.get(answer_cache_key)
if cached_answer:
    return cached_answer  # Skip entire pipeline!
# ... generate answer ...
redis_cache.set(answer_cache_key, {...}, ttl=10800)

# caching_agent.py — Cache conversation history
cache.lpush(f"history:{session_id}", turn_data)
history = cache.lrange(f"history:{session_id}")
```

**Key Insight**: Cache at multiple levels — search results (1h), full answers (3h), conversation history (list). Check cache BEFORE doing expensive work.

---

## 9. Pattern 7 — Semantic Reranking via API

**File**: `agents/retrieval_agent.py`  
**What it does**: After vector search returns candidates, uses Vertex AI Ranking API to rerank by semantic relevance.

```python
from google.cloud import discoveryengine_v1

def _rerank_results(query: str, results: List[dict], top_k: int = 10) -> List[dict]:
    if not results or len(results) <= 1:
        return results

    client = discoveryengine_v1.RankServiceClient()
    records = [
        discoveryengine_v1.RankingRecord(id=r["id"], content=r["text"][:1000])
        for r in results if r.get("text")
    ]

    ranking_config = f"projects/{PROJECT_ID}/locations/global/rankingConfigs/default_ranking_config"
    request = discoveryengine_v1.RankRequest(
        ranking_config=ranking_config,
        model="semantic-ranker-512@latest",
        query=query,
        records=records,
        top_n=min(top_k, len(records)),
    )

    response = client.rank(request=request)

    reranked = []
    for rr in response.records:
        orig = next((r for r in results if r["id"] == rr.id), None)
        if orig:
            orig["rank_score"] = rr.score
            reranked.append(orig)
    return reranked
```

**Key Insight**: Vector search returns approximate nearest neighbors. A dedicated reranker with the actual query text produces much better ordering.

---

## 10. Pattern 8 — Agent Timing & Profiling

**File**: `agents/orchestrator.py`  
**What it does**: Tracks and prints execution times for every agent, showing time saved by parallelization.

```python
agent_timings = {}

# Wrap every agent call with timing
_t = time.time()
result = some_agent_function(...)
agent_timings["agent_name"] = time.time() - _t

# Track parallel phase wall-clock time
_phase1 = time.time()
# ... parallel submissions + results ...
agent_timings["PARALLEL_PHASE1 (understand ‖ retrieval)"] = time.time() - _phase1

# At the end, print summary
def _print_agent_timings(agent_timings: dict, total_time: float):
    sum_all_individual = sum(
        v for k, v in agent_timings.items()
        if "PARALLEL" not in k and "PHASE" not in k
    )
    print(f"Sum of all individual agents (if sequential): {sum_all_individual:.3f}s")
    print(f"Actual wall time (with parallelization):      {total_time:.3f}s")
    print(f"Time saved by parallelization:                {sum_all_individual - total_time:.3f}s")
    print(f"Speedup factor:                               {sum_all_individual / total_time:.2f}x")
```

**Output Example**:
```
═══════════════════════════════════════════════════════════════════════════════
                   PARALLEL AGENT EXECUTION SUMMARY
═══════════════════════════════════════════════════════════════════════════════

  ┌── PHASE 1 — Understanding ‖ Speculative Retrieval
  │     caching_agent (retrieve)                          0.012s
  │     access_check (home_loc)                           1.234s
  │     query_understanding_agent                         8.456s
  │     retrieval_agent (speculative)                    10.123s
  │  ⚡ PARALLEL_PHASE1 (understand ‖ retrieval)         10.130s
  └────────────────────────────────────────────────────────────

  Sum of all individual agents (if sequential):          25.000s
  Actual wall time (with parallelization):               12.500s
  Time saved by parallelization:                         12.500s
  Speedup factor:                                         2.00x
```

---

## 11. Full Pipeline Flow Diagram

```
t=0   ┬── [Thread 1] caching_agent: get_conversation_history()  ──── INSTANT
      ├── [Thread 2] access_control_agent: check_access()       ──── ~1-2s (background)
      ├── [Thread 3] retrieval_agent: search_vectors(raw_query) ──── ~10s  (background)
      │
      └── [Main Thread] query_understanding_agent: understand_query() ── ~8s
                │
t=8   ─────────┘
      │   Collect home_loc from Thread 2 (already done)
      │
      │   (Optional) Launch refined retrieval if query was translated
      │
      ├── [Thread A] guardrails_agent: apply_guardrails()       ──── ~1s
      ├── [Thread B] access_control_agent: full pipeline        ──── ~2s
      │
t=10  │   Collect speculative retrieval from Thread 3 (done)
      │   Collect guardrails + access (done)
      │
      │   reranking_agent: rerank_and_filter()                  ──── ~1s (sequential)
      │   generation_agent: generate_answer()                   ──── ~3s (sequential)
      │
      ├── [Thread X] translation_agent: translate_response()    ──── ~1s
      ├── [Thread Y] post_validation_agent: validate()          ──── ~1s
      ├── [Thread Z] followup_suggestions: generate()           ──── ~2s
      │
t=16  └── caching_agent: save_conversation_turn()               ──── INSTANT
           DONE — Return response

WITHOUT parallelization: ~30s
WITH parallelization:    ~16s
Speedup:                 ~1.9x
```

---

## 12. How to Reuse in Another Project

### Step 1: Project Structure

```
your_project/
├── main.py                    # FastAPI entry point
├── config.py                  # All configuration constants
├── agents/
│   ├── __init__.py
│   ├── orchestrator.py        # Master coordinator with ThreadPoolExecutor
│   ├── your_agent_1.py        # Each agent = 1 file with tool functions + Agent()
│   ├── your_agent_2.py
│   └── ...
├── tools/
│   ├── __init__.py
│   ├── cache_tools.py         # Redis cache singleton
│   ├── embedding_tools.py     # Batch embedding with retry
│   └── ...
└── requirements.txt
```

### Step 2: Agent Template

```python
"""
your_agent.py
"""
from google.adk.agents import Agent

def your_tool_function(param1: str, param2: int) -> dict:
    """The actual work this agent does."""
    # ... implementation ...
    return {"result": "..."}

your_agent = Agent(
    name="your_agent",
    model="gemini-2.0-flash",
    description="What this agent does in one sentence.",
    instruction="You are the XYZ Agent. Use the your_tool_function tool to ...",
    tools=[your_tool_function],
)
```

### Step 3: Orchestrator Parallel Template

```python
"""
orchestrator.py — Parallel agent execution template
"""
import time
from concurrent.futures import ThreadPoolExecutor

def process_query(question: str, session_id: str) -> dict:
    agent_timings = {}
    pipeline_start = time.time()

    pool = ThreadPoolExecutor(max_workers=4)

    # ── PHASE 1: Launch independent tasks at t=0 ──
    fut_a = pool.submit(agent_a_function, question)
    fut_b = pool.submit(agent_b_function, question)

    # Main thread: do dependent work
    result_c = agent_c_function(question)

    # Collect parallel results
    result_a = fut_a.result()
    result_b = fut_b.result()

    # ── PHASE 2: Next group of parallel tasks ──
    with ThreadPoolExecutor(max_workers=3) as pool2:
        fut_d = pool2.submit(agent_d_function, result_a, result_c)
        fut_e = pool2.submit(agent_e_function, result_b)
        result_d = fut_d.result()
        result_e = fut_e.result()

    pool.shutdown(wait=False)
    return {"answer": result_d, "metadata": result_e}
```

### Step 4: Checklist Before Parallelizing

```
□ Map out your pipeline as a dependency graph
□ Identify which agents/tasks have NO dependency on each other
□ Group independent tasks into parallel phases
□ Use ThreadPoolExecutor for I/O-bound work (API calls, DB queries)
□ Use ProcessPoolExecutor for CPU-bound work (heavy computation)
□ Add Redis caching to skip repeated expensive operations
□ Add timing instrumentation to measure actual speedup
□ Batch API calls where possible (embeddings, Firestore reads)
□ Combine multiple LLM calls into one structured prompt
□ Consider speculative execution for the slowest operations
```

### Step 5: Key Dependencies

```
# requirements.txt
google-adk
google-cloud-aiplatform
google-cloud-firestore
google-cloud-discoveryengine
google-cloud-translate
vertexai
redis
fastapi
uvicorn
```

---

## Summary of All Patterns

| # | Pattern | Where Used | Speedup |
|---|---------|-----------|---------|
| 1 | ThreadPoolExecutor parallel phases | `orchestrator.py` | ~2x overall |
| 2 | Parallel multi-index vector search | `retrieval_agent.py` | ~2x per search |
| 3 | Speculative execution (start before ready) | `orchestrator.py` | ~8-10s saved |
| 4 | Combined LLM call (5-in-1 JSON) | `query_understanding_agent.py` | ~5x for analysis |
| 5 | Batch processing with exponential retry | `embedding_tools.py` | Reliability + throughput |
| 6 | Multi-level Redis caching | `cache_tools.py` + all agents | Skip entire pipeline |
| 7 | Semantic reranking API | `retrieval_agent.py` | Better quality (not speed) |
| 8 | Agent timing & profiling | `orchestrator.py` | Visibility into bottlenecks |
