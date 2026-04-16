"""
retrieval_agent.py
===================
Searches the Vertex AI Matching Engine vector index for semantically similar
document chunks.  Retrieves chunk metadata from Firestore.
Optimised: searches multiple indices in parallel, driven by dynamic index
group configuration (see config.INDEX_GROUP_REGISTRY / ENABLED_INDEX_GROUPS).

Supports agent variant overrides — callers can pass an explicit list of
IndexGroupConfig objects to restrict which indexes are searched, enabling
variant-specific retrieval (e.g. P-Card agent only searches servicenow_kb
+ pcard indexes).
"""

from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
from google.cloud import aiplatform, discoveryengine_v1, firestore
from google.adk.agents import Agent
from config import (
    PROJECT_ID,
    REGION,
    TENANT,
    get_enabled_index_groups,
    IndexGroupConfig,
)
from tools.embedding_tools import generate_embeddings
from tools.cache_tools import cache
import logging

logger = logging.getLogger("HD_SKYE_AGENT")

# ─── Singleton clients — initialized once, reused across requests ────────────
_endpoint_cache: Dict[str, object] = {}
_db_cache: Dict[str, firestore.Client] = {}


def _get_endpoint(endpoint_id: str):
    global _endpoint_cache
    if endpoint_id not in _endpoint_cache:
        aiplatform.init(project=PROJECT_ID, location=REGION)
        if not endpoint_id:
            return None
        if "/" in endpoint_id:
            _endpoint_cache[endpoint_id] = aiplatform.MatchingEngineIndexEndpoint(
                endpoint_id
            )
        else:
            eps = aiplatform.MatchingEngineIndexEndpoint.list(
                filter=f'display_name="{endpoint_id}"'
            )
            _endpoint_cache[endpoint_id] = eps[0] if eps else None
    return _endpoint_cache.get(endpoint_id)


def _get_firestore_db(database: str) -> firestore.Client:
    """Return a cached Firestore client for the given database name."""
    if database not in _db_cache:
        _db_cache[database] = firestore.Client(project=PROJECT_ID, database=database)
    return _db_cache[database]


def _rerank_results(query: str, results: List[dict], top_k: int = 10) -> List[dict]:
    """Rerank results using Vertex AI Ranking API."""
    if not results or len(results) <= 1:
        return results
    try:
        client = discoveryengine_v1.RankServiceClient()
        records = [
            discoveryengine_v1.RankingRecord(id=r["id"], content=r["text"][:1000])
            for r in results
            if r.get("text")
        ]
        if not records:
            return results
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
    except Exception as e:
        logger.error(f"Reranking failed: {e}. Using original order.")
        return results[:top_k]


def _build_endpoint_groups(
    groups: List[IndexGroupConfig],
) -> List[Dict[str, object]]:
    """Merge index groups that share the same endpoint into a single entry.

    Returns a list of dicts like::

        [
            {
                "endpoint_id": "projects/.../indexEndpoints/...",
                "indices": [
                    {"deployed_index_id": "...", "collection": "..."},
                    ...
                ],
            },
            ...
        ]
    """
    by_endpoint: Dict[str, list] = {}
    for g in groups:
        by_endpoint.setdefault(g.endpoint_id, []).append(
            {
                "deployed_index_id": g.deployed_index_id,
                "firestore_database": g.firestore_database,
                "collection": g.firestore_collection,
            }
        )
    return [
        {"endpoint_id": eid, "indices": indices} for eid, indices in by_endpoint.items()
    ]


