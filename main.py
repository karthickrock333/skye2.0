"""
main.py - FastAPI backend for HD SKYE Agentic System.
Serves both the API endpoints and the frontend SPA.
"""

import sys
import os
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ─── Eager initialization at import time (once per process) ────────────────
import vertexai
from config import PROJECT_ID, REGION, VARIANT_REGISTRY, DEFAULT_VARIANT

vertexai.init(project=PROJECT_ID, location=REGION)

from agents.orchestrator import process_query
from agents.pcard_orchestrator import process_pcard_query
from agents.caching_agent import clear_history
from agents.feedback_agent import submit_feedback
from tools.gcs_tools import generate_signed_url
from tools.kb_renderer import is_servicenow_kb, render_kb_article
from tools.cache_tools import cache as redis_cache

app = FastAPI(title="HD SKYE Agentic System")

# ─── Static Files ────────────────────────────────────────────────────────────
STATIC_DIR = Path("/app/dist")
if not STATIC_DIR.exists():
    STATIC_DIR = Path(__file__).parent / "frontend" / "dist"

if STATIC_DIR.exists() and (STATIC_DIR / "assets").exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(STATIC_DIR / "assets")),
        name="static-assets",
    )

# ─── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request Models ──────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    question: str
    session_id: str = "default"
    teams_metadata: dict = None
    data_scope: str = "regional"
    region: str = None
    variant: str = Field(
        default=DEFAULT_VARIANT,
        description="Agent variant (main, pcard, bulk_expense, payroll)",
    )


class FeedbackRequest(BaseModel):
    session_id: str
    helpful: bool
    comment: str = ""


# ─── API Endpoints ───────────────────────────────────────────────────────────


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/chat")
def chat(request: QueryRequest):
    """Main chat endpoint — runs the full agentic pipeline.

    The ``variant`` field in the request body controls which index groups are
    searched.  Defaults to the 'main' variant (all indexes).
    """
    import time

    start = time.time()
    result = process_query(
        question=request.question,
        session_id=request.session_id,
        teams_metadata=request.teams_metadata,
        data_scope=request.data_scope,
        variant=request.variant,
    )
    elapsed = round(time.time() - start, 2)
    result["response_time_seconds"] = elapsed
    result["variant"] = request.variant
    return result


# ─── Variant-Specific Chat Routes ────────────────────────────────────────────
# Convenience routes that hardcode the variant so callers don't need to set it
# in the request body.  The request body schema is the same as /chat.


@app.post("/pcard/chat")
def chat_pcard(request: QueryRequest):
    """P-Card Skye Agent — uses P-Card-specific pipeline with strict source rules."""
    import time

    start = time.time()
    result = process_pcard_query(
        question=request.question,
        session_id=request.session_id,
        teams_metadata=request.teams_metadata,
        data_scope=request.data_scope,
    )
    elapsed = round(time.time() - start, 2)
    result["response_time_seconds"] = elapsed
    result["variant"] = "pcard"
    return result


@app.post("/bulk-expense/chat")
def chat_bulk_expense(request: QueryRequest):
    """Bulk Expense Skye Agent — searches ServiceNow KB + Bulk Expense index."""
    import time

    start = time.time()
    result = process_query(
        question=request.question,
        session_id=request.session_id,
        teams_metadata=request.teams_metadata,
        data_scope=request.data_scope,
        variant="bulk_expense",
    )
    elapsed = round(time.time() - start, 2)
    result["response_time_seconds"] = elapsed
    result["variant"] = "bulk_expense"
    return result


@app.post("/payroll/chat")
def chat_payroll(request: QueryRequest):
    """Payroll Skye Agent — searches ServiceNow KB + APAC Payroll index."""
    import time

    start = time.time()
    result = process_query(
        question=request.question,
        session_id=request.session_id,
        teams_metadata=request.teams_metadata,
        data_scope=request.data_scope,
        variant="payroll",
    )
    elapsed = round(time.time() - start, 2)
    result["response_time_seconds"] = elapsed
    result["variant"] = "payroll"
    return result


@app.post("/new-chat")
def new_chat(request: QueryRequest):
    clear_history(request.session_id)
    return {
        "status": "success",
        "message": f"History cleared for session {request.session_id}",
    }


@app.post("/feedback")
def feedback(request: FeedbackRequest):
    return submit_feedback(
        session_id=request.session_id,
        helpful=request.helpful,
        comment=request.comment,
    )


@app.get("/documents/{filename}")
def get_document(filename: str):
    """Serve a document.

    - ServiceNow KB articles (ServiceNow_KB_*.html): Fetches extracted markdown
      from GCS, renders as a styled HTML page.
    - Regular files (PDF, DOCX, etc.): Redirects to a GCS signed URL.
    """
    try:
        # ServiceNow KB articles → render as styled HTML page
        if is_servicenow_kb(filename):
            html = render_kb_article(filename)
            if html:
                return HTMLResponse(content=html, status_code=200)
            # Fall through to signed URL if no extracted text found

        # Regular documents → signed URL redirect
        url = generate_signed_url(filename)
        return RedirectResponse(url=url)
    except Exception as e:
        return {"error": f"Could not generate access link: {e}"}


