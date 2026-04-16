"""
pcard_orchestrator.py
======================
Self-contained P-Card pipeline that runs when the /pcard/chat endpoint is hit.
This uses the P-Card standalone agent's strict logic:

  1. PNG gold-source only (PCard_Allowable_NonAllowable.png / pcard_gold_source.md)
  2. Footnote-triggered secondary PDF inclusion ((*) → Gift Policy, (**) → 3P Policy)
  3. P-Card-specific LLM prompt (Procurement Card Policy Expert)
  4. No access control, no region filtering, no role rewriting
  5. Fallback to CorporateCard@hitachidigital.com when no match found
  6. Strips (*)/(**) markers from generated answer

This module reuses SKYE 2.0 shared utilities (search_vectors, translate, cache)
but does NOT touch the main orchestrator pipeline.
"""

import os
import re
import json
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from vertexai.generative_models import GenerativeModel

from config import (
    LLM_MODEL,
    CACHE_TTL_ANSWER,
    get_llm_generation_config,
    get_variant_index_groups,
)
from tools.cache_tools import cache as redis_cache
from agents.caching_agent import get_conversation_history, save_conversation_turn
from agents.retrieval_agent import search_vectors
from agents.translation_agent import translate_response
from agents.query_understanding_agent import (
    detect_and_translate,
    is_translation_request,
    identify_explicit_language,
    translate_text,
    is_small_talk,
)
from agents.post_validation_agent import validate_and_attribute
from agents.observability_agent import log_agent_step

logger = logging.getLogger("HD_SKYE_AGENT")

PCARD_FALLBACK_MSG = (
    "There is currently no clear answer available based on the information "
    "in the P-Card policies. For further clarification or investigation, "
    "please contact CorporateCard@hitachidigital.com."
)

PCARD_GREETING_MSG = (
    "Hello! How can I assist you today? I'm SKYE, your P-Card policy companion. "
    "Feel free to ask me anything about P-Card allowable or non-allowable purchases."
)

# ─── Thread-safe singleton LLM model ─────────────────────────────────────────
_pcard_llm = None
_pcard_llm_lock = threading.Lock()


def _get_llm():
    global _pcard_llm
    if _pcard_llm is None:
        with _pcard_llm_lock:
            if _pcard_llm is None:
                _pcard_llm = GenerativeModel(
                    LLM_MODEL, generation_config=get_llm_generation_config()
                )
    return _pcard_llm


# ─── P-Card-Specific Prompt ──────────────────────────────────────────────────


def _build_pcard_prompt(query: str, context_text: str, history_text: str) -> str:
    return f"""You are a Procurement Card Policy Expert agent.
Your goal is to provide helpful, human-like answers about P-Card policies. 
The answers should fall into three categories: Non allowable / Allowable / Conditional - requires review
## WRITING STYLE RULES:
- **Tone**: Conversational and helpful, NOT mechanical or legalistic.
- **FORBIDDEN PHRASES**: Do NOT say "is listed as", "specifically listed", "falls under the category of".
- **GOLD STANDARD STYLE**: Instead of "Yes, books are listed as allowed", say "Yes, you can use your P-Card for books."
- **Footnote Decoding**: When the source data contains (*) or (**) markers, decode them into plain English. Do NOT include the (*) or (**) symbols in your answer.
  - (*) refers to the Global Employee Gift and Celebration Policy.
  - (**) refers to the Third Party, Gifts, Travel and Entertainment Policy.
  - NEVER write (*) or (**) in the final answer text.
  - CRITICAL: Do NOT just say "must comply with [Policy Name]" and stop. If the context documents contain content from that policy, you MUST summarize the key relevant rules, limits, or conditions from it. For example, mention spending limits, approval requirements, eligible occasions, or any restrictions. The user should get a complete answer without needing to look up the policy themselves.
## RESPONSE STRUCTURE:
1. **Direct Answer**: Start with a single, clear, conversational sentence giving the bottom line.
2. **Contextual Detail**: Provide 2-3 helpful sentences. Explain conditions and footnotes here.
   - **CRITICAL**: Do NOT add any headers like "Short Summary" or "Detailed Summary" for these parts.
3. - Use **BULLET POINTS ONLY IF THERE IS A LIST** of items.
   - If there is only one piece of information, display it as a normal paragraph without bullets.
   - One bullet per distinct policy point if listing.
   - Clean the raw text: remove strange markdown markers or redundant symbols.
## SOURCE RULES:
- Your primary reference is `PCard_Allowable_NonAllowable.png` (the P-Card allowable/non-allowable table).
- The CONTEXT DOCUMENTS may also contain content from secondary policies like the Global Employee Gift and Celebration Policy or Third Party, Gifts, Travel and Entertainment Policy. If these are present in the context, you MUST use them to provide specific details (limits, rules, conditions, eligible occasions, etc.) — do NOT just name-drop the policy.
- NEVER say "which has specific guidelines" or "please refer to the policy" — instead, extract and present those guidelines from the context.
- DO NOT use internal knowledge and DO NOT make up any policies by yourself.
- If no clear match is found in "CONTEXT DOCUMENTS", respond ONLY with: "{PCARD_FALLBACK_MSG}"
{history_text}
## CONTEXT DOCUMENTS (POLICY CORPUS):
{context_text}
## USER QUESTION:
{query}
## YOUR ANSWER (v2.3 Conversational Mode):
"""


