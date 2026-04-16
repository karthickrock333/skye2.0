"""
embedding_tools.py - Embedding generation using Vertex AI text-embedding-004.
Used by the embedding_agent for both ingestion and query-time embedding.
"""
from typing import List
import time
from google.api_core import exceptions
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel
from config import EMBEDDING_MODEL

# ─── Singleton model — loaded once, reused across all requests ────────────
_embedding_model = None

def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL)
    return _embedding_model


def generate_embeddings(texts: List[str], model=None) -> List[List[float]]:
    """Generate embeddings with batching and retries."""
    if not texts:
        return []

    if model is None:
        model = get_embedding_model()

    BATCH_SIZE = 100
    all_embeddings: List[List[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = [TextEmbeddingInput(text=t, task_type="RETRIEVAL_DOCUMENT") for t in texts[i : i + BATCH_SIZE]]
        for attempt in range(3):
            try:
                embeddings = model.get_embeddings(batch)
                all_embeddings.extend([e.values for e in embeddings])
                break
            except (exceptions.ServiceUnavailable, exceptions.DeadlineExceeded, Exception) as e:
                if attempt < 2:
                    time.sleep(2 * (2 ** attempt))
                else:
                    print(f"Embedding failed after 3 attempts: {e}")
                    return []
    return all_embeddings
