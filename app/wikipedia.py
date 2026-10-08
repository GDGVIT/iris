"""Wikipedia API client: link/backlink lookups and page metadata.

Forward links (``prop=links``) and backlinks (``prop=linkshere``) are the same
query shape with a different property, so both are expressed as a
``LinkQuery`` and share one paginating fetcher, one thread pool and one cache
path.

Each page is its own request, run in parallel across the worker pool. Asking
for many titles per request was measured and rejected: the API caps a response
at 500 links however many titles it covers, and the pages a search touches
usually have more than that, so batching saved few requests while serialising
the continuations it did need (scripts/benchmark.py).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import requests

from app.config import Settings
from app.errors import WikipediaAPIError
from app.store import RedisStore

logger = logging.getLogger(__name__)

API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "Iris-Wikipedia-Pathfinder/1.0 (https://github.com/mdhishaamakhtar/iris)"

ARTICLE_NAMESPACE = 0
"""Articles only. Filtering server-side keeps Category:, File: and Template:
links out of the 500-link allowance, and keeps titles that merely contain a
colon ("Star Wars: Episode IV – A New Hope") in."""

PageFetched = Callable[[str, list[str]], None]
"""Called with (title, titles-found) as each page arrives. Runs on worker threads."""


@dataclass(frozen=True, slots=True)
class PageStatus:
    exists: bool
    resolved_title: str
    is_disambiguation: bool


@dataclass(frozen=True, slots=True)
class LinkQuery:
    """One of the two directions the graph can be walked in."""

    cache_prefix: str
    prop: str
    params: dict[str, str | int]
    continue_key: str


def resolve_title(query: dict[str, Any], title: str) -> str:
    """Follow the API's own title rewrites to the page it actually answered for.

    MediaWiki rewrites in a fixed order: it *normalises* first ("shah rukh khan"
    → "Shah rukh khan"), then follows *redirects* ("Shah rukh khan" → "Shah Rukh
    Khan"), reporting each stage separately. Applying them in that order is what
    makes a casually-typed title land on the article; doing redirects first
    misses the hand-off and leaves the title stranded at the input.
    """
    for stage in ("normalized", "redirects"):
        moves = {entry["from"]: entry["to"] for entry in query.get(stage, [])}
        seen = {title}
        # A stage can chain; ``seen`` stops a malformed cycle from spinning.
        while (nxt := moves.get(title)) and nxt not in seen:
            title = nxt
            seen.add(title)
    return title


def _extract(query: dict[str, Any], prop: str, title: str) -> list[str]:
    """Pull one page's links out of a ``prop=links`` or ``prop=linkshere`` response.

    The API answers under the resolved title, so redirects and normalisations
    are followed back to the title that was asked for.
    """
    resolved = resolve_title(query, title)
    for page in query.get("pages", {}).values():
        if page.get("title") == resolved and "missing" not in page:
            return [link["title"] for link in page.get(prop, [])]
    return []


FORWARD = LinkQuery(
    cache_prefix="wiki_links",
    prop="links",
    params={"prop": "links", "pllimit": "max", "plnamespace": ARTICLE_NAMESPACE},
    continue_key="plcontinue",
)

BACKWARD = LinkQuery(
    cache_prefix="wiki_backlinks",
    prop="linkshere",
    params={
        "prop": "linkshere",
        "lhlimit": "max",
        "lhnamespace": ARTICLE_NAMESPACE,
        "lhprop": "title",
    },
    continue_key="lhcontinue",
)


class HttpSession(Protocol):
    """The slice of ``requests.Session`` this client uses."""

    headers: Any

    def get(self, url: str, params: dict[str, Any], timeout: int) -> Any: ...


class WikipediaSource(Protocol):
    """What the rest of the application needs from Wikipedia.

    Declaring it structurally lets the search and the tasks depend on the
    behaviour rather than on this module's concrete client.
    """

    def links(
        self, titles: list[str], on_page: PageFetched | None = None
    ) -> dict[str, list[str]]: ...

    def backlinks(
        self, titles: list[str], on_page: PageFetched | None = None
    ) -> dict[str, list[str]]: ...

    def page_status(self, title: str) -> PageStatus: ...


class WikipediaClient:
    """Fetches page links, with a shared rate limit and a Redis-backed cache."""

    def __init__(
        self,
        settings: Settings,
        cache: RedisStore | None = None,
        session: HttpSession | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.session: Any = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._rate_lock = threading.Lock()
        self._last_request = 0.0
        self._paused_until = 0.0

    # --- Public API -------------------------------------------------------

    def links(
        self, titles: list[str], on_page: PageFetched | None = None
    ) -> dict[str, list[str]]:
        """Outgoing article links for each title."""
        return self._fetch_many(FORWARD, titles, on_page)

    def backlinks(
        self, titles: list[str], on_page: PageFetched | None = None
    ) -> dict[str, list[str]]:
        """Articles linking *to* each title."""
        return self._fetch_many(BACKWARD, titles, on_page)

    def page_status(self, title: str) -> PageStatus:
        """Whether a page exists, what it resolves to, and if it disambiguates."""
        cache_key = f"page_info:{title}"
        if self.cache and (cached := self.cache.get(cache_key)):
            return PageStatus(**cached)

        status = self._page_status(title)
        if not status.exists and (match := self._match_ignoring_case(title)):
            status = self._page_status(match)

        if self.cache:
            self.cache.set(cache_key, asdict(status), ttl=self.settings.page_cache_ttl)
        return status

    def _page_status(self, title: str) -> PageStatus:
        query = self._request(
            {"titles": title, "prop": "info|categories", "cllimit": "max"}
        ).get("query", {})

        resolved = resolve_title(query, title)
        page = next(
            (p for p in query.get("pages", {}).values() if "missing" not in p), None
        )
        return PageStatus(
            exists=page is not None,
            resolved_title=resolved,
            is_disambiguation=page is not None and _is_disambiguation(page),
        )

    def _match_ignoring_case(self, title: str) -> str | None:
        """Find the article whose title differs from ``title`` only by case.

        Wikipedia capitalises the first letter of a title and nothing else, so
        "shah rukh khan" resolves through a redirect but "sHaH rUkH kHaN" is
        simply a page that does not exist. The search index knows the real
        title; accepting a hit only when it case-folds to what was typed keeps
        this a fix for capitalisation and deliberately not for spelling.
        """
        query = self._request(
            {"list": "search", "srsearch": title, "srlimit": 5, "srprop": ""}
        ).get("query", {})

        folded = title.casefold()
        return next(
            (
                hit["title"]
                for hit in query.get("search", [])
                if hit["title"].casefold() == folded
            ),
            None,
        )

    # --- Fetching ---------------------------------------------------------

    def _fetch_many(
        self, query: LinkQuery, titles: list[str], on_page: PageFetched | None
    ) -> dict[str, list[str]]:
        if not titles:
            return {}

        results: dict[str, list[str]] = {}
        misses: list[str] = []

        for title in titles:
            cached = (
                self.cache.get(f"{query.cache_prefix}:{title}") if self.cache else None
            )
            if cached is None:
                misses.append(title)
            else:
                results[title] = cached
                if on_page:
                    on_page(title, cached)

        logger.info(
            "wikipedia_cache_lookup",
            extra={
                "hits": len(results),
                "misses": len(misses),
                "direction": query.cache_prefix,
            },
        )

        if not misses:
            return results

        with ThreadPoolExecutor(max_workers=self.settings.wikipedia_workers) as pool:
            futures = {
                pool.submit(self._fetch_one, query, title): title for title in misses
            }
            for future in as_completed(futures):
                title = futures[future]
                try:
                    found = future.result()
                except WikipediaAPIError:
                    raise
                except Exception as exc:
                    logger.error(
                        "wikipedia_page_failed",
                        extra={"page": title, "error": str(exc)},
                    )
                    found = []

                results[title] = found
                if self.cache:
                    self.cache.set(
                        f"{query.cache_prefix}:{title}",
                        found,
                        ttl=self.settings.links_cache_ttl,
                    )
                if on_page:
                    on_page(title, found)

        return results

    def _fetch_one(self, query: LinkQuery, title: str) -> list[str]:
        """Fetch one page, following continuations up to the configured limit."""
        params: dict[str, str | int] = {"titles": title, **query.params}
        found: list[str] = []
        for _ in range(self.settings.wikipedia_max_pages):
            response = self._request(params)
            found.extend(_extract(response.get("query", {}), query.prop, title))
            if "continue" not in response:
                break
            params |= {
                query.continue_key: response["continue"][query.continue_key],
                "continue": response["continue"]["continue"],
            }
        return found

    def _request(self, params: dict[str, str | int]) -> dict[str, Any]:
        """GET the API, retrying on 429, 5xx and network failures.

        Client errors other than 429 are the caller's fault and raise straight
        away; retrying them would only burn the budget.
        """
        params = {"action": "query", "format": "json", "redirects": 1, **params}
        attempts = self.settings.wikipedia_max_retries
        last_error = "no attempts made"

        for attempt in range(attempts):
            self._await_rate_slot()
            backoff = 2**attempt

            try:
                response = self.session.get(
                    API_URL, params=params, timeout=self.settings.wikipedia_timeout
                )
            except requests.RequestException as exc:
                last_error = str(exc)
            else:
                if response.ok:
                    return response.json()
                if response.status_code == 429:
                    backoff = int(response.headers.get("Retry-After", backoff))
                    last_error = "rate limited"
                elif response.status_code >= 500:
                    last_error = f"server error {response.status_code}"
                else:
                    raise WikipediaAPIError(
                        f"Wikipedia API rejected the request ({response.status_code})"
                    )

            if attempt == attempts - 1:
                break
            logger.warning(
                "wikipedia_retry",
                extra={"attempt": attempt + 1, "wait": backoff, "error": last_error},
            )
            if last_error == "rate limited":
                # Throttling applies to the client, not this request: hold
                # every thread back, or the others walk into the same 429.
                self._pause(backoff)
            else:
                time.sleep(backoff)

        raise WikipediaAPIError(
            f"Wikipedia API failed after {attempts} attempts: {last_error}"
        )

    def _pause(self, seconds: float) -> None:
        """Stop every thread from requesting until ``seconds`` from now."""
        with self._rate_lock:
            self._paused_until = max(self._paused_until, time.monotonic() + seconds)

    def _await_rate_slot(self) -> None:
        """Hold every thread to one request per ``wikipedia_request_delay``,
        and to any pause a 429 imposed.

        The lock is held across the sleep so a second thread cannot slip in and
        start its own request before the interval has elapsed.
        """
        with self._rate_lock:
            now = time.monotonic()
            wait = max(
                self._paused_until - now,
                self.settings.wikipedia_request_delay - (now - self._last_request),
            )
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()


def _is_disambiguation(page: dict[str, Any]) -> bool:
    title = page.get("title", "")
    if "(disambiguation)" in title.lower():
        return True
    return any(
        "disambiguation" in category.get("title", "").lower()
        for category in page.get("categories", [])
    )
