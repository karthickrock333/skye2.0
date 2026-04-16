"""
ingestion_agent.py
===================
End-to-end document ingestion pipeline: parse → chunk → embed → store in
Firestore → upsert into vector index.
"""
from google.cloud import firestore
from google.adk.agents import Agent
from config import PROJECT_ID, FIRESTORE_COLLECTION, FIRESTORE_DATABASE, GCS_BUCKET_NAME
from agents.parsing_chunking_agent import read_document, chunk_text, detect_language
from tools.embedding_tools import generate_embeddings
from tools.gcs_tools import list_blobs, download_blob
import os
import logging
import tempfile

logger = logging.getLogger("HD_SKYE_AGENT")


def ingest_document(file_path: str, source_name: str = None) -> dict:
    """
    Ingest a single document: parse, chunk, embed, store.
    Returns summary of ingestion.
    """
    if not source_name:
        source_name = os.path.basename(file_path)

    # 1. Parse
    text = read_document(file_path)
    if not text.strip():
        return {"status": "error", "message": f"No text extracted from {source_name}"}

    # 2. Detect language
    lang = detect_language(text[:500])

    # 3. Chunk
    chunks = chunk_text(text)
    if not chunks:
        return {"status": "error", "message": f"No chunks produced from {source_name}"}

    # 4. Embed
    embeddings = generate_embeddings(chunks)
    if not embeddings or len(embeddings) != len(chunks):
        return {"status": "error", "message": f"Embedding failed for {source_name}"}

    # 5. Store in Firestore
    db = firestore.Client(project=PROJECT_ID, database=FIRESTORE_DATABASE)
    col = db.collection(FIRESTORE_COLLECTION)

    chunk_ids = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        doc_id = f"{source_name}_chunk_{i}"
        col.document(doc_id).set({
            "text": chunk,
            "source": source_name,
            "language": lang,
            "chunk_index": i,
            "embedding": embedding,
        })
        chunk_ids.append(doc_id)

    return {
        "status": "success",
        "source": source_name,
        "chunks_created": len(chunks),
        "language": lang,
        "chunk_ids": chunk_ids,
    }


def ingest_from_gcs(prefix: str = "pdf/") -> dict:
    """Ingest all documents from a GCS bucket prefix."""
    blobs = list_blobs(GCS_BUCKET_NAME, prefix=prefix)
    results = []
    for blob in blobs:
        if blob.name.endswith("/"):
            continue
        ext = os.path.splitext(blob.name)[1].lower()
        if ext not in (".pdf", ".docx"):
            continue

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name
        try:
            download_blob(GCS_BUCKET_NAME, blob.name, tmp_path)
            result = ingest_document(tmp_path, source_name=os.path.basename(blob.name))
            results.append(result)
        except Exception as e:
            results.append({"status": "error", "source": blob.name, "message": str(e)})
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    success = sum(1 for r in results if r.get("status") == "success")
    return {"total": len(results), "success": success, "failed": len(results) - success, "details": results}


ingestion_agent = Agent(
    name="ingestion_agent",
    model="gemini-2.0-flash",
    description="End-to-end document ingestion: parse, chunk, embed, store in Firestore.",
    instruction="Use ingest_document for single files, ingest_from_gcs for batch GCS ingestion.",
    tools=[ingest_document, ingest_from_gcs],
)
