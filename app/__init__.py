"""Application factory and shared service wiring.

Long-lived collaborators — the Redis store and the Wikipedia client — are built
once per process and hung off ``app.extensions``, which is where Flask expects
extension state to live. Reach them through :func:`services`; because Celery
tasks run inside an app context, that works identically in web and worker
processes and replaces the old hand-rolled singleton factory.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from celery import Celery
from flasgger import Swagger
from flask import Flask, Response, current_app, g, jsonify, request
from marshmallow import ValidationError
from werkzeug.exceptions import HTTPException

from app.config import Settings
from app.errors import ErrorCode, IrisError
from app.log import configure_logging
from app.store import RedisStore
from app.wikipedia import WikipediaClient, WikipediaSource

logger = logging.getLogger(__name__)

celery = Celery(__name__)

SWAGGER_TEMPLATE = {
    "swagger": "2.0",
    "info": {
        "title": "Iris Wikipedia Pathfinder API",
        "description": "Find the shortest path between two Wikipedia pages.",
        "version": "2.0.0",
        "license": {"name": "MIT"},
    },
    "basePath": "/",
    "consumes": ["application/json"],
    "produces": ["application/json"],
    "tags": [
        {"name": "Pathfinding", "description": "Find paths between Wikipedia pages"},
        {"name": "Tasks", "description": "Inspect and cancel background searches"},
        {"name": "System", "description": "Health and administration"},
    ],
}

SWAGGER_CONFIG = {
    "headers": [],
    "specs": [{"endpoint": "apispec", "route": "/apispec.json"}],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/api/docs",
}


@dataclass(frozen=True, slots=True)
class Services:
    """Process-wide collaborators, created once in :func:`create_app`."""

    settings: Settings
    store: RedisStore
    wikipedia: WikipediaSource


def services() -> Services:
    """The current app's services. Requires an application context."""
    return current_app.extensions["iris"]


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()

    app = Flask(__name__, static_folder="../static", static_url_path="/static")
    app.config.update(
        SECRET_KEY=settings.secret_key,
        DEBUG=settings.debug,
        TESTING=settings.testing,
        MAX_CONTENT_LENGTH=1024 * 1024,
    )

    configure_logging(app, settings)

    store = RedisStore.connect(settings.redis_url, settings.links_cache_ttl)
    app.extensions["iris"] = Services(
        settings=settings,
        store=store,
        wikipedia=WikipediaClient(settings, cache=store),
    )

    _configure_celery(app, settings)
    Swagger(app, template=SWAGGER_TEMPLATE, config=SWAGGER_CONFIG)
    _register_hooks(app)
    _register_error_handlers(app)

    from app.routes import api

    app.register_blueprint(api)

    logger.info("app_created", extra={"env": settings.env})
    return app


# --- Request lifecycle ----------------------------------------------------


def _register_hooks(app: Flask) -> None:
    @app.before_request
    def start_request() -> None:
        g.request_id = str(uuid.uuid4())

    @app.after_request
    def finish_request(response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        if request.path.startswith(("/static/", "/flasgger_static/")):
            return response
        logger.info("request_completed", extra={"http_status": response.status_code})
        return response


def _register_error_handlers(app: Flask) -> None:
    """One handler per error *shape*, not one per exception class."""

    def problem(
        message: str, code: str, status: int, **extra: object
    ) -> tuple[Response, int]:
        return jsonify(
            {"error": True, "message": message, "code": code, **extra}
        ), status

    @app.errorhandler(ValidationError)
    def on_validation_error(exc: ValidationError):
        logger.warning("validation_error", extra={"details": exc.messages})
        return problem(
            "Invalid request data",
            ErrorCode.INVALID_REQUEST,
            400,
            details=exc.messages,
        )

    @app.errorhandler(IrisError)
    def on_iris_error(exc: IrisError):
        logger.warning(
            "application_error", extra={"code": exc.code, "error": exc.message}
        )
        return problem(exc.message, exc.code, exc.status)

    @app.errorhandler(HTTPException)
    def on_http_error(exc: HTTPException):
        return problem(
            exc.description or exc.name,
            exc.name.upper().replace(" ", "_"),
            exc.code or 500,
        )

    @app.errorhandler(Exception)
    def on_unexpected_error(exc: Exception):
        logger.error("unexpected_error", extra={"error": str(exc)}, exc_info=True)
        return problem("Internal server error", ErrorCode.INTERNAL_ERROR, 500)


# --- Celery ---------------------------------------------------------------


def _configure_celery(app: Flask, settings: Settings) -> None:
    celery.conf.update(
        broker_url=settings.redis_url,
        result_backend=settings.redis_url,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_soft_time_limit=settings.task_soft_time_limit,
        task_time_limit=settings.task_time_limit,
        task_reject_on_worker_lost=True,
        result_expires=3600,
        timezone="UTC",
        # Keep our root-logger JSON handlers instead of Celery's plain text.
        worker_hijack_root_logger=False,
    )

    class AppContextTask(celery.Task):
        """Runs every task inside the Flask app context."""

        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = AppContextTask
