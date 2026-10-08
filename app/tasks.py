"""Celery tasks.

``find_path_task`` always returns a :class:`TaskResult`. Failures are values,
not exceptions, so that a page that does not exist reads back to the client the
same way whether it was caught during validation or deep inside the search.
Only genuinely transient faults — Redis or Wikipedia being unreachable — escape
as exceptions, which is what drives Celery's retry.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, NotRequired, TypedDict

import redis
import requests
from celery import states

from app import celery, services
from app.errors import (
    DisambiguationError,
    ErrorCode,
    IrisError,
    PageNotFoundError,
    WikipediaAPIError,
)
from app.pathfinding import (
    BIDIRECTIONAL,
    Algorithm,
    PathFinder,
    PathResult,
    Progress,
)

logger = logging.getLogger(__name__)

PROGRESS = "PROGRESS"
"""Custom Celery state for an in-flight search; its meta is the progress payload."""

RETRYABLE = (requests.RequestException, redis.RedisError, WikipediaAPIError)


class SearchStats(TypedDict, total=False):
    """The progress payload the UI renders. Field names are part of the API."""

    nodes_explored: int
    queue_size: int
    current_depth: int
    max_depth: int
    last_node: str
    start_page: str
    end_page: str
    final_depth: int
    search_completed: bool


class TaskResult(TypedDict):
    status: str
    start_page: str
    end_page: str
    error: NotRequired[str]
    code: NotRequired[str]
    path: NotRequired[list[str]]
    length: NotRequired[int]
    search_time: NotRequired[float]
    nodes_explored: NotRequired[int]
    algorithm: NotRequired[str]
    search_stats: NotRequired[SearchStats]


@celery.task(bind=True, autoretry_for=RETRYABLE, retry_backoff=True, max_retries=3)
def find_path_task(
    self: Any, start_page: str, end_page: str, algorithm: Algorithm = BIDIRECTIONAL
) -> TaskResult:
    """Find a path between two Wikipedia pages, reporting progress as it goes.

    ---
    Progress is published as Celery ``PROGRESS`` state and polled by the UI.
    """
    logger.info(
        "pathfinding_started",
        extra={"start_page": start_page, "end_page": end_page, "algorithm": algorithm},
    )

    settings = services().settings
    requested = start_page, end_page

    def publish(stage: str, stats: SearchStats, elapsed: float = 0.0) -> None:
        self.update_state(
            state=PROGRESS,
            meta={
                "status": stage,
                "search_stats": {
                    "start_page": start_page,
                    "end_page": end_page,
                    "max_depth": settings.max_search_depth,
                    **stats,
                },
                "search_time_elapsed": elapsed,
            },
        )

    publish("Validating pages...", {"nodes_explored": 0, "current_depth": 0})

    try:
        # Rebind to the canonical titles: `publish` and the success payload
        # close over these names, so everything downstream reports the pages
        # actually searched rather than whatever casing was typed. `requested`
        # keeps the originals, so errors still echo the input.
        start_page, end_page = _check_pages_exist(start_page, end_page)

        publish(
            "Starting pathfinding search...",
            {
                "nodes_explored": 0,
                "current_depth": 0,
                "queue_size": 1,
                "last_node": start_page,
            },
        )

        def on_progress(progress: Progress) -> None:
            publish(
                "Searching...",
                {
                    "nodes_explored": progress.nodes_explored,
                    "queue_size": progress.queue_size,
                    "current_depth": progress.current_depth,
                    "last_node": progress.last_node,
                },
                elapsed=progress.elapsed,
            )

        result = _find_path(start_page, end_page, algorithm, on_progress)

    except IrisError as exc:
        logger.warning(
            "pathfinding_failed", extra={"code": exc.code, "error": exc.message}
        )
        return _failure(*requested, exc.message, exc.code)
    except RETRYABLE as exc:
        if self.request.retries >= self.max_retries:
            logger.error("pathfinding_retries_exhausted", extra={"error": str(exc)})
            return _failure(
                *requested,
                f"Service unavailable after retries: {exc}",
                ErrorCode.INTERNAL_ERROR,
            )
        raise
    except Exception as exc:
        logger.error("pathfinding_crashed", extra={"error": str(exc)}, exc_info=True)
        return _failure(
            *requested, f"Unexpected error: {exc}", ErrorCode.INTERNAL_ERROR
        )

    logger.info(
        "pathfinding_completed",
        extra={
            "path_length": result.length,
            "search_time": round(result.search_time, 3),
        },
    )
    return {
        "status": states.SUCCESS,
        "start_page": start_page,
        "end_page": end_page,
        "path": result.path,
        "length": result.length,
        "search_time": result.search_time,
        "nodes_explored": result.nodes_explored,
        "algorithm": algorithm,
        "search_stats": {
            "start_page": start_page,
            "end_page": end_page,
            "max_depth": settings.max_search_depth,
            "nodes_explored": result.nodes_explored,
            "final_depth": max(result.length - 1, 0),
            "search_completed": True,
        },
    }


def _failure(start_page: str, end_page: str, message: str, code: str) -> TaskResult:
    """The one failure shape this task ever returns."""
    return {
        "status": states.FAILURE,
        "start_page": start_page,
        "end_page": end_page,
        "error": message,
        "code": code,
    }


def _check_pages_exist(start_page: str, end_page: str) -> tuple[str, str]:
    """Reject missing pages, and return the canonical titles to search with.

    A disambiguation page is allowed as the *start*: its links are still useful
    for getting somewhere else.

    The resolved titles matter to the search, not just to the response. The
    frontier compares titles as strings, and ``list=backlinks`` resolves nothing
    at all — seeded with a redirect it returns that redirect page's handful of
    incoming links rather than the article's. Searching from what the user typed
    is how "gibson guitars" turns a 20-second search into a hopeless one.
    """
    wikipedia = services().wikipedia
    resolved: list[str] = []

    for title in (start_page, end_page):
        status = wikipedia.page_status(title)
        if not status.exists:
            raise PageNotFoundError(title)
        if title == end_page and status.is_disambiguation:
            raise DisambiguationError(title, status.resolved_title)
        resolved.append(status.resolved_title)

    return resolved[0], resolved[1]


def _find_path(
    start_page: str,
    end_page: str,
    algorithm: Algorithm,
    on_progress: Callable[[Progress], None],
) -> PathResult:
    """Run the search, serving a cached result when the pair was seen recently."""
    app = services()
    cache_key = f"path:{algorithm}:{start_page}:{end_page}"

    if cached := app.store.get(cache_key):
        logger.info(
            "path_cache_hit", extra={"start_page": start_page, "end_page": end_page}
        )
        return PathResult(**cached)

    finder = PathFinder(app.wikipedia, app.store, app.settings, on_progress)
    result = finder.find(start_page, end_page, algorithm)
    app.store.set(cache_key, asdict(result), ttl=app.settings.path_cache_ttl)
    return result
