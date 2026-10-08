"""The search algorithm, running for real against fakeredis."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.errors import InvalidRequestError, PathNotFoundError
from app.pathfinding import BFS, BIDIRECTIONAL, Algorithm, PathFinder, Progress
from tests.conftest import GRAPH, FakeWikipedia

ALGORITHMS = [BIDIRECTIONAL, BFS]


@pytest.fixture
def finder(wikipedia, store, settings):
    return PathFinder(wikipedia, store, settings)


def is_walkable(path: list[str], graph: dict[str, list[str]]) -> bool:
    """Every consecutive pair must be a real edge in the graph."""
    return all(nxt in graph[cur] for cur, nxt in zip(path, path[1:], strict=False))


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_finds_the_shortest_path(finder, algorithm):
    """Two 4-step routes exist; either is correct, a longer one is not."""
    result = finder.find("Python", "Mathematics", algorithm)

    assert result.path[0] == "Python"
    assert result.path[-1] == "Mathematics"
    assert result.length == 4
    assert is_walkable(result.path, GRAPH)


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_finds_a_direct_neighbour(finder, algorithm):
    assert finder.find("Python", "Programming", algorithm).path == [
        "Python",
        "Programming",
    ]


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_reports_no_path_when_the_target_is_unreachable(finder, algorithm):
    with pytest.raises(PathNotFoundError, match="No path found"):
        finder.find("Python", "Orphan", algorithm)


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_identical_pages_need_no_search(finder, wikipedia, algorithm):
    result = finder.find("Python", "Python", algorithm)

    assert result.path == ["Python"]
    assert wikipedia.link_calls == [], "should not call Wikipedia at all"


def test_rejects_empty_page_titles(finder):
    with pytest.raises(InvalidRequestError):
        finder.find("", "Mathematics")


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_gives_up_beyond_the_depth_limit(wikipedia, store, settings, algorithm):
    """A path exists but is longer than the budget, so it must not be returned."""
    chain = {f"P{i}": [f"P{i + 1}"] for i in range(10)} | {"P10": []}
    finder = PathFinder(
        FakeWikipedia(chain), store, replace(settings, max_search_depth=2)
    )

    with pytest.raises(PathNotFoundError, match="within 2 steps"):
        finder.find("P0", "P10", algorithm)


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_deletes_all_search_state_when_done(finder, store, algorithm):
    finder.find("Python", "Mathematics", algorithm)

    assert not store._redis.keys("bfs:*"), "per-search keys must not outlive the search"


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_deletes_search_state_even_when_no_path_is_found(finder, store, algorithm):
    with pytest.raises(PathNotFoundError):
        finder.find("Python", "Orphan", algorithm)

    assert not store._redis.keys("bfs:*")


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_reports_progress_as_pages_arrive(wikipedia, store, settings, algorithm):
    updates: list[Progress] = []
    finder = PathFinder(wikipedia, store, settings, on_progress=updates.append)

    result = finder.find("Python", "Mathematics", algorithm)

    assert updates, "the UI needs progress before the search finishes"
    assert [u.nodes_explored for u in updates] == sorted(
        u.nodes_explored for u in updates
    )
    assert updates[-1].nodes_explored == result.nodes_explored
    assert all(u.last_node for u in updates)
    assert all(u.elapsed >= 0 for u in updates)


def test_counts_explored_pages_without_a_progress_callback(finder):
    """Node counts must not depend on anyone listening — an old bug did."""
    assert finder.find("Python", "Mathematics").nodes_explored > 0


def test_bidirectional_explores_less_than_plain_bfs(wikipedia, store, settings):
    """The whole point of searching from both ends."""
    wide = {"Start": [f"Hub{i}" for i in range(30)] + ["Target"]}
    wide |= {f"Hub{i}": ["Target"] for i in range(30)}
    wide["Target"] = []

    def explored(algorithm: Algorithm) -> int:
        finder = PathFinder(FakeWikipedia(wide), store, settings)
        return finder.find("Start", "Target", algorithm).nodes_explored

    assert explored(BIDIRECTIONAL) <= explored(BFS)


def test_backward_frontier_is_only_used_when_bidirectional(wikipedia, store, settings):
    PathFinder(wikipedia, store, settings).find("Python", "Mathematics", BFS)
    assert wikipedia.backlink_calls == []


def test_bidirectional_uses_backlinks(wikipedia, store, settings):
    PathFinder(wikipedia, store, settings).find("Python", "Mathematics", BIDIRECTIONAL)
    assert wikipedia.backlink_calls, "the reverse frontier must walk backlinks"
