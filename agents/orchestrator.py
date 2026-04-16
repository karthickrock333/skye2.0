"""
orchestrator.py --------------------
================
The master Orchestrator Agent built with Google ADK.
Coordinates the full pipeline: query understanding → guardrails → access control
→ retrieval → reranking → generation → translation → post-validation → caching.

This replaces the monolithic get_answer() function from the original RAG system
with a modular, agentic architecture where each stage is a distinct agent.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from google.adk.agents import Agent
from vertexai.generative_models import GenerativeModel
import logging

from config import PROJECT_ID, REGION, LLM_MODEL, CACHE_TTL_ANSWER, CACHE_TTL_SESSION
from config import (
    get_llm_generation_config,
    get_variant_index_groups,
    get_variant_config,
    get_variant_priority_collections,
    get_variant_priority_categories,
    should_apply_region_filter,
)
from tools.cache_tools import cache as redis_cache
from tools.embedding_tools import generate_embeddings

# ─── Pre-warm singleton model instances for fast reuse ───────────────────────
_cached_llm_model = None


def _get_llm_model():
    global _cached_llm_model
    if _cached_llm_model is None:
        _cached_llm_model = GenerativeModel(
            LLM_MODEL, generation_config=get_llm_generation_config()
        )
    return _cached_llm_model


# Agent tool functions (direct imports for orchestrator use)
from agents.caching_agent import (
    get_conversation_history,
    save_conversation_turn,
    clear_history,
)
from agents.query_understanding_agent import (
    understand_query,
    translate_text,
    is_translation_request,
    identify_explicit_language,
    detect_and_translate,
)
from agents.guardrails_agent import apply_guardrails
from agents.access_control_agent import (
    check_access,
    get_user_allowed_locations,
    _resolve_country,
)
from agents.retrieval_agent import search_vectors
from agents.reranking_agent import rerank_and_filter
from agents.generation_agent import generate_answer
from agents.translation_agent import translate_response, handle_translation_request
from agents.post_validation_agent import validate_and_attribute
from agents.observability_agent import log_user_context, log_agent_step
from agents.feedback_agent import submit_feedback
from tools.opco_tools import (
    get_user_opco,
    is_hv_query,
    is_holiday_query,
    is_p_card_query,
    build_opco_context_note,
)
from tools.bq_tools import (
    get_user_profile,
    get_user_details_from_bq,
    get_user_roles,
    get_reportees_for_user,
    find_employee_in_reportees,
    search_employee_globally,
)
from agents.pcard_orchestrator import (
    run_pcard_pipeline,
    prioritize_and_filter_pcard_results,
    PCARD_FALLBACK_MSG,
)

logger = logging.getLogger("HD_SKYE_AGENT")


# ─── Role-Based Query Rewriting ─────────────────────────────────────────────


def _get_role_key(roles: dict) -> str:
    """Derive a cache-bucket key from user roles."""
    if not roles:
        return "employee"
    if roles.get("is_hr") and roles.get("is_finance"):
        return "hr_finance"
    if roles.get("is_hr"):
        return "hr"
    if roles.get("is_finance"):
        return "finance"
    if roles.get("is_executive"):
        return "executive"
    if roles.get("is_vp"):
        return "vp"
    if roles.get("is_manager"):
        return "manager"
    return "employee"


def _rewrite_query_for_role(
    query_en: str, search_query: str, roles: dict, home_loc: str, user_opco: str
) -> dict:
    """
    Augment the search query with role-specific context so retrieval
    returns role-appropriate policy documents.

    Returns: {"search_query": str, "role_context_note": str}
    """
    role_key = _get_role_key(roles)
    role_note = ""

    if role_key == "hr_finance":
        role_note = (
            "The user belongs to the HR/Finance department. "
            "They have access to all location-based policies and P-Card policies. "
            "Prioritize HR administration, finance policies, payroll, and cross-location guidelines."
        )
        hr_terms = _get_role_search_terms(query_en, "hr_finance")
        if hr_terms:
            search_query = f"{search_query} {hr_terms}"

    elif role_key == "executive":
        role_note = (
            "The user is an Executive-level employee. "
            "Prioritize executive-specific policies, leadership travel allowances, "
            "executive compensation, stock options, and C-suite HR guidelines."
        )
        # Augment search for executive-specific results
        exec_terms = _get_role_search_terms(query_en, "executive")
        if exec_terms:
            search_query = f"{search_query} {exec_terms}"

    elif role_key == "vp":
        role_note = (
            "The user is a VP (Vice President). "
            "Prioritize VP/senior leadership policies, management travel policies, "
            "executive benefits, and leadership-level HR guidelines."
        )
        vp_terms = _get_role_search_terms(query_en, "vp")
        if vp_terms:
            search_query = f"{search_query} {vp_terms}"

    elif role_key == "manager":
        role_note = (
            "The user is a Manager with direct reports. "
            "Include manager-specific policies: team management, performance reviews, "
            "manager approval workflows, and team HR guidelines."
        )
        mgr_terms = _get_role_search_terms(query_en, "manager")
        if mgr_terms:
            search_query = f"{search_query} {mgr_terms}"

    return {"search_query": search_query, "role_context_note": role_note}


def _get_role_search_terms(query_en: str, role: str) -> str:
    """Return additional search terms based on role and query topic."""
    q = query_en.lower()
    terms = []

    # Topic-specific role augmentation
    if role == "hr_finance":
        if any(w in q for w in ("travel", "expense", "trip", "flight", "hotel")):
            terms.append("HR finance travel expense policy cross-location")
        elif any(w in q for w in ("leave", "holiday", "vacation", "pto", "time off")):
            terms.append("HR finance leave policy administration")
        elif any(w in q for w in ("payroll", "salary", "compensation", "pay")):
            terms.append("HR finance payroll compensation administration")
        elif any(w in q for w in ("pcard", "p-card", "procurement", "purchasing")):
            terms.append("procurement card pcard finance purchasing policy")
        else:
            terms.append("HR finance department policy administration")

    elif role in ("executive", "vp"):
        if any(w in q for w in ("travel", "expense", "trip", "flight", "hotel")):
            terms.append("executive travel business class leadership allowance")
        elif any(w in q for w in ("leave", "holiday", "vacation", "pto", "time off")):
            terms.append("executive leave senior leadership time off")
        elif any(w in q for w in ("compensation", "salary", "bonus", "stock", "pay")):
            terms.append("executive compensation stock options leadership bonus")
        elif any(w in q for w in ("benefit", "insurance", "health", "medical")):
            terms.append("executive benefits leadership health plan")
        else:
            terms.append("executive leadership senior management policy")

    elif role == "manager":
        if any(w in q for w in ("leave", "holiday", "vacation", "pto", "time off")):
            terms.append("manager approval team leave workflow")
        elif any(w in q for w in ("performance", "review", "appraisal", "feedback")):
            terms.append("manager performance review team appraisal guidelines")
        elif any(w in q for w in ("hiring", "recruit", "interview", "onboarding")):
            terms.append("manager hiring approval team recruitment onboarding")
        elif any(w in q for w in ("travel", "expense")):
            terms.append("manager travel approval team expense")
        else:
            terms.append("manager team policy guidelines")

    return " ".join(terms)


def _generate_followup_questions(
    query: str, answer: str, context: dict, lang_code: str = "en"
) -> list:
    """Generate follow-up question suggestions using a fast model."""
    try:
        model = _get_llm_model()
        region = context.get("region", "Global")
        lang_instruction = (
            f"Generate the questions in language code: {lang_code}."
            if lang_code != "en"
            else "Generate the questions in English."
        )
        prompt = f"""You are a professional HR Knowledge Assistant.
