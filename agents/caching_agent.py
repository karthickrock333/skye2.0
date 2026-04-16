"""
caching_agent.py
=================
Manages conversation history and response caching in Redis.
"""

from google.adk.agents import Agent
from tools.cache_tools import cache
from config import CACHE_TTL_HISTORY
import logging

logger = logging.getLogger("HD_SKYE_AGENT")


def get_conversation_history(session_id: str) -> dict:
    """Retrieve conversation history for a session."""
    key = f"history:{session_id}"
    history = cache.lrange(key)
    history_text = ""
    history_text_en = ""
    for turn in history:
        history_text += f"User: {turn['user']}\nAssistant: {turn['assistant']}\n"
        ast_en = turn.get("assistant_en", turn.get("assistant", ""))
        history_text_en += f"User: {turn['user']}\nAssistant: {ast_en}\n"
    return {
        "history": history,
        "history_text": history_text,
        "history_text_en": history_text_en,
    }


def save_conversation_turn(
    session_id: str,
    user_query: str,
    user_original: str,
    answer: str,
    answer_en: str,
    sources: list = None,
    source_links: dict = None,
    suggested_questions: list = None,
    show_feedback_prompt: bool = False,
) -> dict:
    """Save a conversation turn to Redis history."""
    key = f"history:{session_id}"
    cache.lpush(
        key,
        {
            "user": user_query,
            "user_orig": user_original,
            "assistant": answer,
            "assistant_en": answer_en,
            "sources": sources or [],
            "source_links": source_links or {},
            "suggested_questions": suggested_questions or [],
            "show_feedback_prompt": show_feedback_prompt,
        },
        ttl=CACHE_TTL_HISTORY,
    )  # Conversation history TTL (default 24h)
    return {"status": "saved"}


def clear_history(session_id: str) -> dict:
    """Clear conversation history for a session."""
    cache.delete(f"history:{session_id}")
    return {"status": "cleared"}


caching_agent = Agent(
    name="caching_agent",
    model="gemini-2.0-flash",
    description="Manages conversation history and response caching in Redis.",
    instruction="""You are the Caching Agent.
Use get_conversation_history to retrieve history.
Use save_conversation_turn to store new turns.
Use clear_history to reset a session.""",
    tools=[get_conversation_history, save_conversation_turn, clear_history],
)
