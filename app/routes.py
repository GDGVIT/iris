"""HTTP API.

Errors are raised, not returned: the app-level handlers in ``app/__init__.py``
render every one of them into the same JSON shape, so handlers here only
describe the success path.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from celery import states
from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    request,
    url_for,
)
from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from app import celery, services
from app.errors import (
    ConflictError,
    ErrorCode,
    InvalidRequestError,
    NotFoundError,
    RateLimitedError,
)
from app.pathfinding import ALGORITHMS, BIDIRECTIONAL
from app.share import describe, render_card, with_share_meta
from app.tasks import PROGRESS, find_path_task, route_key

logger = logging.getLogger(__name__)

api = Blueprint("api", __name__)

MAX_TITLE = 255
TERMINAL = (states.SUCCESS, states.FAILURE, states.REVOKED)
KEEPALIVE_SECONDS = 15
"""An idle stream sends a comment this often, so proxies do not close it."""


class SearchSchema(Schema):
    """Validates POST /getPath."""

    start = fields.Str(required=True, validate=validate.Length(min=1, max=255))
    end = fields.Str(required=True, validate=validate.Length(min=1, max=255))
    algorithm = fields.Str(
        load_default=BIDIRECTIONAL, validate=validate.OneOf(ALGORITHMS)
    )

    @validates_schema
    def check_pages_differ(self, data: dict, **_: object) -> None:
        if data.get("start", "").strip() == data.get("end", "").strip():
            raise ValidationError("Start and end pages must be different", "end")


@api.post("/getPath")
def start_search() -> tuple[Response, int]:
    """Queue a background search between two Wikipedia pages.
    ---
    tags: [Pathfinding]
    summary: Start pathfinding
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [start, end]
          properties:
            start:
              type: string
              example: "Python (programming language)"
              description: Wikipedia page title to start from
            end:
              type: string
              example: "Monty Python"
              description: Wikipedia page title to reach
            algorithm:
              type: string
              enum: [bidirectional, bfs]
              default: bidirectional
              description: >
                "bidirectional" searches from both ends at once using backlinks
                for the reverse frontier, and is usually far faster. "bfs"
                walks forward only.
    responses:
      202:
        description: Search accepted; poll poll_url for progress and the result
      400:
        description: Validation error
      429:
        description: Too many searches from this address; see Retry-After
    """
    _rate_limit("search", services().settings.search_rate_limit)
    data = SearchSchema().load(request.get_json(silent=True) or {})
    start, end = data["start"].strip(), data["end"].strip()

    task = find_path_task.delay(start, end, data["algorithm"])
    logger.info(
        "search_queued",
        extra={"task_id": task.id, "start_page": start, "end_page": end},
    )

    return jsonify(
        {
            "status": "IN_PROGRESS",
            "task_id": task.id,
            "poll_url": url_for("api.search_status", task_id=task.id),
            "start_page": start,
            "end_page": end,
        }
    ), 202


@api.get("/tasks/status/<task_id>")
def search_status(task_id: str) -> Response:
    """Poll a search for progress or its result.
    ---
    tags: [Pathfinding]
    summary: Get task status
    parameters:
      - name: task_id
        in: path
        required: true
        type: string
    responses:
      200:
        description: Current status — PENDING, IN_PROGRESS, SUCCESS, FAILURE or REVOKED
      404:
        description: Malformed task ID
    """
    _require_task_id(task_id)
    return jsonify(_status(task_id))


@api.get("/tasks/stream/<task_id>")
def stream_status(task_id: str) -> Response:
    """Stream a search's progress as Server-Sent Events.
    ---
    tags: [Pathfinding]
    summary: Stream task status
    description: >
      Each event's data is the same JSON body GET /tasks/status/{task_id}
      returns, sent whenever it changes. The stream ends after a terminal
      status (SUCCESS, FAILURE or REVOKED), or after a couple of minutes, when
      an EventSource reconnects by itself.
    produces: [text/event-stream]
    parameters:
      - name: task_id
        in: path
        required: true
        type: string
    responses:
      200:
        description: An event stream of status bodies
      404:
        description: Malformed task ID
      429:
        description: Too many requests from this address
    """
    _require_task_id(task_id)
    _rate_limit("api", services().settings.api_rate_limit)
    settings = services().settings

    def events() -> Iterator[str]:
        yield f"retry: {int(settings.stream_interval * 2000)}\n\n"
        last: dict[str, Any] | None = None
        quiet = 0.0
        deadline = time.monotonic() + settings.stream_max_seconds
        while True:
            body = _status(task_id)
            if body != last:
                yield f"data: {json.dumps(body)}\n\n"
                last, quiet = body, 0.0
            elif quiet >= KEEPALIVE_SECONDS:
                yield ": keep-alive\n\n"
                quiet = 0.0
            if body["status"] in TERMINAL or time.monotonic() >= deadline:
                return
            time.sleep(settings.stream_interval)
            quiet += settings.stream_interval

    return Response(
        events(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _status(task_id: str) -> dict[str, Any]:
    task = find_path_task.AsyncResult(task_id)
    body: dict[str, Any] = {"task_id": task_id}

    if task.state == states.PENDING:
        body |= {"status": states.PENDING, "message": "Task is waiting to be processed"}
    elif task.state == PROGRESS:
        body |= {"status": "IN_PROGRESS", "progress": task.info}
    elif task.state == states.SUCCESS:
        body |= {"status": states.SUCCESS, **_result(task.result)}
    elif task.state == states.FAILURE:
        body |= {"status": states.FAILURE, "error": str(task.info)}
    else:
        body |= {"status": task.state}
    return body


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a finished task.

    The task reports its own failures as a successful return value, so a
    Celery SUCCESS can still be a search that found nothing.
    """
    if payload.get("status") == states.FAILURE:
        return {
            "status": states.FAILURE,
            "error": payload.get("error"),
            "code": payload.get("code"),
        }
    return {
        "result": {
            "path": payload.get("path", []),
            "length": payload.get("length", 0),
            "search_time": payload.get("search_time"),
            "nodes_explored": payload.get("nodes_explored"),
            "search_stats": payload.get("search_stats"),
        }
    }


