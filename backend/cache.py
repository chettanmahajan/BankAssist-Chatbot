"""Redis cache wrapper. If Redis is unavailable, treats every call as a miss."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import redis

logger = logging.getLogger(__name__)


REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_TTL_SECONDS = int(os.getenv("REDIS_TTL_SECONDS", "3600"))


class CacheClient:
    def __init__(self, host: str = REDIS_HOST, port: int = REDIS_PORT, ttl: int = REDIS_TTL_SECONDS):
        self._ttl = ttl
        self._enabled = True
        try:
            self._client = redis.Redis(
                host=host,
                port=port,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
                decode_responses=True,
            )
            self._client.ping()
            logger.info("redis cache connected at %s:%s", host, port)
        except Exception as exc:
            logger.warning("redis unavailable (%s); cache disabled", exc)
            self._enabled = False
            self._client = None  # type: ignore[assignment]

    @property
    def enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def key_for(query: str, session_id: str) -> str:
        digest = hashlib.sha256(f"{session_id}|{query.strip().lower()}".encode("utf-8")).hexdigest()
        return f"banking:chat:{digest}"

    def get(self, key: str) -> dict[str, Any] | None:
        if not self._enabled:
            return None
        try:
            payload = self._client.get(key)
        except Exception as exc:
            logger.warning("redis get failed (%s); treating as miss", exc)
            return None
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        if not self._enabled:
            return
        try:
            self._client.setex(key, self._ttl, json.dumps(value))
        except Exception as exc:
            logger.warning("redis set failed (%s); ignoring", exc)

    def stats(self) -> dict[str, Any]:
        return {"enabled": self._enabled, "ttl_seconds": self._ttl}


_GLOBAL_CACHE: CacheClient | None = None


def get_cache() -> CacheClient:
    global _GLOBAL_CACHE
    if _GLOBAL_CACHE is None:
        _GLOBAL_CACHE = CacheClient()
    return _GLOBAL_CACHE
