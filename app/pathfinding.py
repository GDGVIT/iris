"""Breadth-first search over Wikipedia, with search state kept in Redis.

There is one algorithm here, not two. A search advances one or two *frontiers*:

* ``bidirectional`` (default) walks forward from the start page and backward
  from the target using backlinks, expanding whichever side is currently
  cheaper. The two meet in the middle, so it explores far fewer pages.
* ``bfs`` runs the forward frontier alone. The backward side still exists as a
  visited set holding only the target, which is exactly what "have we reached
  the target?" means — so termination and path reconstruction need no special
  casing.

State lives in Redis rather than memory: a set of visited titles, a hash of
parent pointers and a FIFO queue, all namespaced per search and deleted when it
finishes.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from app.config import Settings
from app.errors import InvalidRequestError, PathNotFoundError
from app.store import RedisStore
from app.wikipedia import PageFetched, WikipediaSource

logger = logging.getLogger(__name__)

Algorithm = Literal["bidirectional", "bfs"]
BIDIRECTIONAL: Algorithm = "bidirectional"
BFS: Algorithm = "bfs"
ALGORITHMS: tuple[Algorithm, ...] = (BIDIRECTIONAL, BFS)

ProgressCallback = Callable[["Progress"], None]
FetchLinks = Callable[[list[str], PageFetched | None], dict[str, list[str]]]


@dataclass(frozen=True, slots=True)
class Progress:
    """A snapshot of an in-flight search, sent to the client as it runs."""

    nodes_explored: int
    queue_size: int
    current_depth: int
    last_node: str
    elapsed: float


@dataclass(frozen=True, slots=True)
class PathResult:
    path: list[str]
    nodes_explored: int
    search_time: float

    @property
    def length(self) -> int:
        return len(self.path)


@dataclass(slots=True)
class Frontier:
    """One side of the search: its Redis keys and how it expands."""

    name: str
    queue_key: str
    visited_key: str
    parent_key: str
    fetch: FetchLinks
    max_depth: int
    exhausted: bool = False

    @property
    def keys(self) -> tuple[str, str, str]:
        return self.queue_key, self.visited_key, self.parent_key


@dataclass(slots=True)
class _Counters:
    """Search totals, written from the Wikipedia fetcher threads."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    nodes_explored: int = 0
    depth_by_side: dict[str, int] = field(default_factory=dict)

    def record(self, side: str, depth: int) -> tuple[int, int]:
        """Count one fetched page; return (total explored, combined depth).

        Combined depth sums both sides because a bidirectional search meeting
        at forward depth 2 and backward depth 2 has covered four hops of path.
        """
        with self.lock:
            self.nodes_explored += 1
            self.depth_by_side[side] = max(self.depth_by_side.get(side, 0), depth)
            return self.nodes_explored, sum(self.depth_by_side.values())