Suggest 3 relevant follow-up questions for region: {region}.
Every suggestion MUST be answerable from the source context.
{lang_instruction}
Output a raw JSON array of 3 strings. No markdown. No language code prefix.
Query: {query}
Answer: {answer}
Context: {context.get("text", "")[:2000]}"""
        import json, re

        text = model.generate_content(prompt).text.strip()
        text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
        questions = json.loads(text)
        if isinstance(questions, list) and all(isinstance(q, str) for q in questions):
            return questions[:3]
        return []
    except Exception as e:
        logger.error(f"Suggestion generation error: {e}")
        return []


# ─── Agent Timing Helper ─────────────────────────────────────────────────────


def _print_agent_timings(agent_timings: dict, total_time: float):
    """Print only parallelized phases and their constituent agents to the terminal."""
    print("\n" + "=" * 75)
    print(f"{'PARALLEL AGENT EXECUTION SUMMARY':^75}")
    print("=" * 75)

    # Group agents by phase
    phases = {
        "PHASE 1 — Understanding ‖ Speculative Retrieval ‖ Guardrails": [
            "caching_agent (retrieve)",
            "access_check (home_loc)",
            "query_understanding_agent",
            "guardrails_agent",
            "retrieval_agent (speculative)",
            "retrieval_agent (refined)",
            "PARALLEL_PHASE1 (understand ‖ retrieval)",
        ],
        "PHASE 2 — Access Control": [
            "access_control_pipeline",
            "PARALLEL_PHASE2 (access)",
        ],
        "PHASE 5 — Translation ‖ Validation ‖ Suggestions": [
            "translation_agent",
            "post_validation_agent",
            "followup_suggestions",
            "PARALLEL_PHASE5 (translate ‖ validate ‖ suggest)",
        ],
    }
    sequential_keys = [
        "reranking_agent",
        "generation_agent",
        "fallback_retry",
        "caching_agent (save)",
        "translation_agent (early)",
    ]

    for phase_name, keys in phases.items():
        matching = [(k, agent_timings[k]) for k in keys if k in agent_timings]
        if not matching:
            continue
        print(f"\n  ┌── {phase_name}")
        for k, v in matching:
            if "PARALLEL" in k or "PHASE" in k:
                print(f"  │  ⚡ {k:<48} {v:>8.3f}s")
            else:
                print(f"  │     {k:<48} {v:>8.3f}s")
        print(f"  └{'─' * 60}")

    # Sequential agents
    seq = [(k, agent_timings[k]) for k in sequential_keys if k in agent_timings]
    if seq:
        print(f"\n  ┌── Sequential Agents")
        for k, v in seq:
            print(f"  │     {k:<48} {v:>8.3f}s")
        print(f"  └{'─' * 60}")

    # Totals
    parallel_total = sum(
        agent_timings.get(k, 0)
        for k in [
            "PARALLEL_PHASE1 (understand ‖ retrieval)",
            "PARALLEL_PHASE2 (access)",
            "PARALLEL_PHASE5 (translate ‖ validate ‖ suggest)",
        ]
    )
    sequential_total = sum(
        agent_timings.get(k, 0) for k in sequential_keys if k in agent_timings
    )
    sum_all_individual = sum(
        v for k, v in agent_timings.items() if "PARALLEL" not in k and "PHASE" not in k
    )

    print(f"\n{'─' * 75}")
    print(
        f"  {'Sum of all individual agents (if sequential):':<50} {sum_all_individual:>8.3f}s"
    )
    print(f"  {'Actual wall time (with parallelization):':<50} {total_time:>8.3f}s")
    print(
        f"  {'Time saved by parallelization:':<50} {sum_all_individual - total_time:>8.3f}s"
    )
    print(
        f"  {'Speedup factor:':<50} {sum_all_individual / total_time if total_time > 0 else 0:>8.2f}x"
    )
    print("=" * 75 + "\n")


# ─── Main Orchestration Function ────────────────────────────────────────────


def process_query(
    question: str,
    session_id: str = "default",
    teams_metadata: dict = None,
    data_scope: str = "regional",
    variant: str = "main",
) -> dict:
    """
    Master orchestration pipeline with aggressive parallel agent execution.
    Overlaps the two slowest agents (query understanding ~8s and retrieval ~10s)
    using speculative retrieval to cut wall-clock time roughly in half.

    Args:
        question: The user's question.
        session_id: Session identifier for conversation history.
        teams_metadata: Teams user metadata (email, etc.).
        data_scope: Data access scope (regional/global).
        variant: Agent variant name (main, pcard, bulk_expense, payroll).
            Controls which index groups are searched.

    Pipeline layout (time flows left → right):
      ┌─ History + Home-loc (instant) ─→ Understanding (8s) ─→ Guardrails ‖ Access ─┐
      │                                                                               ▼
      └─ Speculative Retrieval (10s) ─────────────────────────→ Reranking → Gen → GRP_C
      GRP_C = Translation ‖ Post-Validation ‖ Follow-up Suggestions (parallel)
    """
    pipeline_start = time.time()
    agent_timings = {}
    _query_embedding_cache = None  # Reused for similarity cache storage

    # Resolve variant-specific index groups once for this request
    variant_config = get_variant_config(variant)
    variant_index_groups = get_variant_index_groups(variant)
    variant_priority_collections = get_variant_priority_collections(variant)
    variant_priority_categories = get_variant_priority_categories(variant)

    log_agent_step(
        "orchestrator",
        "START",
        f"variant={variant} groups={[g.name for g in variant_index_groups]} | "
        f"Q: {question[:80]}... | Session: {session_id}",
    )

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 1 — Launch ALL independent work at t=0
    #   Thread 1: History retrieval         (instant, needed for understanding)
    #   Thread 2: Home-loc access check     (slow — runs in background)
    #   Thread 3: SPECULATIVE vector search (~10s, runs in background)
    # Main thread waits ONLY for history, then starts understanding immediately.
    # Home-loc and retrieval continue in background.
    # ═══════════════════════════════════════════════════════════════════════

    user_email = teams_metadata.get("email") if teams_metadata else None
    user_opco = get_user_opco(user_email)

    log_agent_step(
        "orchestrator",
        "PHASE_1",
        "History → Understanding ‖ Retrieval ‖ Home-loc ‖ Embedding",
    )
    _phase1 = time.time()

    pool = ThreadPoolExecutor(max_workers=5)

    def _fetch_history():
        t = time.time()
        return get_conversation_history(session_id), time.time() - t

    def _fetch_home_access():
        t = time.time()
        return check_access(
            user_email, "Global", teams_metadata, data_scope
        ), time.time() - t

    def _speculative_retrieval():
        t = time.time()
        return search_vectors(
            question, top_k=40, index_groups=variant_index_groups
        ), time.time() - t

    def _generate_query_embedding():
        """Generate embedding for the raw question in background (for similarity cache)."""
        t = time.time()
        embs = generate_embeddings([question])
        return embs[0] if embs else None, time.time() - t

    fut_hist = pool.submit(_fetch_history)
    fut_home = pool.submit(_fetch_home_access)  # runs in background — don't wait!
    fut_spec = pool.submit(_speculative_retrieval)  # runs in background
    fut_emb = pool.submit(_generate_query_embedding)  # embedding in background

    # Wait ONLY for history (instant) — don't block on home_loc
    hist, t_hist = fut_hist.result()
    agent_timings["caching_agent (retrieve)"] = t_hist

    history = hist["history"]
    history_text = hist["history_text"]
    history_text_en = hist["history_text_en"]

    # ─── Translation-only shortcut ───────────────────────────────────────
    if history and is_translation_request(question):
        log_agent_step("translation_agent", "Translation-only request detected")
        _t = time.time()
        last_turn = history[-1]
        # Prefer English answer for best translation quality
        last_answer_for_translation = last_turn.get("assistant_en") or last_turn.get(
            "assistant", ""
        )
        result = handle_translation_request(
            question,
            last_answer_for_translation,
            last_turn.get("sources", []),
            last_turn.get("suggested_questions", []),
        )
        agent_timings["translation_agent (early)"] = time.time() - _t
        if result:
            fut_spec.cancel()
            pool.shutdown(wait=False)
            save_conversation_turn(
                session_id,
                question,
                question,
                result["answer"],
                last_turn.get("assistant_en", last_turn.get("assistant", "")),
                result["sources"],
                result.get("source_links", {}),
                result["suggested_questions"],
            )
            _print_agent_timings(agent_timings, time.time() - pipeline_start)
            return result

    # ═══════════════════════════════════════════════════════════════════════
    # Understanding runs on main thread while retrieval + home_loc continue
    # ═══════════════════════════════════════════════════════════════════════
    log_agent_step(
        "query_understanding_agent",
        "Analyzing query (retrieval + home_loc running in parallel)",
    )
    _t = time.time()
    understanding = understand_query(
        question=question,
        history_text=history_text,
        history_text_en=history_text_en,
        home_location="Global",  # Use default; real home_loc resolves in background
        teams_metadata=teams_metadata,
    )
    agent_timings["query_understanding_agent"] = time.time() - _t

    # NOW collect home_loc (should be ready by now — ran in parallel with understanding)
    access_info, t_home = fut_home.result()
    agent_timings["access_check (home_loc)"] = t_home
    home_loc = access_info["home_location"]

    query_en = understanding["query_en"]
    lang_code = understanding["response_language_code"]
    is_greeting = understanding["is_greeting"]
    is_followup = understanding["is_followup"]
    intent = understanding["intent"]
    search_query = understanding["search_query"]
    target_region = understanding["target_region"]
    mentioned_employee = understanding["mentioned_employee"]

    # Strip explicit language request from query_en so generation LLM doesn't add translation disclaimers
    if lang_code != "en":
        import re as _re

        query_en = (
            _re.sub(
                r"\s*(?:(?:give|answer|respond|reply|tell|explain|write|say)\s+(?:me\s+)?)?in\s+(?:english|tamil|hindi|japanese|telugu|kannada|malayalam|marathi|bengali|german|french|spanish|chinese|korean|thai|vietnamese|arabic|portuguese|italian|dutch|russian|turkish|polish|czech|swedish|danish)\s*(?:please|pls|plz|language)?\s*[?.!]*\s*$",
                "",
                query_en,
                flags=_re.IGNORECASE,
            ).strip()
            or query_en
        )

    # Fix target_region if understanding defaulted to Global but user has a home location.
    # For data_scope=global (HR/GPS), narrow to home for personal queries and
    # holidays — leave genuinely informational queries as Global.
    if home_loc.lower() != "global":
        from tools.opco_tools import is_holiday_query as _is_holiday_q

        _should_use_home = False
        _is_global_scope = data_scope == "global"

        # Detect personal/individual queries: the user is asking about THEIR
        # own leave, payroll, benefits, etc. — not a general informational query.
        _PERSONAL_SIGNALS = [
            "my ",
            "i have",
            "i am",
            "i'm",
            "i get",
            "i want",
            "am i",
            "do i",
            "can i",
            "how many days",
            "how much leave",
            "how many leave",
            "paternity",
            "maternity",
            "sick leave",
            "my leave",
            "my salary",
            "my payroll",
            "my benefits",
            "my manager",
            "my reporting",
            "my team",
            "allowed per year",
            "entitled to",
        ]
        _q_lower = query_en.lower()
        _is_personal = any(sig in _q_lower for sig in _PERSONAL_SIGNALS)

        if target_region.lower() == "global":
            if _is_global_scope:
                # Global/HR users: narrow for holidays, personal questions,
                # detailed-intent queries, AND general informational queries
                # that don't explicitly request global/all-country info.
                # Rationale: "payroll contact details" or "insurance queries"
                # without a country → user wants their own country's info.
                if _is_holiday_q(query_en):
                    _should_use_home = True
                elif _is_personal or intent == "detailed":
                    _should_use_home = True
                elif intent == "concise":
                    # Check if user explicitly asks for global/all-country info
                    _GLOBAL_SIGNALS = [
                        "all countries",
                        "every country",
                        "each country",
                        "across countries",
                        "compare",
                        "comparison",
                        "globally",
                        "worldwide",
                    ]
                    _wants_global = any(sig in _q_lower for sig in _GLOBAL_SIGNALS)
                    if not _wants_global:
                        _should_use_home = True
            else:
                # Regional users: ALWAYS narrow to home when no specific
                # country was mentioned (target_region defaulted to Global).
                # A regional user in Poland asking "payroll contacts" should
                # get Poland results, not all-countries.
                _should_use_home = True
        elif (
            is_followup
            and target_region.lower() != home_loc.lower()
            and _is_holiday_q(query_en)
        ):
            _q = (question + " " + query_en).lower()
            if target_region.lower() not in _q:
                _should_use_home = True

        # ── Bereavement / death queries: the mentioned location is often
        # WHERE the event happened, not which country's policy applies.
        # E.g. "grandfather passed away in the US" from an India user →
        # the user needs India's bereavement policy, not US policy.
        if (
            not _should_use_home
            and target_region.lower() != home_loc.lower()
            and target_region.lower() != "global"
        ):
            _BEREAVEMENT_SIGNALS = [
                "passed away",
                "died",
                "death",
                "funeral",
                "bereavement",
                "compassionate leave",
                "days off",
                "time off",
            ]
            _is_bereavement = any(sig in _q_lower for sig in _BEREAVEMENT_SIGNALS)
            if _is_bereavement:
                logger.info(
                    f"[orchestrator] Bereavement query — overriding region "
                    f"'{target_region}' → home '{home_loc}' "
                    f"(mentioned location is event location, not policy country)"
                )
                _should_use_home = True

        if _should_use_home:
            target_region = home_loc

    log_agent_step(
        "query_understanding_agent",
        "DONE",
        f"lang={lang_code} greeting={is_greeting} followup={is_followup} "
        f"intent={intent} region={target_region} employee={mentioned_employee}",
    )

    # ═══════════════════════════════════════════════════════════════════════
    # GUARDRAILS — run BEFORE cache to prevent restricted content from being
    # served via cached answers (root cause of Japanese P-Card cache leak).
    # Guardrails are fast (keyword checks + 1 BQ call) so inline is fine.
    # ═══════════════════════════════════════════════════════════════════════
    _t_guard = time.time()
    guardrail = apply_guardrails(
        query_en=query_en,
        user_email=user_email,
        user_opco=user_opco,
        is_greeting=is_greeting,
        data_scope=data_scope,
    )
    agent_timings["guardrails_agent"] = time.time() - _t_guard
    hv_disclaimer = guardrail.get("hv_disclaimer")
    pcard_authorized = guardrail.get("pcard_authorized", False)

    if not guardrail["proceed"]:
        response_text = guardrail["response"] or "I'm unable to process this request."
        if lang_code != "en" and not is_greeting:
            response_text = translate_response(response_text, lang_code)
        save_conversation_turn(
            session_id, query_en, question, response_text, guardrail.get("response", "")
        )
        fut_spec.cancel()
        pool.shutdown(wait=False)
        _print_agent_timings(agent_timings, time.time() - pipeline_start)
        return {
            "answer": response_text,
            "sources": [],
            "source_links": {},
            "suggested_questions": [],
            "show_feedback_prompt": False,
        }

    # ─── Answer Cache — check exact match, then similarity-based ────────
    # Embedding was generated in parallel with understanding — collect it now
    query_embedding_result, t_emb = fut_emb.result()
    _query_embedding_cache = query_embedding_result
    agent_timings["embedding_generation"] = t_emb

    if not is_greeting and not is_followup and not mentioned_employee:
        # Exact match first (fastest — pure Redis, <10ms)
        answer_cache_key = (
            f"answer_cache:{variant}:{query_en.strip().lower()}:{target_region.lower()}"
        )
        cached_answer = redis_cache.get(answer_cache_key)

        # If no exact match, try similarity-based semantic cache
        if not cached_answer and _query_embedding_cache:
            _t_sim = time.time()
            try:
                # Search semantic cache with role-aware bucketing
                for rk in ["employee", "manager", "vp", "executive"]:
                    cached_answer = redis_cache.find_similar_cached(
                        _query_embedding_cache, target_region.lower(), rk
                    )
                    if cached_answer:
                        sim_score = cached_answer.pop("_similarity_score", 0)
                        matched_q = cached_answer.pop("_matched_query", "")
                        matched_intent = cached_answer.pop("_matched_intent", "")

                        # Fix 5: Intent/topic validation — reject if cached answer's
                        # intent doesn't match the current query's intent.
                        if matched_intent and intent and matched_intent != intent:
                            log_agent_step(
                                "orchestrator",
                                "SIMILARITY_CACHE_INTENT_MISMATCH",
                                f"score={sim_score:.4f} cached_intent='{matched_intent}' "
                                f"query_intent='{intent}' — skipping",
                            )
                            cached_answer = None
                            continue

                        log_agent_step(
                            "orchestrator",
                            "SIMILARITY_CACHE_HIT",
                            f"score={sim_score:.4f} matched='{matched_q[:60]}' "
                            f"role_bucket={rk} intent={matched_intent or 'n/a'}",
                        )
                        break
            except Exception as e:
                logger.error(f"Similarity cache lookup failed: {e}")
            agent_timings["similarity_cache_lookup"] = time.time() - _t_sim
        elif cached_answer:
            log_agent_step(
                "orchestrator",
                "EXACT_CACHE_HIT",
                f"Returning cached answer for: {query_en[:60]}",
            )

        if cached_answer:
            # Skip cached denial / error / fallback messages — access level or
            # backend availability may differ per user or request.
            ca_text = (
                cached_answer.get("answer_en") or cached_answer.get("answer") or ""
            ).lower()
            _SKIP_PHRASES = [
                "do not have permission",
                "not have access",
                "i don't have information",
                "i don't have enough information",
                "don't have enough information",
                "don't have sufficient information",
                "no relevant context found",
                "no information about that",
                "unable to process",
                "access denied",
                "error occurred",
            ]
            # Fix 2: Also skip cached P-Card content — belt-and-suspenders
            # protection in case a P-Card answer was cached before this fix.
            if any(p in ca_text for p in _SKIP_PHRASES) or is_p_card_query(
                cached_answer.get("answer_en") or cached_answer.get("answer") or ""
            ):
                cached_answer = None

        if cached_answer:
            # Translate cached answer if needed
            if lang_code != "en" and cached_answer.get("answer_en"):
                cached_answer["answer"] = translate_response(
                    cached_answer["answer_en"], lang_code
                )
                if cached_answer.get("suggested_questions"):
                    cached_answer["suggested_questions"] = (
                        [
                            translate_text(q, lang_code)
                            for q in cached_answer["suggested_questions_en"]
                        ]
                        if cached_answer.get("suggested_questions_en")
                        else cached_answer["suggested_questions"]
                    )
            save_conversation_turn(
                session_id=session_id,
                user_query=query_en,
                user_original=question,
                answer=cached_answer["answer"],
                answer_en=cached_answer.get("answer_en", cached_answer["answer"]),
                sources=cached_answer["sources"],
                source_links=cached_answer.get("source_links", {}),
                suggested_questions=cached_answer.get("suggested_questions", []),
                show_feedback_prompt=cached_answer.get("show_feedback_prompt", False),
            )
            fut_spec.cancel()
            pool.shutdown(wait=False)
            _print_agent_timings(agent_timings, time.time() - pipeline_start)
            return {
                "answer": cached_answer["answer"],
                "sources": cached_answer["sources"],
                "source_links": cached_answer.get("source_links", {}),
                "suggested_questions": cached_answer.get("suggested_questions", []),
                "show_feedback_prompt": cached_answer.get(
                    "show_feedback_prompt", False
                ),
            }

    # ─── Pre-launch refined retrieval for holiday, translated, OR region-specific queries
    # When the original question is non-English, speculative retrieval used the raw
    # foreign text and likely returned poor results. Re-run with the English search_query.
    # When the user has a specific target_region (e.g. India) but the raw question
    # doesn't mention it, speculative retrieval misses country-specific content
    # because the embedding/ranking favours other countries.  Re-run with region context.
    # Use the ORIGINAL query_en for holiday detection, NOT the expanded
    # search_query — expansions can inject "holiday" as a synonym (e.g. the
    # "carry forward" expansion includes "holiday") causing false positives.
    is_holiday_ctx = is_holiday_query(query_en) or (
        is_followup and is_holiday_query(history_text_en)
    )
    is_translated = understanding.get("original_language_code", "en") != "en"
    # Region-specific: speculative retrieval used raw question without country context.
    # If the user's target region is not mentioned in the raw question, a refined
    # retrieval with the region appended will surface country-specific content that
    # the speculative retrieval missed.
    _needs_region_refinement = (
        target_region.lower() != "global"
        and target_region.lower() not in question.lower()
    )
    fut_refined = None
    if is_holiday_ctx or is_translated or _needs_region_refinement:
        _refined_top_k = 100 if is_holiday_ctx else 40

        # Augment refined retrieval query with target_region for better
        # country-specific results.  The main search_query augmentation at
        # line ~923 happens too late (after retrieval is launched in the
        # background), so we do it here for the refined query.
        _refined_query = search_query
        if (
            target_region.lower() != "global"
            and target_region.lower() not in search_query.lower()
        ):
            _refined_query = f"{search_query} in {target_region}"

        def _refined_retrieval():
            t = time.time()
            return search_vectors(
                _refined_query, top_k=_refined_top_k, index_groups=variant_index_groups
            ), time.time() - t

        fut_refined = pool.submit(_refined_retrieval)

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 2 — Access Control (guardrails already done above)
    # ═══════════════════════════════════════════════════════════════════════
    log_agent_step(
        "orchestrator", "PHASE_2", "Access control (retrieval still running)"
    )
    _phase2 = time.time()

    def _employee_access_task():
        """Sequential: employee lookup (if needed) → access control → user details."""
        t_total = time.time()
        emp_note = ""
        early_ret = None
        _target = target_region
        _search = search_query
        _emp_broad_region = None  # BQ EMPLOYING_REGION from employee lookup

        if mentioned_employee and user_email and not is_greeting:
            profile = get_user_profile(user_email)
            roles = profile["roles"]
            is_super = roles.get("is_super_admin", False)
            is_vp_or_exec = roles.get("is_vp") or roles.get("is_executive")
            is_mgr = roles.get("is_manager")
            can_lookup = is_vp_or_exec or is_mgr

            if not can_lookup:
                # Regular Employee (including super admins without manager/VP role)
                # — employee lookup is DENIED
                early_ret = {
                    "answer": (
                        "Employee lookup is not available for your role. "
                        "This feature is accessible to managers and above. "
                        "Please contact your manager or HR for employee-specific information."
                    ),
                    "sources": [],
                    "source_links": {},
                    "suggested_questions": [],
                    "show_feedback_prompt": False,
                }
            else:
                # Manager (1 level) / VP-Executive (2 levels) — lookup within reportees
                log_agent_step(
                    "access_control_agent", f"Employee lookup: {mentioned_employee}"
                )
                reportees = get_reportees_for_user(
                    user_email, is_vp=bool(is_vp_or_exec)
                )
                matched = find_employee_in_reportees(reportees, mentioned_employee)

                if len(matched) == 1:
                    emp = matched[0]
                    emp_country = emp.get("country", "")
                    emp_name = emp.get("name") or mentioned_employee
                    _emp_broad_region = emp.get("region", "")  # BQ EMPLOYING_REGION
                    if emp_country:
                        resolved_region = _resolve_country(emp_country)
                        _target = resolved_region
                        _search = f"{_search} {resolved_region}"
                        emp_note = (
                            f"The user is asking about employee '{emp_name}' located in {resolved_region}. "
                            f"Answer based on {resolved_region}'s HR policies."
                        )
                elif len(matched) > 1:
                    names_list = "\n".join(
                        f"  - {m.get('name', 'Unknown')} ({m.get('country', '?')}, LDAP: {m.get('ldap_id', 'N/A')})"
                        for m in matched
                    )
                    early_ret = {
                        "answer": f"Multiple employees named '{mentioned_employee}' found:\n{names_list}\n\nPlease clarify.",
                        "sources": [],
                        "source_links": {},
                        "suggested_questions": [],
                        "show_feedback_prompt": False,
                    }
                else:
                    early_ret = {
                        "answer": f"Could not find '{mentioned_employee}' in your org. Please check spelling.",
                        "sources": [],
                        "source_links": {},
                        "suggested_questions": [],
                        "show_feedback_prompt": False,
                    }

        user_det = get_user_details_from_bq(user_email) if user_email else None
        access_r = check_access(user_email, _target, teams_metadata, data_scope)

        return {
            "employee_context_note": emp_note,
            "employee_broad_region": _emp_broad_region or None,
            "target_region": _target,
            "search_query": _search,
            "access": access_r,
            "user_details": user_det,
            "early_return": early_ret,
            "total_time": time.time() - t_total,
        }

    fut_access = pool.submit(_employee_access_task)
    access_bundle = fut_access.result()

    agent_timings["access_control_pipeline"] = access_bundle["total_time"]
    agent_timings["PARALLEL_PHASE2 (access)"] = time.time() - _phase2

    employee_context_note = access_bundle["employee_context_note"]
    target_region = access_bundle["target_region"]
    search_query = access_bundle["search_query"]
    access = access_bundle["access"]
    user_details = access_bundle["user_details"]

    # ─── Resolve broad_region for reranking (BQ EMPLOYING_REGION) ────────
    # Priority: 1) employee lookup region, 2) user's home_region (when target
    # matches their home country), 3) None (falls back to static dict)
    broad_region = access_bundle.get("employee_broad_region")
    if not broad_region:
        home_region = access.get("home_region")
        if (
            home_region
            and target_region.lower() != "global"
            and target_region.lower() == access.get("home_location", "").lower()
        ):
            broad_region = home_region

    # ─── Resolve region filter mode ──────────────────────────────────────
    # Controlled by REGION_FILTER_MODE env var.  When disabled for this
    # user's role, reranking skips region boost / other-region penalty.
    _apply_region_filter = should_apply_region_filter(access.get("roles", {}))
    if not _apply_region_filter:
        log_agent_step(
            "orchestrator",
            "REGION_FILTER",
            f"Region filter DISABLED for role={_get_role_key(access.get('roles', {}))}",
        )

    # ─── Check employee lookup early return ──────────────────────────────
    if access_bundle["early_return"]:
        fut_spec.cancel()
        pool.shutdown(wait=False)
        _print_agent_timings(agent_timings, time.time() - pipeline_start)
        return access_bundle["early_return"]

    # ─── Check access gate ───────────────────────────────────────────────
    if not access["allowed"]:
        # Instead of hard-blocking, fall back to the user's home region.
        # The user may be asking about a process they need (e.g., visa docs
        # for travel) rather than trying to access another country's HR
        # policies.  Re-scope to their home location and proceed.
        home = access.get("home_location", "Global")
        if home and home.lower() != "global":
            log_agent_step(
                "orchestrator",
                "ACCESS_FALLBACK",
                f"Denied for '{target_region}' — falling back to home='{home}'",
            )
            target_region = home
            # Re-augment search query with home region
            _sq = search_query
            # Strip any previous "in <region>" suffix
            import re as _re_sq

            _sq = _re_sq.sub(r"\s+in\s+\S+$", "", _sq).strip() or _sq
            search_query = f"{_sq} in {target_region}"
        else:
            # No usable home location — hard block
            denial = access["denial_message"]
            if lang_code != "en":
                denial = translate_response(denial, lang_code)
            save_conversation_turn(
                session_id, query_en, question, denial, access["denial_message"]
            )
            fut_spec.cancel()
            pool.shutdown(wait=False)
            _print_agent_timings(agent_timings, time.time() - pipeline_start)
            return {
                "answer": denial,
                "sources": [],
                "source_links": {},
                "suggested_questions": [],
                "show_feedback_prompt": False,
            }

    # ─── Observability — Log context ─────────────────────────────────────
    user_name = (
        user_details.get("name")
        if user_details
        else user_email.split("@")[0].replace(".", " ").title()
        if user_email
        else "Anonymous"
    )
    log_user_context(
        user_name=user_name,
        user_email=user_email or "",
        roles=access["roles"],
        home_loc=access["home_location"],
        allowed_locations=access["allowed_locations"],
        target_region=target_region,
        data_scope=data_scope,
    )

    # ─── Role-Based Query Rewriting ──────────────────────────────────────
    role_key = _get_role_key(access.get("roles", {}))
    role_rewrite = _rewrite_query_for_role(
        query_en,
        search_query,
        access.get("roles", {}),
        access.get("home_location", "Global"),
        user_opco,
    )
    search_query = role_rewrite["search_query"]
    role_context_note = role_rewrite["role_context_note"]

    if role_context_note:
        log_agent_step(
            "orchestrator",
            "ROLE_REWRITE",
            f"role={role_key} | augmented_query={search_query[:80]}",
        )

    # Augment search query with region
    if (
        target_region.lower() != "global"
        and target_region.lower() not in search_query.lower()
    ):
        search_query = f"{search_query} in {target_region}"

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 3 — Collect retrieval results
    # ═══════════════════════════════════════════════════════════════════════
    log_agent_step("retrieval_agent", "Collecting speculative retrieval results")

    speculative_results, t_spec = fut_spec.result()
    agent_timings["retrieval_agent (speculative)"] = t_spec

    if fut_refined is not None:
        # Collect refined retrieval (holiday / translated / region-specific)
        _reason = (
            "Holiday"
            if is_holiday_ctx
            else "Translated"
            if is_translated
            else "Region-specific"
        )
        log_agent_step("retrieval_agent", f"{_reason} — collecting refined retrieval")
        refined_results, t_refined = fut_refined.result()
        agent_timings["retrieval_agent (refined)"] = t_refined

        if _needs_region_refinement and not is_holiday_ctx:
            # Merge speculative + refined results — deduplicate by chunk ID.
            # This preserves good global results from speculative retrieval
            # while adding country-specific results from refined retrieval.
            seen_ids = set()
            merged = []
            # Refined results first (they have region context → better country-specific hits)
            for r in refined_results:
                rid = r.get("id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    merged.append(r)
            # Then speculative results for anything not already seen
            for r in speculative_results:
                rid = r.get("id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    merged.append(r)
            raw_results = merged
            log_agent_step(
                "retrieval_agent",
                f"Merged: {len(refined_results)} refined + {len(speculative_results)} spec "
                f"→ {len(merged)} unique",
            )
        else:
            raw_results = refined_results
    else:
        raw_results = speculative_results

    agent_timings["PARALLEL_PHASE1 (understand ‖ retrieval)"] = time.time() - _phase1
    pool.shutdown(wait=False)

    # ═══════════════════════════════════════════════════════════════════════
    # P-CARD SUB-PIPELINE — when guardrails detected a P-Card query and the
    # user is VP/Executive/Super Admin, switch to the strict P-Card pipeline
    # (same logic as /pcard/chat but inside the /chat flow).
    # ═══════════════════════════════════════════════════════════════════════
    if pcard_authorized:
        log_agent_step(
            "orchestrator", "PCARD_BRANCH", "Switching to P-Card sub-pipeline"
        )

        # Re-retrieve with P-Card index groups for gold-source documents
        pcard_index_groups = get_variant_index_groups("pcard")
        _t_pcard_ret = time.time()
        pcard_raw = search_vectors(query_en, top_k=50, index_groups=pcard_index_groups)
        agent_timings["retrieval_agent (pcard)"] = time.time() - _t_pcard_ret

        log_agent_step(
            "orchestrator",
            "PCARD_BRANCH",
            f"Retrieved {len(pcard_raw)} results for query: {query_en[:80]}",
        )

        # Prioritize gold-source chunks and apply strict filtering
        pcard_filtered = prioritize_and_filter_pcard_results(pcard_raw)

        if not pcard_filtered:
            log_agent_step(
                "orchestrator", "PCARD_BRANCH", "No match — returning fallback"
            )
            fallback_msg = PCARD_FALLBACK_MSG
            if lang_code != "en":
                fallback_msg = translate_response(PCARD_FALLBACK_MSG, lang_code)
            save_conversation_turn(
                session_id, query_en, question, fallback_msg, PCARD_FALLBACK_MSG
            )
            _print_agent_timings(agent_timings, time.time() - pipeline_start)
            return {
                "answer": fallback_msg,
                "sources": [],
                "source_links": {},
                "suggested_questions": [],
                "show_feedback_prompt": False,
            }

        # Delegate to shared P-Card pipeline (generation + validation + caching)
        pcard_result = run_pcard_pipeline(
            query_en=query_en,
            question_original=question,
            session_id=session_id,
            lang_code=lang_code,
            history_text_en=history_text_en,
            filtered_results=pcard_filtered,
            agent_timings=agent_timings,
            cache_key_prefix="pcard_chat",
        )

        total_time = time.time() - pipeline_start
        log_agent_step(
            "orchestrator",
            "PCARD_COMPLETE",
            f"Total: {total_time:.2f}s | Sources: {len(pcard_result.get('sources', []))}",
        )
        _print_agent_timings(agent_timings, total_time)
        return pcard_result

    log_agent_step(
        "reranking_agent", f"Filtering {len(raw_results)} results for {target_region}"
    )
    _t = time.time()
    reranked = rerank_and_filter(
        results=raw_results,
        target_region=target_region,
        search_query=search_query,
        history_text_en=history_text_en,
        is_followup=is_followup,
        priority_collections=variant_priority_collections,
        priority_categories=variant_priority_categories,
        apply_region_filter=_apply_region_filter,
        broad_region=broad_region,
    )
    agent_timings["reranking_agent"] = time.time() - _t
    filtered_results = reranked["filtered_results"]
    opco_note = reranked["opco_context_note"]
    context_text = reranked["context_text"]

    # DEBUG: Log filtered result sources for investigation
    if filtered_results:
        _src_summary = [
            f"  {i + 1}. {os.path.basename(r.get('source', '?'))} "
            f"[{r.get('collection', '?')}] "
            f"country={r.get('country', 'N/A')} "
            f"score={r.get('rank_score', r.get('distance', 0)):.3f}"
            for i, r in enumerate(filtered_results[:10])
        ]
        logger.info(
            f"[orchestrator] FILTERED SOURCES for generation "
            f"(target={target_region}):\n" + "\n".join(_src_summary)
        )

    # ─── Pre-generation fallback: if reranking returned empty, retry with broader search ──
    if not filtered_results or context_text == "No relevant context found.":
        log_agent_step(
            "retrieval_agent",
            "Pre-gen fallback: empty results, retrying with broader search",
        )
        _t = time.time()
        # Try broader search with expanded query and higher top_k
        broader_query = query_en  # Use raw English query without region suffix
        retry_results = search_vectors(
            broader_query, top_k=50, index_groups=variant_index_groups
        )
        if retry_results:
            retry_reranked = rerank_and_filter(
                retry_results,
                "global",
                broader_query,
                history_text_en,
                is_followup,
                priority_collections=variant_priority_collections,
                priority_categories=variant_priority_categories,
                apply_region_filter=_apply_region_filter,
                broad_region=broad_region,
            )
            if retry_reranked["filtered_results"]:
                filtered_results = retry_reranked["filtered_results"]
                opco_note = retry_reranked["opco_context_note"]
                context_text = retry_reranked["context_text"]
        agent_timings["pre_gen_fallback"] = time.time() - _t

    log_agent_step(
        "generation_agent", f"Generating answer (intent={intent}, role={role_key})"
    )
    _t = time.time()
    # Combine role context with employee context for generation
    combined_context_note = employee_context_note
    if role_context_note:
        combined_context_note = (
            f"{role_context_note}\n{employee_context_note}"
            if employee_context_note
            else role_context_note
        )
    answer_en = generate_answer(
        query=query_en,
        context_text=context_text,
        history_text_en=history_text_en if is_followup else "",
        target_region=target_region,
        user_opco=user_opco,
        intent=intent,
        is_greeting=is_greeting,
        opco_note=opco_note,
        employee_context_note=combined_context_note,
    )
    agent_timings["generation_agent"] = time.time() - _t

    # ─── Fallback Retry ──────────────────────────────────────────────────
    FALLBACK_PHRASES = [
        "i don't have information",
        "i don't have enough information",
        "i don't have specific",
        "i don't have a specific",
        "don't have enough information",
        "don't have sufficient information",
        "don't have specific information",
        "no relevant context found",
        "no information about that",
    ]
    if any(p in answer_en.lower() for p in FALLBACK_PHRASES):
        log_agent_step(
            "retrieval_agent",
            "Fallback retry — broader search with raw query_en, top_k=50",
        )
        _t = time.time()
        # Use raw English query WITHOUT region suffix for broader retrieval
        retry_results = search_vectors(
            query_en, top_k=50, index_groups=variant_index_groups
        )
        if retry_results:
            # Rerank with global scope to avoid excluding relevant results
            retry_reranked = rerank_and_filter(
                retry_results,
                "global",  # Broaden to global to catch cross-region content
                query_en,
                history_text_en,
                is_followup,
                priority_collections=variant_priority_collections,
                priority_categories=variant_priority_categories,
                apply_region_filter=False,  # Disable region filter for broader results
                broad_region=broad_region,
            )
            if retry_reranked["filtered_results"]:
                retry_answer = generate_answer(
                    query_en,
                    retry_reranked["context_text"],
                    history_text_en if is_followup else "",
                    target_region,  # Still tell generation the user's actual region
                    user_opco,
                    intent,
                    is_greeting,
                    retry_reranked["opco_context_note"],
                    employee_context_note,
                )
                if not any(p in retry_answer.lower() for p in FALLBACK_PHRASES):
                    answer_en = retry_answer
                    filtered_results = retry_reranked["filtered_results"]
                    context_text = retry_reranked["context_text"]
        agent_timings["fallback_retry"] = time.time() - _t

    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 5 — Translation ‖ Post-Validation ‖ Follow-up Suggestions
    # ═══════════════════════════════════════════════════════════════════════
    log_agent_step("orchestrator", "PHASE_5", "Translation ‖ Validation ‖ Suggestions")
    _grp = time.time()

    def _translate_task():
        t = time.time()
        r = translate_response(answer_en, lang_code)
        return r, time.time() - t

    def _validate_task():
        t = time.time()
        r = validate_and_attribute(
            answer_en, filtered_results, target_region=target_region
        )
        return r, time.time() - t

    def _suggest_task():
        t = time.time()
        r = _generate_followup_questions(
            query_en,
            answer_en,
            {"text": context_text, "region": target_region},
            lang_code,
        )
        return r, time.time() - t

    with ThreadPoolExecutor(max_workers=3) as pool_c:
        fut_trans = pool_c.submit(_translate_task)
        fut_valid = pool_c.submit(_validate_task)
        fut_suggest = pool_c.submit(_suggest_task) if not is_greeting else None

        answer, t_trans = fut_trans.result()
        validation, t_valid = fut_valid.result()
        if fut_suggest:
            suggested_qs_raw, t_suggest = fut_suggest.result()
        else:
            suggested_qs_raw, t_suggest = [], 0.0

    agent_timings["translation_agent"] = t_trans
    agent_timings["post_validation_agent"] = t_valid
    if t_suggest > 0:
        agent_timings["followup_suggestions"] = t_suggest
    agent_timings["PARALLEL_PHASE5 (translate ‖ validate ‖ suggest)"] = (
        time.time() - _grp
    )

    # HV disclaimer
    if hv_disclaimer:
        answer = f"{hv_disclaimer}\n\n{answer}"

    final_sources = validation["final_sources"]
    is_no_info = validation["is_no_info"]
    source_urls = validation.get("source_urls", {})
    if is_no_info:
        final_sources = []

    show_feedback = bool(final_sources and not is_no_info and not is_greeting)
    suggested_questions = suggested_qs_raw if show_feedback else []

    # Build source links: use ServiceNow article URL when available, otherwise
    # fall back to the GCS-backed /documents/ endpoint for PDFs.
    source_links = {}
    for s in final_sources:
        bn = os.path.basename(s)
        sn_url = source_urls.get(bn)
        if sn_url:
            source_links[s] = sn_url
        else:
            source_links[s] = f"/documents/{bn}"

    # ─── Cache the answer for repeated questions (3 hours) ────────────────
    # Never cache fallback/error/denial answers — they reflect transient failures
    # (e.g. BQ 403, empty retrieval) and should not poison future requests.
    _answer_en_lower = answer_en.lower()
    _NOCACHE_PHRASES = FALLBACK_PHRASES + [
        "do not have permission",
        "not have access",
        "unable to process",
        "access denied",
        "error occurred",
    ]
    _is_cacheable = (
        not is_greeting
        and not is_followup
        and not mentioned_employee
        and final_sources
        and not any(p in _answer_en_lower for p in _NOCACHE_PHRASES)
    )
    if _is_cacheable:
        answer_cache_key = (
            f"answer_cache:{variant}:{query_en.strip().lower()}:{target_region.lower()}"
        )
        answer_data = {
            "answer": answer,
            "answer_en": answer_en,
            "sources": final_sources,
            "source_links": source_links,
            "suggested_questions": suggested_questions,
            "suggested_questions_en": suggested_qs_raw,
            "show_feedback_prompt": show_feedback,
        }
        # Exact-match cache
        redis_cache.set(answer_cache_key, answer_data, ttl=CACHE_TTL_ANSWER)

        # Similarity-based semantic cache (with embedding)
        try:
            if _query_embedding_cache is None:
                embs = generate_embeddings([query_en])
                _query_embedding_cache = embs[0] if embs else None
            if _query_embedding_cache:
                redis_cache.store_semantic_cache(
                    query_en=query_en,
                    region=target_region.lower(),
                    role_key=role_key,
                    embedding=_query_embedding_cache,
                    answer_data=answer_data,
                    ttl=CACHE_TTL_ANSWER,
                    intent=intent,
                )
                log_agent_step(
                    "orchestrator",
                    "SEM_CACHE_STORE",
                    f"Stored semantic cache: region={target_region.lower()} role={role_key}",
                )
        except Exception as e:
            logger.error(f"Semantic cache store failed: {e}")

    # ─── Cache full session context (user profile + access + Q&A) ────────
    session_cache_key = f"session:{session_id}:latest"
    redis_cache.hset(
        session_cache_key,
        {
            "user_email": user_email or "",
            "user_details": user_details or {},
            "roles": access.get("roles", {}),
            "role_key": role_key,
            "home_location": access.get("home_location", "Global"),
            "allowed_locations": access.get("allowed_locations", ["Global"]),
            "access_allowed": access.get("allowed", True),
            "data_scope": data_scope,
            "last_query": question,
            "last_query_en": query_en,
            "last_intent": intent,
            "last_target_region": target_region,
            "last_language": lang_code,
            "last_answer": answer,
            "last_answer_en": answer_en,
            "last_sources": final_sources,
            "last_source_links": source_links,
            "last_suggested_questions": suggested_questions,
            "show_feedback_prompt": show_feedback,
            "timestamp": time.time(),
        },
        ttl=CACHE_TTL_SESSION,
    )

    # ─── Caching Agent — Save Turn ───────────────────────────────────────
    log_agent_step("caching_agent", "Saving conversation turn")
    _t = time.time()
    save_conversation_turn(
        session_id=session_id,
        user_query=query_en,
        user_original=question,
        answer=answer,
        answer_en=answer_en,
        sources=final_sources,
        source_links=source_links,
        suggested_questions=suggested_questions,
        show_feedback_prompt=show_feedback,
    )
    agent_timings["caching_agent (save)"] = time.time() - _t

    total_time = time.time() - pipeline_start
    log_agent_step(
        "orchestrator",
        "COMPLETE",
        f"Total: {total_time:.2f}s | Sources: {len(final_sources)}",
    )

    _print_agent_timings(agent_timings, total_time)

    return {
        "answer": answer,
        "sources": final_sources,
        "source_links": source_links,
        "suggested_questions": suggested_questions,
        "show_feedback_prompt": show_feedback,
    }


# ─── ADK Orchestrator Agent ─────────────────────────────────────────────────

orchestrator_agent = Agent(
    name="hd_skye_orchestrator",
    model="gemini-2.0-flash",
    description=(
        "HD SKYE Master Orchestrator — coordinates all sub-agents to answer "
        "HR policy questions with access control, multi-language support, and "
        "source attribution."
    ),
    instruction="""You are the HD SKYE Orchestrator Agent.
When a user asks a question, use the process_query tool to run the full
agentic pipeline: query understanding → guardrails → access control →
retrieval → reranking → generation → translation → validation → caching.
Return the structured response.""",
    tools=[process_query],
    sub_agents=[],  # Sub-agents are called via their tool functions directly
)