@api.delete("/tasks/<task_id>")
def cancel_task(task_id: str) -> Response:
    """Revoke a task, terminating it if it is already running.
    ---
    tags: [Tasks]
    summary: Cancel task
    parameters:
      - name: task_id
        in: path
        required: true
        type: string
      - name: terminate
        in: query
        type: boolean
        default: true
    responses:
      200:
        description: Task revoked
      409:
        description: Task already finished
    """
    _require_task_id(task_id)
    terminate = _wants_terminate()

    if find_path_task.AsyncResult(task_id).state in (states.SUCCESS, states.FAILURE):
        raise ConflictError("Task has already finished")

    celery.control.revoke(task_id, terminate=terminate, signal="SIGTERM")
    logger.info("task_revoked", extra={"task_id": task_id, "terminate": terminate})
    return jsonify({"revoked": True, "task_id": task_id, "terminated": terminate})


@api.get("/health")
def health() -> tuple[Response, int]:
    """Report Redis and cache connectivity.
    ---
    tags: [System]
    summary: Health check
    responses:
      200:
        description: Healthy
      503:
        description: Degraded
    """
    store = services().store
    checks = {"redis": "healthy" if store.ping() else "unhealthy: ping failed"}

    try:
        store.set("health_check:web", "ok", ttl=60)
        checks["cache"] = (
            "healthy" if store.get("health_check:web") == "ok" else "unhealthy"
        )
    except Exception as exc:
        checks["cache"] = f"unhealthy: {exc}"

    healthy = all(value == "healthy" for value in checks.values())
    return jsonify({"status": "healthy" if healthy else "degraded", **checks}), (
        200 if healthy else 503
    )


# --- Search helpers -------------------------------------------------------


@api.get("/api/suggest")
def suggest() -> Response:
    """Suggest article titles for what has been typed so far.
    ---
    tags: [Pathfinding]
    summary: Title suggestions
    parameters:
      - name: q
        in: query
        required: true
        type: string
        description: The start of a title
    responses:
      200:
        description: >
          Up to eight articles, best match first. Redirects are resolved;
          disambiguation pages are flagged, since they cannot be an end page.
      429:
        description: Too many requests from this address
    """
    _rate_limit("api", services().settings.api_rate_limit)
    query = request.args.get("q", "").strip()[:MAX_TITLE]
    titles = services().wikipedia.suggest(query) if query else []
    return jsonify({"query": query, "titles": [asdict(item) for item in titles]})


