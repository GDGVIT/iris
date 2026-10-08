"""The Celery tasks, run inline against the fake Wikipedia and fakeredis.

``find_path_task`` reports its own failures as return values rather than
exceptions, so every case below asserts on the returned payload.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from app.pathfinding import BFS, BIDIRECTIONAL
from app.tasks import find_path_task


def run(start="Python", end="Mathematics", algorithm=BIDIRECTIONAL) -> dict:
    return find_path_task.apply(args=(start, end, algorithm)).result


# --- Success --------------------------------------------------------------


@pytest.mark.parametrize("algorithm", [BIDIRECTIONAL, BFS])
def test_returns_the_path_and_its_statistics(app, algorithm):
    result = run(algorithm=algorithm)

    assert result["status"] == "SUCCESS"
    assert result["path"][0] == "Python"
    assert result["path"][-1] == "Mathematics"
    assert result["length"] == len(result["path"])
    assert result["algorithm"] == algorithm
    assert result["search_time"] > 0
    assert result["nodes_explored"] > 0


def test_search_stats_carry_what_the_ui_displays(app):
    stats = run()["search_stats"]

    assert stats["start_page"] == "Python"
    assert stats["end_page"] == "Mathematics"
    assert stats["final_depth"] == 3
    assert stats["search_completed"] is True
    assert stats["max_depth"] > 0


def test_searches_from_the_canonical_titles_not_what_was_typed(app, wikipedia):
    """An alias must be resolved before the frontier ever sees it.

    The search compares titles as strings and backlinks resolve nothing, so a
    frontier seeded with "python" explores an article that no link points at.
    """
    result = run(start="python", end="Maths")

    assert result["status"] == "SUCCESS"
    assert result["path"][0] == "Python"
    assert result["path"][-1] == "Mathematics"
    assert result["start_page"] == "Python"
    assert wikipedia.link_calls[0] == ["Python"]


def test_publishes_progress_while_searching(app):
    with patch.object(find_path_task, "update_state") as update_state:
        find_path_task.apply(args=("Python", "Mathematics", BIDIRECTIONAL))

    states = [call.kwargs["meta"] for call in update_state.call_args_list]

    assert len(states) > 2, (
        "progress must arrive during the search, not just at the ends"
    )
    assert all(
        call.kwargs["state"] == "PROGRESS" for call in update_state.call_args_list
    )
    for meta in states:
        assert meta["search_stats"]["start_page"] == "Python"
        assert meta["search_stats"]["end_page"] == "Mathematics"
        assert "max_depth" in meta["search_stats"]


# --- Caching --------------------------------------------------------------


def test_reuses_a_cached_path_instead_of_searching_again(app, wikipedia):
    first = run()
    calls_after_first = len(wikipedia.link_calls)

    second = run()

    assert second["path"] == first["path"]
    assert len(wikipedia.link_calls) == calls_after_first, "second run must not refetch"


def test_each_algorithm_caches_separately(app, store):
    run(algorithm=BIDIRECTIONAL)
    assert store.get(f"path:{BIDIRECTIONAL}:Python:Mathematics") is not None
    assert store.get(f"path:{BFS}:Python:Mathematics") is None


# --- Failure ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "code"),
    [
        ("Python", "Orphan", "PATH_NOT_FOUND"),
        ("Nowhere", "Mathematics", "PAGE_NOT_FOUND"),
        ("Python", "Nowhere", "PAGE_NOT_FOUND"),
        ("Python", "Mercury", "DISAMBIGUATION_PAGE"),
    ],
)
def test_reports_failures_as_a_result_with_a_code(app, start, end, code):
    result = run(start, end)

    assert result["status"] == "FAILURE"
    assert result["code"] == code
    assert result["error"]
    assert (result["start_page"], result["end_page"]) == (start, end)


def test_a_disambiguation_start_page_is_allowed(app, wikipedia):
    """Its links are still useful; only the destination must be unambiguous."""
    wikipedia.graph["Mercury"] = ["Mathematics"]

    assert run("Mercury", "Mathematics")["status"] == "SUCCESS"


def test_an_unexpected_crash_is_reported_not_raised(app):
    with patch("app.tasks.PathFinder.find", side_effect=RuntimeError("boom")):
        result = run()

    assert result["status"] == "FAILURE"
    assert result["code"] == "INTERNAL_ERROR"
    assert "boom" in result["error"]


def test_gives_up_after_exhausting_retries_on_a_transient_fault(app):
    """Retries are Celery's job; the last attempt must still answer the client."""
    with patch(
        "app.tasks.PathFinder.find", side_effect=requests.ConnectionError("down")
    ):
        result = find_path_task.apply(
            args=("Python", "Mathematics", BIDIRECTIONAL), retries=3
        ).result

    assert result["status"] == "FAILURE"
    assert result["code"] == "INTERNAL_ERROR"
    assert "down" in result["error"]