def search_vectors(
    query_text: str,
    top_k: int = 10,
    index_groups: Optional[List[IndexGroupConfig]] = None,
) -> List[dict]:
    """Search the vector index for similar chunks and return enriched results.

    Args:
        query_text: The search query text.
        top_k: Maximum number of results to return.
        index_groups: Optional explicit list of IndexGroupConfig objects to
            search.  When provided, these override the global
            ENABLED_INDEX_GROUPS.  Used by agent variants to restrict
            retrieval to variant-specific indexes.
    """
    # Include a variant fingerprint in the cache key so different variants
    # don't share cached results.
    variant_tag = ""
    if index_groups is not None:
        variant_tag = "|".join(sorted(g.name for g in index_groups))
    cache_key = f"search:{TENANT}:{variant_tag}:{query_text}:{top_k}"
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"[retrieval] CACHE HIT | {len(cached)} results for top_k={top_k}")
        return cached

    embeddings = generate_embeddings([query_text])
    if not embeddings:
        return []
    query_vector = embeddings[0]

    # Build endpoint_groups from the dynamic registry + enabled list.
    # When index_groups is provided (variant override), use those instead
    # of the global enabled list.
    # Groups sharing the same endpoint_id are merged so we open only one
    # connection per physical endpoint.
    enabled_groups = (
        index_groups if index_groups is not None else get_enabled_index_groups()
    )
    if not enabled_groups:
        logger.warning("No index groups enabled — nothing to search.")
        return []

    endpoint_groups = _build_endpoint_groups(enabled_groups)

    if not endpoint_groups:
        return []

    try:
        all_results = []

        def _search_endpoint_group(group):
            """Search all indices on a single endpoint IN PARALLEL.

            Previously iterated indices sequentially — with 4 deployed
            indexes on one endpoint each taking ~1.5-2s, this was ~7s.
            Now searches all indices concurrently, bringing it down to
            ~2s (limited by the slowest single index query).
            """
            ep = _get_endpoint(group["endpoint_id"])
            if not ep:
                return []
            indices = group["indices"]
            if len(indices) == 1:
                return _search_single_index(ep, indices[0], query_vector, top_k)
            # Parallel search across all deployed indexes on this endpoint
            results = []
            with ThreadPoolExecutor(max_workers=len(indices)) as idx_pool:
                futs = [
                    idx_pool.submit(
                        _search_single_index, ep, idx_info, query_vector, top_k
                    )
                    for idx_info in indices
                ]
                for fut in futs:
                    results.extend(fut.result())
            return results

        def _search_single_index(endpoint, idx_info, qvec, top_k):
            """Search a single index and retrieve Firestore metadata."""
            import time as _t

            _t0 = _t.time()
            dep_id = idx_info["deployed_index_id"]
            col = idx_info["collection"]
            db = _get_firestore_db(idx_info["firestore_database"])
            results = []
            if not any(d.id == dep_id for d in endpoint.deployed_indexes):
                logger.info(
                    f"[retrieval] Index {dep_id} not found on endpoint — skipping"
                )
                return results
            try:
                response = endpoint.find_neighbors(
                    deployed_index_id=dep_id,
                    queries=[qvec],
                    num_neighbors=top_k,
                )
                if response:
                    col_ref = db.collection(col)
                    doc_refs = [col_ref.document(n.id) for n in response[0]]
                    docs = db.get_all(doc_refs)
                    for neighbor, doc in zip(response[0], docs):
                        if doc.exists:
                            info = doc.to_dict()
                            text = info.get("text") or info.get("content") or "[Empty]"
                            source = (
                                info.get("source")
                                or info.get("doc_filename")
                                or info.get("metadata", {}).get("filename")
                                or "Unknown"
                            )
                            entry = {
                                "id": neighbor.id,
                                "distance": neighbor.distance,
                                "text": text,
                                "source": source,
                                "collection": col,
                            }

                            # ── Propagate metadata fields ──────────────
                            # New-schema collections (servicenow_kb, pcard,
                            # bulk_expense) have rich top-level fields.
                            # Old-schema collections (main HR, APAC payroll)
                            # have a nested ``metadata`` dict — we read both.

                            # Country (region filtering)
                            country = info.get("country")
                            if country:
                                entry["country"] = country

                            # Region — APAC / EMEA / Americas / null=Global
                            region = info.get("region")
                            if region:
                                entry["region"] = region

                            # Category ID — primary category field, 100%
                            # populated in new-schema (e.g. hr_policy,
                            # procurement, p_card_policy, bulk_expense)
                            category_id = info.get("category_id")
                            if category_id:
                                entry["category_id"] = category_id

                            # Category — sparse human-readable label
                            # (e.g. "P Card", "Bulk Expense")
                            category = info.get("category")
                            if category:
                                entry["category"] = category

                            # Policy domain — hr, security, compliance,
                            # finance, general, it
                            policy_domain = info.get("policy_domain")
                            if policy_domain:
                                entry["policy_domain"] = policy_domain

                            # Document type classification
                            document_type = info.get("document_type")
                            if document_type:
                                entry["document_type"] = document_type

                            # Language of the document
                            language = info.get("language")
                            if language:
                                entry["language"] = language

                            # Table flag — chunk contains tabular data
                            is_table = info.get("is_table")
                            if is_table is not None:
                                entry["is_table"] = is_table

                            # Section titles for richer context
                            chunk_title = info.get("chunk_title")
                            if chunk_title:
                                entry["chunk_title"] = chunk_title
                            section_title = info.get("section_title")
                            if section_title:
                                entry["section_title"] = section_title

                            # ServiceNow metadata for source linking
                            sn_url = info.get("servicenow_url")
                            if sn_url:
                                entry["servicenow_url"] = sn_url
                            sn_number = info.get("servicenow_number")
                            if sn_number:
                                entry["servicenow_number"] = sn_number

                            results.append(entry)
            except Exception as e:
                logger.error(f"Error querying index {dep_id}: {e}")
            _elapsed = _t.time() - _t0
            logger.info(
                f"[retrieval] Index {dep_id} ({col}): {len(results)} results in {_elapsed:.2f}s"
            )
            return results

        # Search all endpoint groups in parallel
        with ThreadPoolExecutor(max_workers=len(endpoint_groups)) as pool:
            futures = [pool.submit(_search_endpoint_group, g) for g in endpoint_groups]
            for fut in futures:
                all_results.extend(fut.result())

        if all_results and len(all_results) > 1:
            all_results = _rerank_results(query_text, all_results, top_k=top_k)
        else:
            all_results = all_results[:top_k]

        if all_results:
            cache.set(cache_key, all_results, ttl=3600)
        return all_results
    except Exception as e:
        logger.error(f"Vector search error: {e}")
        return []


retrieval_agent = Agent(
    name="retrieval_agent",
    model="gemini-2.0-flash",
    description="Searches vector index for relevant HR policy document chunks.",
    instruction="""You are the Retrieval Agent.
Use the search_vectors tool to find relevant document chunks for a given query.
Return the list of results with id, text, source, distance, and rank_score.""",
    tools=[search_vectors],
)
