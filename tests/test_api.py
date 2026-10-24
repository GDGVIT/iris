"""HTTP contract.

The UI in ``static/script.js`` reads these exact fields, so these tests double
as the guard against silently changing the shape it depends on.
"""

from __future__ import annotations

import json
from dataclasses import replace
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


def use_settings(app, **changes):
    current = app.extensions["iris"]
    app.extensions["iris"] = replace(
        current, settings=replace(current.settings, **changes)
    )


def test_limits_how_many_searches_one_client_starts(app, client):
    use_settings(app, search_rate_limit=2)

    statuses = [search(client).status_code for _ in range(3)]

    assert statuses == [202, 202, 429]
    refused = search(client)
    assert refused.get_json()["code"] == "RATE_LIMITED"
    assert 0 < int(refused.headers["Retry-After"]) <= 60


def test_rate_limits_are_per_client(app, client):
    use_settings(app, search_rate_limit=1)

    assert search(client).status_code == 202
    other = client.post(
        "/getPath",
        json={"start": "Python", "end": "Mathematics"},
        environ_base={"REMOTE_ADDR": "10.0.0.2"},
    )
    assert other.status_code == 202


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


# --- Streaming ------------------------------------------------------------


def events(response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.get_data(as_text=True).splitlines()
        if line.startswith("data: ")
    ]


def test_streams_status_changes_until_the_search_ends(app, client, monkeypatch):
    use_settings(app, stream_interval=0)
    progress = {"search_stats": {"nodes_explored": 3}}
    states = iter(
        [
            StubResult("PENDING"),
            StubResult("PROGRESS", info=progress),
            StubResult("PROGRESS", info=progress),
            StubResult("SUCCESS", result={"status": "SUCCESS", "path": ["A", "B"]}),
        ]
    )
    monkeypatch.setattr(
        "app.routes.find_path_task.AsyncResult", lambda _id: next(states)
    )

    response = client.get(f"/tasks/stream/{TASK_ID}")

    assert response.mimetype == "text/event-stream"
    assert [e["status"] for e in events(response)] == [
        "PENDING",
        "IN_PROGRESS",
        "SUCCESS",
    ], "an unchanged status is not sent twice"
    assert events(response)[-1]["result"]["path"] == ["A", "B"]


def test_a_stream_closes_after_its_time_limit(app, client, task_state):
    use_settings(app, stream_interval=0, stream_max_seconds=0)
    task_state(state="PENDING")

    response = client.get(f"/tasks/stream/{TASK_ID}")

    assert [e["status"] for e in events(response)] == ["PENDING"]
    assert response.get_data(as_text=True).startswith("retry: ")


def test_streaming_a_malformed_task_id_is_a_404(client):
    assert client.get("/tasks/stream/not-a-uuid").status_code == 404


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


# --- Suggestions and link context ---------------------------------------


def test_suggests_titles_for_a_prefix(client):
    body = client.get("/api/suggest?q=mer").get_json()

    assert body["query"] == "mer"
    assert body["titles"] == [
        {"title": "Mercury", "description": "", "is_disambiguation": True}
    ]


def test_an_empty_suggestion_query_returns_nothing(client):
    assert client.get("/api/suggest?q=").get_json()["titles"] == []


def test_explains_a_step_with_the_sentence_that_links_it(client):
    body = client.get("/api/context?from=Python&to=Programming").get_json()

    assert body["from"] == "Python"
    assert body["to"] == "Programming"
    assert body["where"] == "prose"
    assert body["before"] + body["link"] + body["after"] == (
        "Python relates to Programming."
    )


def test_context_needs_both_ends(client):
    response = client.get("/api/context?from=Python")

    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_REQUEST"


def test_lighter_endpoints_share_their_own_rate_limit(app, client):
    use_settings(app, api_rate_limit=1)

    assert client.get("/api/suggest?q=py").status_code == 200
    assert client.get("/api/suggest?q=py").status_code == 429
    assert search(client).status_code == 202, "searches are counted separately"


# --- Sharing ----------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n"


def test_draws_a_share_image_for_a_found_route(client, store):
    store.set("route:Python:Mathematics", ["Python", "Monty Python", "Mathematics"])

    response = client.get("/api/og.png?from=Python&to=Mathematics")

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data.startswith(PNG)
    assert "max-age=86400" in response.headers["Cache-Control"]


def test_draws_a_share_image_for_a_route_not_yet_found(client):
    response = client.get("/api/og.png?from=Python&to=Orphan")

    assert response.data.startswith(PNG)
    assert "max-age=300" in response.headers["Cache-Control"], "may be found soon"


def test_a_shared_link_unfurls_into_its_own_route(client, store):
    store.set("route:Python:Mathematics", ["Python", "Monty Python", "Mathematics"])

    page = client.get("/?from=Python&to=Mathematics").get_data(as_text=True)

    assert "<title>Python → Mathematics in 2 links · Iris</title>" in page
    assert (
        'property="og:image" content="http://localhost/api/og.png?from=Python&amp;to=Mathematics"'
        in page
    )
    assert "Python → Monty Python → Mathematics" in page


def test_share_tags_escape_what_the_link_carries(client):
    page = client.get('/?from=<i>x</i>&to=a"b').get_data(as_text=True)

    assert "<i>x</i>" not in page
    assert "&lt;i&gt;x&lt;/i&gt;" in page
    assert "a&quot;b" in page


def test_share_previews_use_the_public_scheme_behind_a_proxy(client):
    page = client.get(
        "/?from=Python&to=Mathematics",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "iris.example"},
    ).get_data(as_text=True)

    assert 'content="https://iris.example/api/og.png?' in page


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


def test_unknown_pages_are_a_real_404_page(client):
    response = client.get("/some/deep/link")

    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert b"No page here" in response.data


@pytest.mark.parametrize(
    ("path", "kind"), [("/favicon.ico", "image/"), ("/robots.txt", "text/plain")]
)
def test_serves_root_files_crawlers_and_browsers_ask_for(client, path, kind):
    """Not redirected to the UI: a crawler must get the file itself."""
    response = client.get(path)

    assert response.status_code == 200
    # .ico's registered type differs between platforms' mimetypes tables
    assert response.mimetype.startswith(kind)


def test_robots_lets_previews_through_but_not_the_api(client):
    robots = client.get("/robots.txt").get_data(as_text=True)

    assert "Allow: /api/og.png" in robots
    assert "Disallow: /api/" in robots


def test_responses_carry_basic_security_headers(client):
    headers = client.get("/").headers

    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


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
