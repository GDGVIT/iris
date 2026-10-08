"""The Wikipedia client, driven by a stub HTTP session.

Only ``requests`` is faked. Parsing, pagination, caching, retry and the rate
limiter all run for real.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import requests

from app.config import Settings
from app.errors import WikipediaAPIError
from app.wikipedia import WikipediaClient


class FakeResponse:
    def __init__(
        self,
        payload: dict | None = None,
        status: int = 200,
        headers: dict | None = None,
    ):
        self._payload = payload or {}
        self.status_code = status
        self.headers = headers or {}

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """Replays a queued list of responses and records the params it was sent."""

    def __init__(self, *responses: FakeResponse | Exception):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, params: dict, timeout: int) -> FakeResponse:
        self.requests.append(params)
        result = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(result, Exception):
            raise result
        return result


def links_payload(title: str, titles: list[str], **extra) -> dict:
    return {
        "query": {
            "pages": {"1": {"title": title, "links": [{"title": t} for t in titles]}}
        },
        **extra,
    }


@pytest.fixture
def settings() -> Settings:
    return Settings(
        env="testing",
        secret_key="test",
        wikipedia_request_delay=0,
        wikipedia_max_retries=3,
        wikipedia_max_pages=3,
    )


def build(settings: Settings, session: FakeSession, cache=None) -> WikipediaClient:
    return WikipediaClient(settings, cache=cache, session=session)


# --- Parsing --------------------------------------------------------------


def test_asks_the_api_for_articles_only(settings):
    """Namespaces are filtered server-side, so they never use up the allowance."""
    session = FakeSession(FakeResponse(links_payload("Python", ["Programming"])))
    build(settings, session).links(["Python"])

    assert session.requests[0]["plnamespace"] == 0


def test_keeps_articles_whose_titles_contain_a_colon(settings):
    """A colon is not a namespace: these are ordinary articles."""
    titles = ["Star Wars: Episode IV – A New Hope", "List of: things"]
    session = FakeSession(FakeResponse(links_payload("Films", titles)))

    assert build(settings, session).links(["Films"]) == {"Films": titles}


def test_follows_redirects_back_to_the_requested_title(settings):
    """The API answers under the resolved title; callers asked for the original."""
    payload = links_payload("Python (programming language)", ["Programming"])
    payload["query"]["redirects"] = [
        {"from": "Python lang", "to": "Python (programming language)"}
    ]
    session = FakeSession(FakeResponse(payload))

    assert build(settings, session).links(["Python lang"]) == {
        "Python lang": ["Programming"]
    }


def test_follows_normalisation_before_redirects(settings):
    """A casually-typed title is normalised first, then redirected.

    Reading the two stages in the wrong order strands the title at the input,
    the page match fails and the caller silently gets no links at all — which
    leaves a search with a dead frontier rather than an error.
    """
    payload = links_payload("Shah Rukh Khan", ["Bollywood"])
    payload["query"]["normalized"] = [
        {"from": "shah rukh khan", "to": "Shah rukh khan"}
    ]
    payload["query"]["redirects"] = [{"from": "Shah rukh khan", "to": "Shah Rukh Khan"}]
    session = FakeSession(FakeResponse(payload))

    assert build(settings, session).links(["shah rukh khan"]) == {
        "shah rukh khan": ["Bollywood"]
    }


def test_missing_pages_yield_no_links(settings):
    session = FakeSession(
        FakeResponse({"query": {"pages": {"-1": {"title": "X", "missing": ""}}}})
    )
    assert build(settings, session).links(["X"]) == {"X": []}


def linkshere_payload(title: str, titles: list[str]) -> dict:
    return {
        "query": {
            "pages": {
                "1": {"title": title, "linkshere": [{"title": t} for t in titles]}
            }
        }
    }


def test_reads_backlinks_from_linkshere(settings):
    session = FakeSession(FakeResponse(linkshere_payload("X", ["A", "B"])))

    assert build(settings, session).backlinks(["X"]) == {"X": ["A", "B"]}
    assert session.requests[0]["prop"] == "linkshere"
    assert session.requests[0]["lhnamespace"] == 0


# --- Pagination -----------------------------------------------------------


def test_follows_continuations(settings):
    session = FakeSession(
        FakeResponse(
            links_payload(
                "P", ["A"], **{"continue": {"plcontinue": "c1", "continue": "||"}}
            )
        ),
        FakeResponse(links_payload("P", ["B"])),
    )

    assert build(settings, session).links(["P"]) == {"P": ["A", "B"]}
    assert session.requests[1]["plcontinue"] == "c1"


def test_stops_paginating_at_the_configured_limit(settings):
    endless = FakeResponse(
        links_payload("P", ["A"], **{"continue": {"plcontinue": "c", "continue": "||"}})
    )
    session = FakeSession(*[endless] * 10)

    build(replace(settings, wikipedia_max_pages=2), session).links(["P"])

    assert len(session.requests) == 2


# --- Caching --------------------------------------------------------------


def test_caches_results_and_serves_repeats_without_hitting_the_api(settings, store):
    session = FakeSession(FakeResponse(links_payload("Python", ["Programming"])))
    client = build(settings, session, cache=store)

    assert client.links(["Python"]) == {"Python": ["Programming"]}
    assert client.links(["Python"]) == {"Python": ["Programming"]}
    assert len(session.requests) == 1


def test_only_fetches_the_pages_that_are_not_cached(settings, store):
    store.set("wiki_links:Cached", ["Known"])
    session = FakeSession(FakeResponse(links_payload("Fresh", ["New"])))

    result = build(settings, session, cache=store).links(["Cached", "Fresh"])

    assert result == {"Cached": ["Known"], "Fresh": ["New"]}
    assert len(session.requests) == 1


def test_reports_cached_pages_to_the_progress_callback_too(settings, store):
    store.set("wiki_links:Cached", ["Known"])
    seen: list[str] = []

    build(settings, FakeSession(), cache=store).links(
        ["Cached"], lambda title, _links: seen.append(title)
    )

    assert seen == ["Cached"]


def test_forward_and_backward_caches_do_not_collide(settings, store):
    store.set("wiki_links:X", ["forward-only"])
    session = FakeSession(FakeResponse(linkshere_payload("X", ["B"])))

    assert build(settings, session, cache=store).backlinks(["X"]) == {"X": ["B"]}


# --- Failures -------------------------------------------------------------


def test_retries_server_errors_then_succeeds(settings):
    session = FakeSession(
        FakeResponse(status=503),
        FakeResponse(links_payload("P", ["A"])),
    )
    assert build(settings, session).links(["P"]) == {"P": ["A"]}


def test_respects_retry_after_on_rate_limiting(settings, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("app.wikipedia.time.sleep", slept.append)

    session = FakeSession(
        FakeResponse(status=429, headers={"Retry-After": "7"}),
        FakeResponse(links_payload("P", ["A"])),
    )
    build(settings, session).links(["P"])

    assert slept == [pytest.approx(7, abs=0.5)]


def test_a_rate_limit_pauses_every_thread_not_just_the_one_throttled(
    settings, monkeypatch
):
    """Six threads each walking into the same 429 wastes requests and time."""
    slept: list[float] = []
    monkeypatch.setattr("app.wikipedia.time.sleep", slept.append)
    client = build(settings, FakeSession())

    client._pause(5)
    client._await_rate_slot()  # any thread's next request

    assert slept == [pytest.approx(5, abs=0.5)]


def test_retries_network_failures(settings, monkeypatch):
    monkeypatch.setattr("app.wikipedia.time.sleep", lambda _s: None)
    session = FakeSession(
        requests.ConnectionError("boom"),
        FakeResponse(links_payload("P", ["A"])),
    )
    assert build(settings, session).links(["P"]) == {"P": ["A"]}


def test_gives_up_after_the_retry_budget(settings, monkeypatch):
    monkeypatch.setattr("app.wikipedia.time.sleep", lambda _s: None)
    session = FakeSession(*[FakeResponse(status=500)] * 3)

    with pytest.raises(WikipediaAPIError, match="after 3 attempts"):
        build(settings, session).links(["P"])

    assert len(session.requests) == 3


def test_does_not_retry_client_errors(settings):
    session = FakeSession(FakeResponse(status=400))

    with pytest.raises(WikipediaAPIError, match="rejected"):
        build(settings, session).links(["P"])

    assert len(session.requests) == 1, "a 400 will never become a 200"


# --- Page status ----------------------------------------------------------


def test_reports_an_existing_page(settings):
    session = FakeSession(
        FakeResponse({"query": {"pages": {"1": {"title": "Python"}}}})
    )
    status = build(settings, session).page_status("Python")

    assert status.exists and not status.is_disambiguation


def test_reports_a_missing_page(settings):
    session = FakeSession(FakeResponse({"query": {"pages": {"-1": {"missing": ""}}}}))
    assert build(settings, session).page_status("Nope").exists is False


def test_detects_disambiguation_by_category(settings):
    session = FakeSession(
        FakeResponse(
            {
                "query": {
                    "pages": {
                        "1": {
                            "title": "Mercury",
                            "categories": [{"title": "Category:Disambiguation pages"}],
                        }
                    }
                }
            }
        )
    )
    assert build(settings, session).page_status("Mercury").is_disambiguation is True


def test_detects_disambiguation_by_title(settings):
    session = FakeSession(
        FakeResponse({"query": {"pages": {"1": {"title": "Mercury (disambiguation)"}}}})
    )
    assert build(settings, session).page_status("Mercury").is_disambiguation is True


def test_reports_where_a_redirect_lands(settings):
    session = FakeSession(
        FakeResponse(
            {
                "query": {
                    "redirects": [{"from": "NYC", "to": "New York City"}],
                    "pages": {"1": {"title": "New York City"}},
                }
            }
        )
    )
    assert build(settings, session).page_status("NYC").resolved_title == "New York City"


def test_reports_where_a_normalised_redirect_lands(settings):
    session = FakeSession(
        FakeResponse(
            {
                "query": {
                    "normalized": [{"from": "nyc", "to": "Nyc"}],
                    "redirects": [{"from": "Nyc", "to": "New York City"}],
                    "pages": {"1": {"title": "New York City"}},
                }
            }
        )
    )
    assert build(settings, session).page_status("nyc").resolved_title == "New York City"


def test_recovers_a_title_that_differs_only_by_case(settings):
    """Wikipedia capitalises the first letter and nothing else.

    "sHaH rUkH kHaN" is therefore a page that genuinely does not exist, and
    only the search index knows what was meant.
    """
    session = FakeSession(
        FakeResponse({"query": {"pages": {"-1": {"missing": ""}}}}),
        FakeResponse({"query": {"search": [{"title": "Shah Rukh Khan"}]}}),
        FakeResponse({"query": {"pages": {"1": {"title": "Shah Rukh Khan"}}}}),
    )
    status = build(settings, session).page_status("sHaH rUkH kHaN")

    assert status.exists
    assert status.resolved_title == "Shah Rukh Khan"


def test_a_search_hit_that_is_not_a_case_variant_is_refused(settings):
    """The fallback fixes capitalisation, deliberately not spelling."""
    session = FakeSession(
        FakeResponse({"query": {"pages": {"-1": {"missing": ""}}}}),
        FakeResponse({"query": {"search": [{"title": "Something Else Entirely"}]}}),
    )
    status = build(settings, session).page_status("asdkjhaskdjh")

    assert not status.exists


# --- Rate limiting --------------------------------------------------------


def test_spaces_requests_by_the_configured_delay(monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr("app.wikipedia.time.sleep", waits.append)

    settings = Settings(env="testing", secret_key="t", wikipedia_request_delay=0.5)
    client = build(settings, FakeSession(FakeResponse(), FakeResponse()))

    client._await_rate_slot()
    client._await_rate_slot()

    assert waits and waits[0] > 0, "the second call must wait out the interval"
