"""HTTP contract.

The UI in ``static/script.js`` reads these exact fields, so these tests double
as the guard against silently changing the shape it depends on.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

TASK_ID = "550e8400-e29b-41d4-a716-446655440000"


def search(client, start="Python", end="Mathematics", **extra):
    return client.post("/getPath", json={"start": start, "end": end, **extra})


# --- Starting a search ----------------------------------------------------


def test_accepts_a_search_and_returns_somewhere_to_poll(client):
    response = search(client)

    assert response.status_code == 202
    body = response.get_json()
    assert body["status"] == "IN_PROGRESS"
    assert body["task_id"]
    assert body["poll_url"] == f"/tasks/status/{body['task_id']}"
    assert (body["start_page"], body["end_page"]) == ("Python", "Mathematics")


def test_trims_surrounding_whitespace_from_page_titles(client):
    body = search(client, start="  Python  ", end=" Mathematics ").get_json()
    assert (body["start_page"], body["end_page"]) == ("Python", "Mathematics")


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"end": "Mathematics"}, "missing start"),
        ({"start": "Python"}, "missing end"),
        ({"start": "", "end": "Mathematics"}, "empty start"),
        ({"start": "Python", "end": "Python"}, "same page twice"),
        (
            {"start": "Python", "end": "Maths", "algorithm": "dijkstra"},
            "unknown algorithm",
        ),
    ],
)
def test_rejects_invalid_searches(client, payload, reason):
    response = client.post("/getPath", json=payload)

    assert response.status_code == 400, reason
    assert response.get_json()["error"] is True


def test_rejects_a_body_that_is_not_json(client):
    response = client.post("/getPath", data="start=Python", content_type="text/plain")
    assert response.status_code == 400


# --- Polling --------------------------------------------------------------
#
# The status endpoint is a translation layer over Celery's task states. Driving
# it with a stubbed result exercises every branch, including the PROGRESS state
# that a finished task has already left behind.


class StubResult:
    def __init__(self, state, info=None, result=None):
        self.state = state
        self.info = info
        self.result = result


@pytest.fixture
def task_state(monkeypatch):
    def set_state(**kwargs):
        monkeypatch.setattr(
            "app.routes.find_path_task.AsyncResult", lambda _id: StubResult(**kwargs)
        )

    return set_state


def test_a_queued_search_reads_as_pending(client, task_state):
    task_state(state="PENDING")

    body = client.get(f"/tasks/status/{TASK_ID}").get_json()

    assert body["status"] == "PENDING"
    assert body["message"]


def test_a_running_search_returns_its_progress(client, task_state):
    progress = {
        "status": "Searching...",
        "search_stats": {"nodes_explored": 42, "queue_size": 7, "last_node": "Comedy"},
        "search_time_elapsed": 1.5,
    }
    task_state(state="PROGRESS", info=progress)

    body = client.get(f"/tasks/status/{TASK_ID}").get_json()

    assert body["status"] == "IN_PROGRESS"
    assert body["progress"] == progress


def test_a_finished_search_returns_the_path(client, task_state):
    task_state(
        state="SUCCESS",
        result={
            "status": "SUCCESS",
            "path": ["Python", "Mathematics"],
            "length": 2,
            "search_time": 0.5,
            "nodes_explored": 9,
            "search_stats": {"search_completed": True},
        },
    )

    body = client.get(f"/tasks/status/{TASK_ID}").get_json()

    assert body["status"] == "SUCCESS"
    assert body["result"]["path"] == ["Python", "Mathematics"]
    assert body["result"]["nodes_explored"] == 9


def test_a_search_that_failed_is_reported_as_a_failure_not_a_success(
    client, task_state
):
    """The task returns its failures, so Celery still calls the task SUCCESS."""
    task_state(
        state="SUCCESS",
        result={
            "status": "FAILURE",
            "error": "No path found",
            "code": "PATH_NOT_FOUND",
        },
    )

    body = client.get(f"/tasks/status/{TASK_ID}").get_json()

    assert body["status"] == "FAILURE"
    assert body["code"] == "PATH_NOT_FOUND"
    assert "result" not in body


def test_a_crashed_task_surfaces_its_error(client, task_state):
    task_state(state="FAILURE", info=RuntimeError("worker died"))

    body = client.get(f"/tasks/status/{TASK_ID}").get_json()

    assert body["status"] == "FAILURE"
    assert "worker died" in body["error"]


def test_a_revoked_task_is_reported_as_revoked(client, task_state):
    """The UI relies on this to tell cancellation apart from failure."""
    task_state(state="REVOKED")

    assert client.get(f"/tasks/status/{TASK_ID}").get_json()["status"] == "REVOKED"


def test_rejects_a_malformed_task_id(client):
    response = client.get("/tasks/status/not-a-uuid")

    assert response.status_code == 404
    assert response.get_json()["code"] == "INVALID_TASK_ID"


# --- Cancelling -----------------------------------------------------------


def test_cancelling_revokes_the_task(client, task_state):
    task_state(state="PROGRESS")

    with patch("app.routes.celery.control.revoke") as revoke:
        response = client.delete(f"/tasks/{TASK_ID}")

    assert response.status_code == 200
    assert response.get_json()["revoked"] is True
    revoke.assert_called_once_with(TASK_ID, terminate=True, signal="SIGTERM")


def test_cancelling_can_skip_termination(client, task_state):
    task_state(state="PROGRESS")

    with patch("app.routes.celery.control.revoke") as revoke:
        client.delete(f"/tasks/{TASK_ID}?terminate=false")

    assert revoke.call_args.kwargs["terminate"] is False


def test_will_not_cancel_a_finished_task(client, task_state):
    task_state(state="SUCCESS")

    response = client.delete(f"/tasks/{TASK_ID}")

    assert response.status_code == 409
    assert response.get_json()["code"] == "TASK_ALREADY_COMPLETE"


def test_cancelling_a_malformed_task_id_is_a_404(client):
    assert client.delete("/tasks/not-a-uuid").status_code == 404


# --- System ---------------------------------------------------------------


def test_health_is_green_when_redis_answers(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "healthy",
        "redis": "healthy",
        "cache": "healthy",
    }


def test_health_is_503_when_redis_is_down(client, store):
    with patch.object(store, "ping", return_value=False):
        response = client.get("/health")

    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"


# --- UI and routing -------------------------------------------------------


def test_serves_the_ui_at_the_root(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"<html" in response.data.lower()


def test_sends_unknown_paths_to_the_ui(client):
    response = client.get("/some/deep/link")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_unknown_api_paths_are_a_json_404(client):
    response = client.get("/tasks/status")

    assert response.status_code == 404
    assert response.get_json()["error"] is True


def test_every_response_carries_cors_headers(client):
    assert client.get("/health").headers["Access-Control-Allow-Origin"] == "*"


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/tasks"), ("DELETE", "/tasks"), ("POST", "/cache/clear")],
)
def test_no_unauthenticated_admin_endpoints(client, method, path):
    """The API is public: nothing may cancel other users' searches or wipe caches."""
    assert client.open(path, method=method).status_code in (404, 405)


def test_serves_the_swagger_spec(client):
    spec = client.get("/apispec.json").get_json()

    assert "/getPath" in spec["paths"]
    assert spec["info"]["title"] == "Iris Wikipedia Pathfinder API"