# ─── P-Card Strict Context Filtering ─────────────────────────────────────────

_GOLD_SOURCE_NAMES = ["PCard_Allowable_NonAllowable.png", "pcard_gold_source.md"]
_GIFT_PDF = "Global Employee Gift and Celebration Policy v.2.1.pdf"
_THIRD_PDF = "Third Party Gifts Travel and Entertainment Policy.pdf"


def _filter_context_strictly(results: list) -> list:
    """
    P-Card strict source filtering:
      1. Separate PNG/gold-source chunks from everything else.
      2. No PNG chunks → return [] (triggers fallback email message).
      3. Inspect PNG text for (*)/(**) footnote markers:
         - (*) → include Global Employee Gift Policy PDF.
         - (**) → include Third Party Gifts/Travel/Entertainment Policy PDF.
      4. Otherwise → return PNG chunks ONLY.
    """
    png_chunks = [
        r for r in results if any(n in r.get("source", "") for n in _GOLD_SOURCE_NAMES)
    ]
    other_chunks = [
        r
        for r in results
        if not any(n in r.get("source", "") for n in _GOLD_SOURCE_NAMES)
    ]

    if not png_chunks:
        logger.info("[pcard] Strict Rule: No PNG chunks found. Triggering fallback.")
        return []

    combined_png_text = " ".join(r.get("text", "") for r in png_chunks)
    needs_3p_policy = bool(re.search(r"\(\*\*\)", combined_png_text))
    needs_gift_policy = bool(re.search(r"\(\*\)(?!\*)", combined_png_text))

    extra_chunks = []
    if needs_gift_policy:
        extra_chunks += [r for r in other_chunks if _GIFT_PDF in r.get("source", "")]
        logger.info(f"[pcard] Strict Rule: (*) found → including '{_GIFT_PDF}'")
    if needs_3p_policy:
        extra_chunks += [r for r in other_chunks if _THIRD_PDF in r.get("source", "")]
        logger.info(f"[pcard] Strict Rule: (**) found → including '{_THIRD_PDF}'")

    filtered = png_chunks + extra_chunks
    logger.info(
        f"[pcard] Strict Rule: PNG={len(png_chunks)}, "
        f"(*){needs_gift_policy}, (**){needs_3p_policy}, extra={len(extra_chunks)}"
    )
    return filtered


# ─── P-Card Result Prioritization & Filtering ────────────────────────────────


def prioritize_and_filter_pcard_results(raw_results: list) -> list:
    """
    Sort by distance, prioritize gold-source PNG chunks, dedup, and apply
    strict context filtering.  Returns an empty list when no gold-source
    chunks are found (caller should return the fallback message).

    This is the single place for the prioritization + dedup + strict
    filtering logic, used by both process_pcard_query() and the P-Card
    sub-pipeline inside the main orchestrator.
    """
    if not raw_results:
        return []

    raw_results.sort(key=lambda x: x.get("distance", 1.0))

    png_results = [
        r
        for r in raw_results
        if any(n in r.get("source", "") for n in _GOLD_SOURCE_NAMES)
    ]
    other_results = [
        r
        for r in raw_results
        if not any(n in r.get("source", "") for n in _GOLD_SOURCE_NAMES)
    ]

    top_results = png_results + other_results[:30]
    seen_ids = set()
    deduped = []
    for r in top_results:
        rid = r.get("id", id(r))
        if rid not in seen_ids:
            deduped.append(r)
            seen_ids.add(rid)

    return _filter_context_strictly(deduped[:35])


# ─── P-Card Follow-Up Suggestions ────────────────────────────────────────────


