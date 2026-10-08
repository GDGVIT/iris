"""Application settings, resolved once from the environment at startup.

A single frozen ``Settings`` object replaces the old config class hierarchy so
that every tunable has exactly one definition and one default. Consumers read
attributes (``settings.max_search_depth``) rather than dictionary lookups with
inline fallbacks, which is what let defaults drift apart in the past.

Precedence, lowest to highest: field default, per-environment default,
environment variable. Environment variable names are the field names upper-cased
(``max_search_depth`` → ``MAX_SEARCH_DEPTH``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Literal, Self

from dotenv import load_dotenv

from app.errors import ConfigError

load_dotenv()

Env = Literal["development", "testing", "production"]

_ENV_DEFAULTS: dict[Env, dict[str, object]] = {
    "development": {
        "log_level": "DEBUG",
        "links_cache_ttl": 3_600,
        "max_search_depth": 4,
        "task_soft_time_limit": 60,
        "task_time_limit": 120,
    },
    "testing": {
        "redis_url": "redis://localhost:6379/1",
        "links_cache_ttl": 60,
        "max_search_depth": 2,
        "batch_size": 10,
        "wikipedia_timeout": 5,
        "task_soft_time_limit": 10,
        "task_time_limit": 20,
    },
    "production": {},
}

_CASTS: dict[type, Callable[[str], object]] = {int: int, float: float, str: str}


@dataclass(frozen=True, slots=True)
class Settings:
    """Every runtime tunable, with its single source of truth."""

    env: Env = "development"
    secret_key: str = ""
    redis_url: str = "redis://localhost:6379/0"

    # Wikipedia API
    wikipedia_timeout: int = 15
    wikipedia_workers: int = 6
    """Concurrent requests per search. Search time is request latency times
    requests in flight; 3 -> 6 measured 1.3x faster with no extra throttling
    (scripts/benchmark.py). The request delay below still caps the rate."""
    wikipedia_max_pages: int = 3
    """Pagination requests per article — 3 covers up to 1,500 links."""
    wikipedia_request_delay: float = 0.1
    wikipedia_max_retries: int = 5

    # Cache lifetimes, in seconds
    links_cache_ttl: int = 86_400
    path_cache_ttl: int = 3_600
    page_cache_ttl: int = 7_200
    search_state_ttl: int = 3_600
    """Safety net so per-search Redis keys expire even if cleanup is skipped."""

    # Pathfinding
    max_search_depth: int = 6
    batch_size: int = 20

    # Celery
    task_soft_time_limit: int = 300
    task_time_limit: int = 600

    log_level: str = "INFO"
    log_dir: str = "logs"

    @property
    def debug(self) -> bool:
        return self.env == "development"

    @property
    def testing(self) -> bool:
        return self.env == "testing"

    @classmethod
    def from_env(cls, env: Env | None = None) -> Self:
        env = env or _resolve_env()
        values: dict[str, object] = {"env": env, **_ENV_DEFAULTS[env]}

        for field in fields(cls):
            raw = os.environ.get(field.name.upper()) if field.name != "env" else None
            if not raw:
                continue
            # `from __future__ import annotations` makes field.type a string, so
            # the concrete default is what tells us how to parse the override.
            cast = _CASTS.get(type(field.default), str)
            values[field.name] = cast(raw)

        settings = cls(**values)  # type: ignore[arg-type]
        settings.validate()
        return settings

    def validate(self) -> None:
        problems: list[str] = []
        if not self.secret_key:
            problems.append("SECRET_KEY is required")
        elif self.env == "production" and len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be at least 32 characters in production")
        if not self.redis_url.startswith(("redis://", "rediss://")):
            problems.append("REDIS_URL must start with redis:// or rediss://")
        if self.max_search_depth <= 0:
            problems.append("MAX_SEARCH_DEPTH must be positive")
        if self.wikipedia_timeout <= 0:
            problems.append("WIKIPEDIA_TIMEOUT must be positive")
        if problems:
            raise ConfigError(f"Invalid configuration: {'; '.join(problems)}")


def _resolve_env() -> Env:
    name = os.environ.get("FLASK_ENV", "development")
    return name if name in _ENV_DEFAULTS else "development"  # type: ignore[return-value]
