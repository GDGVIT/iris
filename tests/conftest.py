"""Shared fixtures.

Two deliberate choices shape this suite:

* Redis is `fakeredis`, not a mock. ``RedisStore`` and the search algorithm run
  against real Redis semantics — expiry, set membership, list ordering — so a
  test failing means the code is wrong, not that a mock drifted.
* Wikipedia is a stub serving a fixed link graph. It is the only thing faked,
  because it is the only thing we cannot run locally.
"""

from __future__ import annotations

import fakeredis
import pytest

from app import Services, celery, create_app
from app.config import Settings
from app.store import RedisStore
from app.wikipedia import PageFetched, PageStatus, Suggestion
from app.wikitext import LinkContext

# A small, deliberately asymmetric graph.
#
#   Python ─→ Programming ─→ Computer science ─→ Mathematics
#      │                            ↑
#      └────→ Monty Python ─────────┘
#
# "Orphan" exists but nothing links to it and it links nowhere.
GRAPH: dict[str, list[str]] = {
    "Python": ["Programming", "Monty Python"],
    "Programming": ["Computer science"],
    "Monty Python": ["Computer science", "Comedy"],
    "Computer science": ["Mathematics"],
    "Comedy": ["Monty Python"],
    "Mathematics": [],
    "Orphan": [],
}

DISAMBIGUATION_PAGES = {"Mercury"}

REDIRECTS = {"python": "Python", "Maths": "Mathematics"}
"""Titles that exist only as an alias, the way a lower-cased input does."""


class FakeWikipedia:
    """Serves :data:`GRAPH` with the real client's interface."""

    def __init__(self, graph: dict[str, list[str]] | None = None) -> None:
        self.graph = GRAPH if graph is None else graph
        self.link_calls: list[list[str]] = []
        self.backlink_calls: list[list[str]] = []

    def links(
        self, titles: list[str], on_page: PageFetched | None = None
    ) -> dict[str, list[str]]:
        self.link_calls.append(list(titles))
        return self._respond(
            {title: self.graph.get(title, []) for title in titles}, on_page
        )

    def backlinks(
        self, titles: list[str], on_page: PageFetched | None = None
    ) -> dict[str, list[str]]:
        self.backlink_calls.append(list(titles))
        found = {
            title: sorted(
                source for source, targets in self.graph.items() if title in targets
            )
            for title in titles
        }
        return self._respond(found, on_page)

    def page_status(self, title: str) -> PageStatus:
        resolved = REDIRECTS.get(title, title)
        return PageStatus(
            exists=resolved in self.graph or resolved in DISAMBIGUATION_PAGES,
            resolved_title=resolved,
            is_disambiguation=resolved in DISAMBIGUATION_PAGES,
        )

    def suggest(self, prefix: str) -> list[Suggestion]:
        titles = sorted([*self.graph, *DISAMBIGUATION_PAGES])
        return [
            Suggestion(
                title=title,
                description="",
                is_disambiguation=title in DISAMBIGUATION_PAGES,
            )
            for title in titles
            if title.casefold().startswith(prefix.casefold())
        ]

    def link_context(self, source: str, target: str) -> LinkContext:
        if target not in self.graph.get(source, []):
            return LinkContext(where="elsewhere")
        return LinkContext(
            where="prose", before=f"{source} relates to ", link=target, after="."
        )

    @staticmethod
    def _respond(
        found: dict[str, list[str]], on_page: PageFetched | None
    ) -> dict[str, list[str]]:
        if on_page:
            for title, titles in found.items():
                on_page(title, titles)
        return found


@pytest.fixture
def settings() -> Settings:
    return Settings(
        env="testing", secret_key="test-secret-key", max_search_depth=6, batch_size=10
    )


@pytest.fixture
def store() -> RedisStore:
    return RedisStore(fakeredis.FakeStrictRedis(decode_responses=True), default_ttl=60)


@pytest.fixture
def wikipedia() -> FakeWikipedia:
    return FakeWikipedia()


@pytest.fixture
def app(settings: Settings, store: RedisStore, wikipedia: FakeWikipedia):
    """A real Flask app whose Redis and Wikipedia are the fixtures above."""
    flask_app = create_app(settings)
    flask_app.extensions["iris"] = Services(
        settings=settings, store=store, wikipedia=wikipedia
    )

    # After create_app, which configures Celery for real Redis. Tasks run
    # inline and their results are kept in memory, so a poll straight after a
    # POST sees the finished search.
    celery.conf.update(
        task_always_eager=True,
        task_eager_propagates=False,
        task_store_eager_result=True,
        result_backend="cache+memory://",
    )

    with flask_app.app_context():
        yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()