def _generate_pcard_followups(query: str, answer: str, context: str) -> list:
    try:
        prompt = f"""Suggest 3 concise follow-up questions a user might ask after this P-Card Q&A.
Output a raw JSON array of 3 strings only. No markdown, no explanation.
Question: {query}
Answer: {answer}
Context excerpt: {context[:2000]}"""
        text = _get_llm().generate_content(prompt).text.strip()
        text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
        questions = json.loads(text)
        if isinstance(questions, list):
            return [str(q) for q in questions[:3]]
        return []
    except Exception as e:
        logger.error(f"[pcard] Suggestion generation error: {e}")
        return []


# ─── Timing Helper ───────────────────────────────────────────────────────────


def _print_pcard_timings(agent_timings: dict, total_time: float):
    print("\n" + "═" * 75)
    print("           P-CARD PARALLEL AGENT EXECUTION SUMMARY")
    print("═" * 75)
    for name, elapsed in agent_timings.items():
        marker = "  ⚡" if "PARALLEL" in name else "    "
        print(f"{marker} {name:<50} {elapsed:.3f}s")
    print("─" * 75)
    sum_individual = sum(
        v for k, v in agent_timings.items() if "PARALLEL" not in k and "PHASE" not in k
    )
    print(f"  Sum of all agents (if sequential): {sum_individual:.3f}s")
    print(f"  Actual wall time (with parallel):  {total_time:.3f}s")
    if total_time > 0:
        saved = sum_individual - total_time
        factor = sum_individual / total_time if total_time > 0 else 1.0
        print(f"  Time saved by parallelization:     {saved:.3f}s")
        print(f"  Speedup factor:                    {factor:.2f}x")
    print("═" * 75 + "\n")


# ─── Main P-Card Pipeline ────────────────────────────────────────────────────