class PathFinder:
    """Finds a route between two Wikipedia pages."""

    def __init__(
        self,
        wikipedia: WikipediaSource,
        store: RedisStore,
        settings: Settings,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.wikipedia = wikipedia
        self.store = store
        self.settings = settings
        self.on_progress = on_progress

    def find(
        self, start: str, end: str, algorithm: Algorithm = BIDIRECTIONAL
    ) -> PathResult:
        """Search for a path from ``start`` to ``end``.

        The result is the first path found. It is not guaranteed to be globally
        shortest: Wikipedia caps how many links a single page will report, so
        BFS can miss edges out of very highly connected articles.
        """
        if not start or not end:
            raise InvalidRequestError("Start and end pages are required")
        if start == end:
            return PathResult(path=[start], nodes_explored=1, search_time=0.0)

        started = time.monotonic()
        counters = _Counters()
        forward, backward = self._build_frontiers(start, end, algorithm)
        all_keys = [*forward.keys, *backward.keys]

        try:
            self._seed(forward, start, backward, end, algorithm)
            meeting = self._search(forward, backward, counters, started)
            if meeting is None:
                raise PathNotFoundError(start, end, self.settings.max_search_depth)

            path = self._reconstruct(meeting, forward, backward)
            logger.info(
                "path_found",
                extra={
                    "start_page": start,
                    "end_page": end,
                    "path_length": len(path),
                    "nodes_explored": counters.nodes_explored,
                },
            )
            return PathResult(
                path=path,
                nodes_explored=counters.nodes_explored,
                search_time=time.monotonic() - started,
            )
        finally:
            self.store.delete(*all_keys)

    # --- Setup ------------------------------------------------------------

    def _build_frontiers(
        self, start: str, end: str, algorithm: Algorithm
    ) -> tuple[Frontier, Frontier]:
        search_id = uuid.uuid4().hex[:12]
        depth = self.settings.max_search_depth

        if algorithm == BIDIRECTIONAL:
            # Split the budget: meeting in the middle halves each side's work.
            forward_depth, backward_depth = (depth + 1) // 2, depth // 2
        else:
            forward_depth, backward_depth = depth, 0

        def side(name: str, max_depth: int, fetch: FetchLinks) -> Frontier:
            return Frontier(
                name=name,
                queue_key=f"bfs:{search_id}:{name}:queue",
                visited_key=f"bfs:{search_id}:{name}:visited",
                parent_key=f"bfs:{search_id}:{name}:parent",
                fetch=fetch,
                max_depth=max_depth,
            )

        return (
            side("forward", forward_depth, self.wikipedia.links),
            side("backward", backward_depth, self.wikipedia.backlinks),
        )

    def _seed(
        self,
        forward: Frontier,
        start: str,
        backward: Frontier,
        end: str,
        algorithm: Algorithm,
    ) -> None:
        self.store.set_add(forward.visited_key, start)
        self.store.set_add(backward.visited_key, end)
        self.store.queue_push(forward.queue_key, [_entry(start, 0)])

        if algorithm == BIDIRECTIONAL:
            self.store.queue_push(backward.queue_key, [_entry(end, 0)])
        else:
            # Plain BFS never expands backward; the visited set alone marks the
            # target, so the meeting check below is the termination test.
            backward.exhausted = True

        ttl = self.settings.search_state_ttl
        for key in (*forward.keys, *backward.keys):
            self.store.expire(key, ttl)

    # --- Search loop ------------------------------------------------------

    def _search(
        self, forward: Frontier, backward: Frontier, counters: _Counters, started: float
    ) -> str | None:
        while True:
            side = self._next_side(forward, backward)
            if side is None:
                return None

            other = backward if side is forward else forward
            meeting = self._expand(side, other, counters, started)
            if meeting is not None:
                return meeting

    def _next_side(self, forward: Frontier, backward: Frontier) -> Frontier | None:
        """Pick the cheaper frontier to expand, or ``None`` when both are spent."""
        candidates = [
            (self.store.queue_length(side.queue_key), side)
            for side in (forward, backward)
            if not side.exhausted
        ]
        live = [(size, side) for size, side in candidates if size > 0]
        if not live:
            return None
        return min(live, key=lambda pair: pair[0])[1]

    def _expand(
        self, side: Frontier, other: Frontier, counters: _Counters, started: float
    ) -> str | None:
        """Expand one batch of ``side``; return a meeting title if the sides touch."""
        batch = self.store.queue_pop(side.queue_key, self.settings.batch_size)
        if not batch:
            side.exhausted = True
            return None

        depth = batch[0]["depth"]
        if depth >= side.max_depth:
            # This side has spent its budget; anything deeper is out of reach.
            side.exhausted = True
            return None

        pages = [item["page"] for item in batch]
        logger.info(
            "expanding_batch",
            extra={"direction": side.name, "depth": depth, "batch_size": len(pages)},
        )

        found = side.fetch(
            pages, self._page_reporter(side, other, depth, counters, started)
        )
        meetings: list[str] = []

        for page in pages:
            neighbours = found.get(page, [])
            if not neighbours:
                continue

            unseen = [
                title
                for title, seen in zip(
                    neighbours,
                    self.store.set_contains(side.visited_key, neighbours),
                    strict=True,
                )
                if not seen
            ]
            if not unseen:
                continue

            touching = self.store.set_contains(other.visited_key, unseen)
            self.store.set_add(side.visited_key, *unseen)
            self.store.hash_set(side.parent_key, dict.fromkeys(unseen, page))
            self.store.queue_push(
                side.queue_key, [_entry(title, depth + 1) for title in unseen]
            )

            meetings += [
                title
                for title, touches in zip(unseen, touching, strict=True)
                if touches
            ]

        if not meetings:
            return None
        return min(meetings, key=lambda title: self._path_cost(title, side, other))

    def _page_reporter(
        self,
        side: Frontier,
        other: Frontier,
        depth: int,
        counters: _Counters,
        started: float,
    ) -> PageFetched:
        """Report progress as each page's links land, not once per batch.

        Spreading updates across the network round-trip is what makes the UI
        counter move smoothly instead of jumping once a batch completes. This
        runs on the fetcher threads, so every shared counter is behind a lock.
        """
        report = self.on_progress

        def page_fetched(title: str, _titles: list[str]) -> None:
            explored, combined_depth = counters.record(side.name, depth)
            if report is None:
                return
            report(
                Progress(
                    nodes_explored=explored,
                    queue_size=self.store.queue_length(side.queue_key)
                    + self.store.queue_length(other.queue_key),
                    current_depth=combined_depth,
                    last_node=title,
                    elapsed=round(time.monotonic() - started, 2),
                )
            )

        return page_fetched

    # --- Path reconstruction ---------------------------------------------

    def _reconstruct(
        self, meeting: str, forward: Frontier, backward: Frontier
    ) -> list[str]:
        """Walk the parent pointers out from the meeting page in both directions."""
        return self._walk_to_start(meeting, forward) + self._walk_to_end(
            meeting, backward
        )

    def _walk_to_start(self, node: str, forward: Frontier) -> list[str]:
        path = [node]
        while parent := self.store.hash_get(forward.parent_key, path[-1]):
            path.append(parent)
        path.reverse()
        return path

    def _walk_to_end(self, node: str, backward: Frontier) -> list[str]:
        chain: list[str] = []
        while child := self.store.hash_get(backward.parent_key, node):
            chain.append(child)
            node = child
        return chain

    def _path_cost(self, candidate: str, side: Frontier, other: Frontier) -> int:
        forward, backward = (side, other) if side.name == "forward" else (other, side)
        return len(self._walk_to_start(candidate, forward)) + len(
            self._walk_to_end(candidate, backward)
        )


def _entry(page: str, depth: int) -> dict[str, object]:
    return {"page": page, "depth": depth}
