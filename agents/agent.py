"""
agent.py (base agent utility)
==============================
Shared base utilities and the Agent registry for the HD SKYE agentic system.
Provides a central registry of all agents for discovery by the orchestrator.
"""
from google.adk.agents import Agent

# Import all agents
from agents.access_control_agent import access_control_agent
from agents.query_understanding_agent import query_understanding_agent
from agents.guardrails_agent import guardrails_agent
from agents.embedding_agent import embedding_agent
from agents.retrieval_agent import retrieval_agent
from agents.reranking_agent import reranking_agent
from agents.generation_agent import generation_agent
from agents.translation_agent import translation_agent
from agents.post_validation_agent import post_validation_agent
from agents.feedback_agent import feedback_agent
from agents.caching_agent import caching_agent
from agents.observability_agent import observability_agent
from agents.parsing_chunking_agent import parsing_chunking_agent
from agents.indexing_agent import indexing_agent
from agents.ingestion_agent import ingestion_agent

# Agent registry
AGENT_REGISTRY = {
    "access_control": access_control_agent,
    "query_understanding": query_understanding_agent,
    "guardrails": guardrails_agent,
    "embedding": embedding_agent,
    "retrieval": retrieval_agent,
    "reranking": reranking_agent,
    "generation": generation_agent,
    "translation": translation_agent,
    "post_validation": post_validation_agent,
    "feedback": feedback_agent,
    "caching": caching_agent,
    "observability": observability_agent,
    "parsing_chunking": parsing_chunking_agent,
    "indexing": indexing_agent,
    "ingestion": ingestion_agent,
}


def get_agent(name: str) -> Agent:
    """Get an agent from the registry by name."""
    return AGENT_REGISTRY.get(name)


def list_agents() -> list:
    """List all registered agent names."""
    return list(AGENT_REGISTRY.keys())