def process_pcard_query(
    question: str,
    session_id: str = "default",
    teams_metadata: dict = None,
    data_scope: str = "regional",
) -> dict:
    """
    P-Card-specific pipeline.  Runs independently of the main SKYE orchestrator.

    Key differences from the main pipeline:
      - Uses P-Card-specific LLM prompt (Procurement Card Policy Expert)
      - Strict PNG gold-source filtering with footnote-triggered PDF inclusion
      - No access control, no region filtering, no role rewriting
      - Fallback directs to CorporateCard@hitachidigital.com
    """
    pipeline_start = time.time()
    agent_timings = {}
    log_agent_step(
        "pcard_orchestrator", "START", f"Q: {question[:50]}... | Session: {session_id}"
    )
    logger.info("P-Card Orchestrator: PARALLEL mode active")

    variant_index_groups = get_variant_index_groups("pcard")
    pool = ThreadPoolExecutor(max_workers=4)

    # ═══ PHASE 1 — Parallel: Translation ‖ History ‖ Explicit Language ════

    _p1 = time.time()

    def _detect_translate():
        t = time.time()
        return detect_and_translate(question), time.time() - t

    def _fetch_history():
        t = time.time()
        return get_conversation_history(session_id), time.time() - t

    def _identify_lang():
        t = time.time()
        return identify_explicit_language(question), time.time() - t

    fut_translate = pool.submit(_detect_translate)
    fut_history = pool.submit(_fetch_history)
    fut_explicit_lang = pool.submit(_identify_lang)

    tr_info, t_translate = fut_translate.result()
    agent_timings["detect_and_translate"] = t_translate
    question_en = tr_info["translated_text"]
    user_lang = tr_info["original_language_code"]
    log_agent_step("pcard_orchestrator", f"Detected language: {user_lang}")

    # ─── Greeting shortcut ────────────────────────────────────────────────
    if is_small_talk(question_en):
        explicit_lang, t_lang = fut_explicit_lang.result()
        agent_timings["identify_explicit_language"] = t_lang
        final_target_lang = explicit_lang or user_lang
        greeting = PCARD_GREETING_MSG
        if final_target_lang != "en":
            greeting = translate_text(greeting, final_target_lang) or greeting
        pool.shutdown(wait=False)
        _print_pcard_timings(agent_timings, time.time() - pipeline_start)
        return {
            "answer": greeting,
            "sources": [],
            "source_links": {},
            "suggested_questions": [],
            "show_feedback_prompt": False,
        }

    # ═══ PHASE 2 — Speculative Retrieval ‖ Translation-Request Check ═════

    _p2 = time.time()

    def _speculative_retrieval():
        t = time.time()
        return search_vectors(
            question_en, top_k=50, index_groups=variant_index_groups
        ), time.time() - t

    def _check_translation_req():
        t = time.time()
        return is_translation_request(question), time.time() - t

    fut_retrieval = pool.submit(_speculative_retrieval)
    fut_is_trans = pool.submit(_check_translation_req)

    # Collect remaining Phase 1 results
    hist_data, t_history = fut_history.result()
    agent_timings["get_conversation_history"] = t_history
    history_text_en = hist_data.get("history_text_en", "")

    explicit_lang, t_lang = fut_explicit_lang.result()
    agent_timings["identify_explicit_language"] = t_lang
    final_target_lang = explicit_lang or user_lang

    agent_timings["PARALLEL_PHASE1 (translate ‖ history ‖ lang)"] = time.time() - _p1

    # ─── Answer-level cache check ─────────────────────────────────────────
    answer_cache_key = f"answer_cache:pcard:{question_en.strip().lower()}"
    cached_answer = redis_cache.get(answer_cache_key)
    if cached_answer:
        log_agent_step("pcard_orchestrator", "CACHE HIT — returning cached answer")
        pool.shutdown(wait=False)
        _print_pcard_timings(agent_timings, time.time() - pipeline_start)
        return cached_answer

    # ─── Translation-request handling ─────────────────────────────────────
    _is_trans, t_trans_check = fut_is_trans.result()
    agent_timings["is_translation_request"] = t_trans_check

    if _is_trans and history_text_en:
        log_agent_step("pcard_orchestrator", "Detected translation request for history")
        parts = history_text_en.split("Assistant:")
        last_answer = parts[-1].split("User:")[0].strip() if len(parts) > 1 else ""
        if last_answer:
            final_translated = translate_response(last_answer, final_target_lang)
            trans_suggestions = []
            hist_turns = hist_data.get("history", [])
            if hist_turns:
                prev_suggestions = hist_turns[0].get("suggested_questions", [])
                if prev_suggestions and final_target_lang != "en":
                    trans_suggestions = [
                        translate_text(q, final_target_lang) for q in prev_suggestions
                    ]
                elif prev_suggestions:
                    trans_suggestions = prev_suggestions
            pool.shutdown(wait=False)
            _print_pcard_timings(agent_timings, time.time() - pipeline_start)
            return {
                "answer": final_translated,
                "sources": [],
                "source_links": {},
                "suggested_questions": trans_suggestions,
                "show_feedback_prompt": False,
            }

    # ═══ PHASE 3 — Collect Retrieval Results ═════════════════════════════

    raw_results, t_retrieval = fut_retrieval.result()
    agent_timings["retrieval_agent (speculative)"] = t_retrieval
    agent_timings["PARALLEL_PHASE2 (retrieval ‖ trans_check)"] = time.time() - _p2

    if not raw_results:
        return _pcard_fallback(final_target_lang, agent_timings, pipeline_start, pool)

    # ═══ STEP 4 — P-Card Strict Reranking & Filtering ════════════════════

    top_results = prioritize_and_filter_pcard_results(raw_results)

    if not top_results:
        log_agent_step("pcard_orchestrator", "No PNG match — returning fallback")
        return _pcard_fallback(final_target_lang, agent_timings, pipeline_start, pool)

    # Hand off to shared pipeline core
    result = run_pcard_pipeline(
        query_en=question_en,
        question_original=question,
        session_id=session_id,
        lang_code=final_target_lang,
        history_text_en=history_text_en,
        filtered_results=top_results,
        agent_timings=agent_timings,
        cache_key_prefix="pcard_standalone",
    )

    pool.shutdown(wait=False)
    total_time = time.time() - pipeline_start
    _print_pcard_timings(agent_timings, total_time)
    log_agent_step(
        "pcard_orchestrator",
        "COMPLETE",
        f"Total: {total_time:.2f}s | Sources: {len(result.get('sources', []))}",
    )

    return result


# ─── Shared P-Card Pipeline Core ─────────────────────────────────────────────