@api.get("/api/context")
def link_context() -> Response:
    """The sentence in one article that links to the next.
    ---
    tags: [Pathfinding]
    summary: Link context for one step of a path
    parameters:
      - name: from
        in: query
        required: true
        type: string
        description: The article that contains the link
      - name: to
        in: query
        required: true
        type: string
        description: The article it links to
    responses:
      200:
        description: >
          "where" is "prose" when the link sits in running text, and then
          before + link + after is the sentence with link as the linked words.
          "template" means the link is in an infobox, table or template;
          "elsewhere" means it is not in the article's own text at all
          (usually a navigation box).
      400:
        description: Missing from or to
      429:
        description: Too many requests from this address
    """
    _rate_limit("api", services().settings.api_rate_limit)
    source, target = _route_args()
    context = services().wikipedia.link_context(source, target)
    return jsonify({"from": source, "to": target, **asdict(context)})


# --- Sharing --------------------------------------------------------------


@api.get("/api/og.png")
def share_image() -> Response:
    """The preview image a shared route link unfurls into.
    ---
    tags: [Pathfinding]
    summary: Share image for a route
    produces: [image/png]
    parameters:
      - name: from
        in: query
        required: true
        type: string
      - name: to
        in: query
        required: true
        type: string
    responses:
      200:
        description: >
          A 1200x630 PNG of the route if it has been found recently, or of the
          question if not.
      400:
        description: Missing from or to
    """
    _rate_limit("api", services().settings.api_rate_limit)
    start, end = _route_args()
    path = services().store.get(route_key(start, end))
    return Response(
        render_card(start, end, path),
        mimetype="image/png",
        # Short when the route is unknown: it may be found a minute from now.
        headers={"Cache-Control": f"public, max-age={86_400 if path else 300}"},
    )


# --- UI -------------------------------------------------------------------

API_PREFIXES = ("getPath", "tasks", "health", "api", "apispec")


@api.get("/")
def index() -> Response:
    """Serve the UI; a shared route link gets that route's preview tags."""
    start = request.args.get("from", "").strip()[:MAX_TITLE]
    end = request.args.get("to", "").strip()[:MAX_TITLE]
    if not (start and end):
        return current_app.send_static_file("index.html")

    path = services().store.get(route_key(start, end))
    route: dict[str, Any] = {"from": start, "to": end}
    page = with_share_meta(
        Path(current_app.static_folder or "", "index.html").read_text("utf-8"),
        describe(start, end, path),
        url=request.url,
        image=url_for("api.share_image", _external=True, **route),
    )
    return Response(page, mimetype="text/html")


@api.get("/favicon.ico")
@api.get("/robots.txt")
def root_file() -> Response:
    """Files browsers and crawlers ask for at the root, not under /static."""
    return current_app.send_static_file(request.path.lstrip("/"))


@api.get("/<path:path>")
def catch_all(path: str) -> tuple[Response, int]:
    """Answer unknown API paths with JSON and everything else with a page."""
    if path.startswith(API_PREFIXES):
        raise NotFoundError("Endpoint not found")
    return current_app.send_static_file("404.html"), 404


# --- Helpers --------------------------------------------------------------


def _require_task_id(task_id: str) -> None:
    try:
        uuid.UUID(task_id)
    except ValueError as exc:
        raise NotFoundError(
            "Invalid task ID format", ErrorCode.INVALID_TASK_ID
        ) from exc


def _route_args() -> tuple[str, str]:
    source = request.args.get("from", "").strip()[:MAX_TITLE]
    target = request.args.get("to", "").strip()[:MAX_TITLE]
    if not (source and target):
        raise InvalidRequestError("Both 'from' and 'to' are required")
    return source, target


def _rate_limit(bucket: str, limit: int) -> None:
    """Allow ``limit`` requests per client per window, counted in Redis.

    Fixed windows, keyed by the window's number, so every key expires on its
    own and nothing needs sweeping.
    """
    window = services().settings.rate_window
    now = int(time.time())
    key = f"ratelimit:{bucket}:{request.remote_addr}:{now // window}"
    if services().store.increment(key, ttl=window) > limit:
        raise RateLimitedError(retry_after=window - now % window)


def _wants_terminate() -> bool:
    return request.args.get("terminate", "true").lower() != "false"