@app.get("/agents")
def list_agents_endpoint():
    """List all registered agents and their descriptions."""
    from agents.agent import AGENT_REGISTRY

    return {
        name: {"description": agent.description}
        for name, agent in AGENT_REGISTRY.items()
    }


@app.get("/variants")
def list_variants():
    """List all available agent variants and their index group configurations."""
    from config import INDEX_GROUP_REGISTRY, INDEX_PRIORITY_BOOST

    return {
        name: {
            "display_name": v.display_name,
            "description": v.description,
            "index_groups": v.index_groups,
            "active_index_groups": [
                g for g in v.index_groups if g in INDEX_GROUP_REGISTRY
            ],
            "priority_index_groups": v.priority_index_groups,
            "priority_boost": INDEX_PRIORITY_BOOST if v.priority_index_groups else 0,
            "routes": {
                "chat": f"/{name}/chat" if name != "main" else "/chat",
            },
        }
        for name, v in VARIANT_REGISTRY.items()
    }


# ─── Cache Inspection Endpoints ──────────────────────────────────────────────


@app.get("/cache/status")
def cache_status():
    """Overview of all cached data in Redis."""
    connected = redis_cache.ping()
    if not connected:
        return {"connected": False, "error": "Redis unreachable"}

    total_keys = redis_cache.dbsize()

    # Categorize keys
    categories = {
        "conversation_history": redis_cache.keys_by_pattern("history:*"),
        "answer_cache": redis_cache.keys_by_pattern("answer_cache:*"),
        "search_cache": redis_cache.keys_by_pattern("search:*"),
        "session_context": redis_cache.keys_by_pattern("session:*"),
        "user_profiles": redis_cache.keys_by_pattern("user_profile_v1:*"),
        "reportees": redis_cache.keys_by_pattern("reportees_list_v3:*"),
        "employee_search": redis_cache.keys_by_pattern("global_emp_search:*"),
        "country_resolution": redis_cache.keys_by_pattern("resolved_country_v3:*"),
    }

    summary = {}
    for cat, keys in categories.items():
        entries = []
        for k in keys:
            ttl = redis_cache.ttl(k)
            entries.append({"key": k, "ttl_seconds": ttl})
        if entries:
            summary[cat] = {"count": len(entries), "keys": entries}

    # Semantic similarity cache stats
    sem_stats = redis_cache.get_semantic_cache_stats()

    return {
        "connected": True,
        "total_keys": total_keys,
        "categories": summary,
        "semantic_cache": sem_stats,
    }


@app.get("/cache/session/{session_id}")
def cache_session_detail(session_id: str):
    """Get full cached context for a session: user profile, roles, access, last Q&A."""
    # Session context hash
    session_data = redis_cache.hgetall(f"session:{session_id}:latest")

    # Conversation history
    history = redis_cache.lrange(f"history:{session_id}")

    if not session_data and not history:
        return {"error": f"No cached data for session '{session_id}'"}

    return {
        "session_id": session_id,
        "session_context": session_data,
        "conversation_history": history,
        "history_turns": len(history),
    }


@app.get("/cache/user/{email}")
def cache_user_detail(email: str):
    """Get all cached BQ data for a user: profile, reportees."""
    user_profile = redis_cache.get(f"user_profile_v1:{email}")

    # Check both VP and non-VP reportee caches
    reportees = redis_cache.get(f"reportees_list_v3:{email}:False")
    reportees_vp = redis_cache.get(f"reportees_list_v3:{email}:True")

    result = {"email": email}
    if user_profile:
        result["user_profile"] = user_profile
    if reportees:
        result["reportees"] = reportees
    if reportees_vp:
        result["reportees_vp"] = reportees_vp

    if len(result) == 1:
        result["message"] = "No cached BQ data found for this user"

    return result


# ─── Frontend Serving ────────────────────────────────────────────────────────


@app.get("/")
async def serve_index():
    if not STATIC_DIR.exists():
        return HTMLResponse(
            "<h1>Frontend not built</h1><p>Run 'cd frontend && npm run build'</p>",
            status_code=404,
        )
    index = STATIC_DIR / "index.html"
    return (
        FileResponse(str(index))
        if index.exists()
        else HTMLResponse("<h1>index.html not found</h1>", status_code=404)
    )


@app.get("/{path:path}")
async def serve_frontend(path: str):
    if path in ("healthz",):
        return {"error": "Not found"}
    if not STATIC_DIR.exists():
        return HTMLResponse("<h1>Frontend not built</h1>", status_code=404)
    file_path = STATIC_DIR / path
    if file_path.exists() and file_path.is_file():
        return FileResponse(str(file_path))
    index = STATIC_DIR / "index.html"
    return (
        FileResponse(str(index))
        if index.exists()
        else HTMLResponse("<h1>Not found</h1>", status_code=404)
    )
