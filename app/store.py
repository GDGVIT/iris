"""Redis access — JSON values, sets, hashes and FIFO queues in one place.

Previously this was split across a cache class, a queue class and two abstract
base classes, all wrapping the same connection and re-raising the same error.
Redis errors now propagate as ``redis.RedisError``: Celery retries on them and
the Flask error handler turns them into a 503, so there is nothing left for a
per-method ``try``/``except`` to add.
"""

from __future__ import annotations

import json
from typing import Any

import redis


class RedisStore:
    """Thin, typed façade over the Redis commands this application uses."""

    def __init__(self, client: redis.Redis, default_ttl: int) -> None:
        self._redis = client
        self.default_ttl = default_ttl

    @classmethod
    def connect(cls, url: str, default_ttl: int) -> RedisStore:
        pool = redis.ConnectionPool.from_url(url, decode_responses=True)
        return cls(redis.Redis(connection_pool=pool), default_ttl)

    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except redis.RedisError:
            return False

    # --- JSON values ------------------------------------------------------

    def get(self, key: str) -> Any | None:
        raw = self._redis.get(key)
        return json.loads(raw) if raw is not None else None  # type: ignore[arg-type]

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self._redis.setex(key, ttl or self.default_ttl, json.dumps(value))

    def delete(self, *keys: str) -> None:
        if keys:
            self._redis.delete(*keys)

    def expire(self, key: str, seconds: int) -> None:
        self._redis.expire(key, seconds)

    # --- Sets -------------------------------------------------------------

    def set_add(self, key: str, *values: str) -> None:
        if values:
            self._redis.sadd(key, *values)

    def set_contains(self, key: str, values: list[str]) -> list[bool]:
        """Membership for each value, in order, in a single round-trip."""
        if not values:
            return []
        return [bool(hit) for hit in self._redis.smismember(key, values)]  # type: ignore[union-attr]

    # --- Hashes -----------------------------------------------------------

    def hash_set(self, key: str, mapping: dict[str, str]) -> None:
        if mapping:
            self._redis.hset(key, mapping=mapping)

    def hash_get(self, key: str, field: str) -> str | None:
        return self._redis.hget(key, field)  # type: ignore[return-value]

    # --- FIFO queues ------------------------------------------------------

    def queue_push(self, key: str, items: list[Any]) -> None:
        if items:
            self._redis.rpush(key, *[json.dumps(item) for item in items])

    def queue_pop(self, key: str, count: int) -> list[Any]:
        if count <= 0:
            return []
        raw = self._redis.lpop(key, count) or []
        return [json.loads(item) for item in raw]  # type: ignore[union-attr]

    def queue_length(self, key: str) -> int:
        return int(self._redis.llen(key))  # type: ignore[arg-type]
