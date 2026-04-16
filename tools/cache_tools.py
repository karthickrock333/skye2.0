"""
cache_tools.py - Redis caching utilities used across all agents.
Includes similarity-based semantic caching using query embeddings.
"""

import redis
import json
import math
import time as _time
import numpy as np
from config import (
    REDIS_HOST,
    REDIS_PORT,
    REDIS_PASSWORD,
    REDIS_KEY_PREFIX,
    SIMILARITY_THRESHOLD,
    CACHE_TTL_ANSWER,
    CACHE_TTL_COUNTER,
)


def _cosine_similarity(vec_a: list, vec_b: list) -> float:
    """Compute cosine similarity between two vectors using numpy (fast)."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


class RedisCache:
    """Thread-safe Redis cache wrapper with connection pooling and JSON serialization.

    All keys are automatically namespaced with REDIS_KEY_PREFIX (default "skye:")
    so that skye-agent keys can be flushed independently of other services sharing
    the same Redis instance.
    """

    def __init__(self):
        self._prefix = REDIS_KEY_PREFIX
        try:
            self._pool = redis.ConnectionPool(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD,
                decode_responses=True,
                max_connections=20,
            )
            self.client = redis.Redis(connection_pool=self._pool)
            self.client.ping()
            print(
                f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT} (prefix={self._prefix!r})"
            )
        except Exception as e:
            print(f"Failed to connect to Redis: {e}")
            self.client = None

    def _prefixed(self, key: str) -> str:
        """Add the configured prefix to a key."""
        return f"{self._prefix}{key}"

    def get(self, key: str):
        if not self.client:
            return None
        try:
            data = self.client.get(self._prefixed(key))
            return json.loads(data) if data else None
        except Exception as e:
            print(f"Redis get error: {e}")
            return None

    def set(self, key: str, value, ttl: int = 3600):
        if not self.client:
            return
        try:
            self.client.set(self._prefixed(key), json.dumps(value), ex=ttl)
        except Exception as e:
            print(f"Redis set error: {e}")

    def lpush(self, key: str, value, ttl: int = 0):
        if not self.client:
            return
        try:
            pk = self._prefixed(key)
            self.client.lpush(pk, json.dumps(value))
            self.client.ltrim(pk, 0, 9)
            if ttl > 0:
                self.client.expire(pk, ttl)
        except Exception as e:
            print(f"Redis lpush error: {e}")

    def lrange(self, key: str):
        if not self.client:
            return []
        try:
            data = self.client.lrange(self._prefixed(key), 0, -1)
            return [json.loads(d) for d in data][::-1]
        except Exception as e:
            print(f"Redis lrange error: {e}")
            return []

    def delete(self, key: str):
        if not self.client:
            return
        try:
            self.client.delete(self._prefixed(key))
        except Exception as e:
            print(f"Redis delete error: {e}")

    # ─── Hash operations for structured data ─────────────────────────────

    def hset(self, key: str, mapping: dict, ttl: int = 3600):
        """Store a dict as a Redis hash with JSON-serialized values."""
        if not self.client:
            return
        try:
            pk = self._prefixed(key)
            serialized = {k: json.dumps(v) for k, v in mapping.items()}
            self.client.hset(pk, mapping=serialized)
            if ttl > 0:
                self.client.expire(pk, ttl)
        except Exception as e:
            print(f"Redis hset error: {e}")

    def hget(self, key: str, field: str):
        """Get a single field from a Redis hash."""
        if not self.client:
            return None
        try:
            data = self.client.hget(self._prefixed(key), field)
            return json.loads(data) if data else None
        except Exception as e:
            print(f"Redis hget error: {e}")
            return None

    def hgetall(self, key: str) -> dict:
        """Get all fields from a Redis hash."""
        if not self.client:
            return {}
        try:
            data = self.client.hgetall(self._prefixed(key))
            return {k: json.loads(v) for k, v in data.items()} if data else {}
        except Exception as e:
            print(f"Redis hgetall error: {e}")
            return {}

    # ─── Admin / introspection helpers ───────────────────────────────────

    def ping(self) -> bool:
        if not self.client:
            return False
        try:
            return self.client.ping()
        except Exception:
            return False

    def dbsize(self) -> int:
        if not self.client:
            return 0
        try:
            return self.client.dbsize()
        except Exception:
            return 0

    def keys_by_pattern(self, pattern: str) -> list:
        """Return keys matching a glob pattern using SCAN (safe for production).
        The prefix is automatically prepended to the pattern, and stripped from
        the returned keys so callers see the same logical key names."""
        if not self.client:
            return []
        try:
            prefixed_pattern = f"{self._prefix}{pattern}"
            prefix_len = len(self._prefix)
            return [
                k[prefix_len:]
                for k in self.client.scan_iter(match=prefixed_pattern, count=100)
            ]
        except Exception as e:
            print(f"Redis scan error: {e}")
            return []

    def ttl(self, key: str) -> int:
        if not self.client:
            return -2
        try:
            return self.client.ttl(self._prefixed(key))
        except Exception:
            return -2

    def key_type(self, key: str) -> str:
        if not self.client:
            return "none"
        try:
            return self.client.type(self._prefixed(key))
        except Exception:
            return "none"

    # ─── Similarity-based semantic caching ───────────────────────────────

    def store_semantic_cache(
        self,
        query_en: str,
        region: str,
        role_key: str,
        embedding: list,
        answer_data: dict,
        ttl: int = None,
        intent: str = "",
    ):
        """
        Store a query+answer with its embedding for similarity matching.
        Uses a Redis hash per entry under 'sem_cache:{region}:{role_key}:{idx}'.
        An index list 'sem_cache_index:{region}:{role_key}' tracks all entries.
        Optionally stores intent/topic for cross-topic validation on read.
        """
        if ttl is None:
            ttl = CACHE_TTL_ANSWER
        if not self.client:
            return
        try:
            index_key = self._prefixed(f"sem_cache_index:{region}:{role_key}")
            counter_key = self._prefixed(f"sem_cache_counter:{region}:{role_key}")
            idx = self.client.incr(counter_key)
            # Set TTL on counter so it doesn't grow unbounded
            self.client.expire(counter_key, CACHE_TTL_COUNTER)
            entry_key = self._prefixed(f"sem_cache:{region}:{role_key}:{idx}")

            mapping = {
                "query": query_en,
                "embedding": json.dumps(embedding),
                "answer_data": json.dumps(answer_data),
                "timestamp": str(_time.time()),
            }
            if intent:
                mapping["intent"] = intent

            self.client.hset(entry_key, mapping=mapping)
            self.client.expire(entry_key, ttl)

            # Add to index
            self.client.sadd(index_key, entry_key)
            self.client.expire(index_key, ttl)
        except Exception as e:
            print(f"Redis store_semantic_cache error: {e}")

    def find_similar_cached(
        self, query_embedding: list, region: str, role_key: str, threshold: float = None
    ) -> dict | None:
        """
        Search semantic cache for a similar question.
        Uses Redis pipeline + batch numpy for speed.
        Returns the cached answer_data (with _similarity_score, _matched_query,
        and _matched_intent metadata) if similarity >= threshold, else None.
        """
        if not self.client:
            return None
        if threshold is None:
            threshold = SIMILARITY_THRESHOLD
        try:
            index_key = self._prefixed(f"sem_cache_index:{region}:{role_key}")
            entry_keys = self.client.smembers(index_key)
            if not entry_keys:
                return None

            # Batch fetch all entries via pipeline
            pipe = self.client.pipeline(transaction=False)
            key_list = list(entry_keys)
            for ek in key_list:
                pipe.hgetall(ek)
            results = pipe.execute()

            query_vec = np.array(query_embedding, dtype=np.float32)
            query_norm = np.linalg.norm(query_vec)
            if query_norm == 0:
                return None

            best_score = 0.0
            best_data = None
            best_query = None
            best_intent = ""
            expired_keys = []

            for ek, entry in zip(key_list, results):
                if not entry or "embedding" not in entry:
                    expired_keys.append(ek)
                    continue
                cached_emb = np.array(json.loads(entry["embedding"]), dtype=np.float32)
                cached_norm = np.linalg.norm(cached_emb)
                if cached_norm == 0:
                    continue
                score = float(
                    np.dot(query_vec, cached_emb) / (query_norm * cached_norm)
                )
                if score > best_score:
                    best_score = score
                    best_data = json.loads(entry["answer_data"])
                    best_query = entry.get("query", "")
                    best_intent = entry.get("intent", "")

            # Clean expired entries
            if expired_keys:
                pipe2 = self.client.pipeline(transaction=False)
                for ek in expired_keys:
                    pipe2.srem(index_key, ek)
                pipe2.execute()

            if best_score >= threshold and best_data:
                best_data["_similarity_score"] = round(best_score, 4)
                best_data["_matched_query"] = best_query
                best_data["_matched_intent"] = best_intent
                return best_data
            return None
        except Exception as e:
            print(f"Redis find_similar_cached error: {e}")
            return None

    def get_semantic_cache_stats(self, region: str = "*", role_key: str = "*") -> dict:
        """Get stats about semantic cache entries."""
        if not self.client:
            return {"total_entries": 0, "regions": []}
        try:
            pattern = f"{self._prefix}sem_cache_index:{region}:{role_key}"
            index_keys = [k for k in self.client.scan_iter(match=pattern, count=100)]
            prefix_len = len(self._prefix)
            stats = {"total_entries": 0, "buckets": []}
            for ik in index_keys:
                count = self.client.scard(ik)
                stats["total_entries"] += count
                stats["buckets"].append(
                    {"index_key": ik[prefix_len:], "entries": count}
                )
            return stats
        except Exception as e:
            print(f"Redis semantic cache stats error: {e}")
            return {"total_entries": 0, "buckets": []}


# Global singleton
cache = RedisCache()
