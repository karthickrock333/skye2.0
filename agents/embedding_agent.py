"""
embedding_agent.py
===================
Generates vector embeddings for text (queries or document chunks)
using Vertex AI text-embedding-004.
"""
from google.adk.agents import Agent
from tools.embedding_tools import generate_embeddings
from typing import List


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Generate embeddings for a list of texts."""
    return generate_embeddings(texts)


def embed_query(query: str) -> List[float]:
    """Generate a single embedding for a query string."""
    results = generate_embeddings([query])
    return results[0] if results else []


embedding_agent = Agent(
    name="embedding_agent",
    model="gemini-2.0-flash",
    description="Generates vector embeddings for text using Vertex AI text-embedding-004.",
    instruction="""You are the Embedding Agent.
Generate embeddings for provided text using the embed_texts or embed_query tools.
Return the embedding vectors.""",
    tools=[embed_texts, embed_query],
)