def run_pcard_pipeline(
    query_en: str,
    question_original: str,
    session_id: str,
    lang_code: str,
    history_text_en: str,
    filtered_results: list,
    agent_timings: dict,
    cache_key_prefix: str = "pcard",
) -> dict:
    """
    Shared P-Card generation + validation + caching pipeline.

    Called by:
      - ``process_pcard_query()`` (the /pcard/chat standalone endpoint)
      - The P-Card sub-pipeline branch inside ``orchestrator.process_query()``

    Args:
        query_en:           English-translated user query.
        question_original:  Raw user question (original language).
        session_id:         Conversation session id.
        lang_code:          Target response language code (e.g. "en", "ja").
        history_text_en:    English conversation history text.
        filtered_results:   Pre-filtered retrieval results (already through
                            ``_filter_context_strictly``).
        agent_timings:      Mutable dict to record timing breakdowns into.
        cache_key_prefix:   Cache key prefix — use different values for /chat
                            vs /pcard/chat to prevent cross-endpoint cache leaks.

    Returns:
        Standard response dict with answer, sources, source_links, etc.
    """
    context_text = "\n\n".join(
        f"SOURCE: {r['source']}\nCONTENT: {r['text']}" for r in filtered_results
    )
    final_context_sources = sorted(
        {os.path.basename(r["source"]) for r in filtered_results}
    )
    logger.info(f"[pcard] Final context sources: {final_context_sources}")

    # ── Generate answer ──────────────────────────────────────────────────
    log_agent_step("pcard_pipeline", "Generating P-Card answer")
    _t_gen = time.time()
    prompt = _build_pcard_prompt(query_en, context_text, history_text_en)
    try:
        answer_en = _get_llm().generate_content(prompt).text.strip()
    except Exception as e:
        logger.error(f"[pcard] LLM generation failed: {e}")
        answer_en = PCARD_FALLBACK_MSG
    # Strip footnote markers from answer
    answer_en = re.sub(r"\s*\(\*{1,2}\)", "", answer_en)
    agent_timings["generation_agent (pcard)"] = time.time() - _t_gen

    # ── Parallel: Validation | Translation | Suggestions ─────────────────
    _p_post = time.time()

    def _post_validate():
        t = time.time()
        return validate_and_attribute(answer_en, filtered_results), time.time() - t

    def _post_translate():
        t = time.time()
        try:
            return translate_response(answer_en, lang_code), time.time() - t
        except Exception as e:
            logger.error(f"[pcard] Answer translation failed: {e}")
            return answer_en, time.time() - t

    def _post_suggestions():
        t = time.time()
        return _generate_pcard_followups(
            query_en, answer_en, context_text
        ), time.time() - t

    with ThreadPoolExecutor(max_workers=3) as post_pool:
        fut_validate = post_pool.submit(_post_validate)
        fut_suggestions = post_pool.submit(_post_suggestions)
        fut_trans_answer = (
            post_pool.submit(_post_translate) if lang_code != "en" else None
        )

        validation, t_validate = fut_validate.result()
        agent_timings["post_validation_agent (pcard)"] = t_validate

        suggested_questions_en, t_suggest = fut_suggestions.result()
        agent_timings["followup_suggestions (pcard)"] = t_suggest

        if fut_trans_answer:
            final_answer, t_trans_answer = fut_trans_answer.result()
            agent_timings["translation_agent (pcard)"] = t_trans_answer
        else:
            final_answer = answer_en

    agent_timings["PARALLEL_PCARD_POST (validate | translate | suggest)"] = (
        time.time() - _p_post
    )

    final_sources = validation["final_sources"]
    is_no_info = validation["is_no_info"]
    if is_no_info:
        final_sources = []

    show_feedback = bool(final_sources and not is_no_info)
    if not show_feedback:
        suggested_questions_en = []

    # Translate suggestions if needed
    final_suggested = suggested_questions_en
    if lang_code != "en" and suggested_questions_en:
        try:
            final_suggested = [
                translate_text(q, lang_code) for q in suggested_questions_en
            ]
        except Exception as e:
            logger.error(f"[pcard] Suggestion translation failed: {e}")

    # ── Cache & save ─────────────────────────────────────────────────────
    source_links = {s: f"/documents/{os.path.basename(s)}" for s in final_sources}
    result = {
        "answer": final_answer,
        "sources": final_sources,
        "source_links": source_links,
        "suggested_questions": final_suggested,
        "show_feedback_prompt": show_feedback,
    }

    answer_cache_key = f"answer_cache:{cache_key_prefix}:{query_en.strip().lower()}"
    redis_cache.set(answer_cache_key, result, ttl=CACHE_TTL_ANSWER)

    save_conversation_turn(
        session_id=session_id,
        user_query=query_en,
        user_original=question_original,
        answer=final_answer,
        answer_en=answer_en,
        sources=final_sources,
        source_links=source_links,
        suggested_questions=final_suggested,
        show_feedback_prompt=show_feedback,
    )

    return result


def _pcard_fallback(target_lang: str, timings: dict, start: float, pool) -> dict:
    """Return the standard P-Card fallback response."""
    final_msg = PCARD_FALLBACK_MSG
    if target_lang != "en":
        final_msg = (
            translate_text(PCARD_FALLBACK_MSG, target_lang) or PCARD_FALLBACK_MSG
        )
    pool.shutdown(wait=False)
    _print_pcard_timings(timings, time.time() - start)
    return {
        "answer": final_msg,
        "sources": [],
        "source_links": {},
        "suggested_questions": [],
        "show_feedback_prompt": False,
    }
