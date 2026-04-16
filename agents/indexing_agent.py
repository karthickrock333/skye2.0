"""
indexing_agent.py
==================
Manages the Vertex AI Matching Engine index: creates, updates,
and deploys vector indexes.
"""
from google.cloud import aiplatform
from google.adk.agents import Agent
from config import PROJECT_ID, REGION, INDEX_ID, INDEX_ENDPOINT_ID, DEPLOYED_INDEX_ID
import logging

logger = logging.getLogger("HD_SKYE_AGENT")


def get_index_status() -> dict:
    """Check current index and endpoint status."""
    aiplatform.init(project=PROJECT_ID, location=REGION)
    try:
        if INDEX_ENDPOINT_ID:
            if "/" in INDEX_ENDPOINT_ID:
                ep = aiplatform.MatchingEngineIndexEndpoint(INDEX_ENDPOINT_ID)
            else:
                eps = aiplatform.MatchingEngineIndexEndpoint.list(filter=f'display_name="{INDEX_ENDPOINT_ID}"')
                ep = eps[0] if eps else None

            if ep:
                deployed = [{"id": d.id, "index": d.index} for d in ep.deployed_indexes]
                return {
                    "endpoint_name": ep.display_name,
                    "deployed_indexes": deployed,
                    "status": "active",
                }
        return {"status": "no_endpoint", "deployed_indexes": []}
    except Exception as e:
        logger.error(f"Error checking index: {e}")
        return {"status": "error", "error": str(e)}


def update_index(vectors: list, ids: list) -> dict:
    """
    Upsert vectors into the Matching Engine index.
    vectors: list of float lists, ids: list of string IDs.
    """
    aiplatform.init(project=PROJECT_ID, location=REGION)
    try:
        index = aiplatform.MatchingEngineIndex(INDEX_ID)
        index.upsert_datapoints(
            datapoints=[
                aiplatform.matching_engine.matching_engine_index_endpoint.UpsertDatapoint(
                    datapoint_id=did,
                    feature_vector=vec,
                )
                for did, vec in zip(ids, vectors)
            ]
        )
        return {"status": "success", "upserted": len(ids)}
    except Exception as e:
        logger.error(f"Index upsert error: {e}")
        return {"status": "error", "error": str(e)}


indexing_agent = Agent(
    name="indexing_agent",
    model="gemini-2.0-flash",
    description="Manages Vertex AI Matching Engine vector index operations.",
    instruction="Use get_index_status and update_index tools to manage the vector index.",
    tools=[get_index_status, update_index],
)
