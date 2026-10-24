"""Find the sentence in an article's wikitext that links to another article.

A path step says "A links to B"; this is what makes it say *why*. The link is
looked for in the article's prose, after templates, references, tables and
file captions are stripped away, and the sentence around it is returned as
plain text, split around the link's own words.

Where the link is not in prose, the answer says so rather than guessing:
``template`` when it sits inside an infobox, table or other template in the
wikitext itself, and ``elsewhere`` when it is not in the wikitext at all —
the usual case for navigation boxes, which are transcluded at render time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Where = Literal["prose", "template", "elsewhere"]

MAX_SENTENCE = 320
"""Characters kept, link included, when a sentence runs long."""

ANCHOR_OPEN, ANCHOR_CLOSE = "\x01", "\x02"
"""Marks the target link's words while the line is reduced to plain text."""

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_REF = re.compile(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", re.DOTALL | re.IGNORECASE)
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_TABLE = re.compile(r"\{\|[^{]*?\|\}", re.DOTALL)
_FILE = re.compile(r"\[\[(?:File|Image):[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]", re.I)
_LINK = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]*))?\]\]")
_LINK_WITH_TRAIL = re.compile(_LINK.pattern + r"([a-z]*)")
"""Letters straight after ``]]`` render as part of the link: [[plantain]]s."""
_EXTERNAL = re.compile(r"\[https?://\S+\s*([^\]]*)\]")
_TAG = re.compile(r"<[^>]+>")
_HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"”’)]*\s+(?=[\"“(\[A-Z0-9])")


@dataclass(frozen=True, slots=True)
class LinkContext:
    """The sentence as three runs, so no client has to count characters:
    ``before + link + after`` is the sentence and ``link`` is the linked words."""

    where: Where
    before: str = ""
    link: str = ""
    after: str = ""
    section: str | None = None
    """The heading the sentence sits under; ``None`` for the lead."""


def find_link_context(wikitext: str, target: str) -> LinkContext:
    if not wikitext or not target:
        return LinkContext(where="elsewhere")

    raw = _COMMENT.sub("", wikitext)
    if not _links_to(raw, target):
        return LinkContext(where="elsewhere")

    prose = _strip_structure(raw)
    section: str | None = None
    for line in prose.splitlines():
        if heading := _HEADING.match(line.strip()):
            section = _plain(heading.group(2)) or None
            continue
        if not _links_to(line, target):
            continue
        text = _plain(_mark_target(line.lstrip("*#:; "), target))
        if runs := _sentence_around_anchor(text):
            before, link, after = runs
            return LinkContext(
                where="prose", before=before, link=link, after=after, section=section
            )

    return LinkContext(where="template")


# --- Matching -------------------------------------------------------------


def _same_title(written: str, target: str) -> bool:
    """MediaWiki's title equality: first letter and spaces/underscores loose."""

    def norm(title: str) -> str:
        title = re.sub(r"[\s_]+", " ", title.split("#", 1)[0]).strip()
        return title[:1].upper() + title[1:]

    return bool(written.strip()) and norm(written) == norm(target)


def _links_to(text: str, target: str) -> bool:
    return any(_same_title(m.group(1), target) for m in _LINK.finditer(text))


def _mark_target(line: str, target: str) -> str:
    """Wrap the first link to ``target`` in anchor marks; leave the rest."""
    marked = False

    def replace(match: re.Match[str]) -> str:
        nonlocal marked
        if marked or not _same_title(match.group(1), target):
            return match.group(0)
        marked = True
        words = (match.group(2) or match.group(1).split("#", 1)[0]) + match.group(3)
        return f"{ANCHOR_OPEN}{words}{ANCHOR_CLOSE}"

    return _LINK_WITH_TRAIL.sub(replace, line)


# --- Reduction to plain text ---------------------------------------------


def _strip_structure(text: str) -> str:
    """Remove everything that is not running prose."""
    text = _REF.sub("", text)
    text = _remove_nested(_FILE, text)
    text = _remove_nested(_TEMPLATE, text)
    return _remove_nested(_TABLE, text)


def _remove_nested(pattern: re.Pattern[str], text: str) -> str:
    """Delete innermost matches until none remain, so nesting unwinds."""
    while True:
        reduced = pattern.sub("", text)
        if reduced == text:
            return text
        text = reduced


def _plain(text: str) -> str:
    text = _LINK.sub(lambda m: m.group(2) or m.group(1).split("#", 1)[0], text)
    text = _EXTERNAL.sub(lambda m: m.group(1), text)
    text = _TAG.sub("", text)
    text = text.replace("'''", "").replace("''", "")
    text = text.replace("&nbsp;", " ").replace("&ndash;", "–").replace("&mdash;", "—")
    text = re.sub(r"\s+", " ", text)
    # Removed references and templates leave stray gaps before punctuation.
    text = re.sub(r"\s+([,.;:!?)])", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    return text.strip()


def _sentence_around_anchor(text: str) -> tuple[str, str, str] | None:
    start = text.find(ANCHOR_OPEN)
    end = text.find(ANCHOR_CLOSE, start)
    if start < 0 or end < 0:
        return None

    boundaries = [0, *(m.end() for m in _SENTENCE_END.finditer(text)), len(text)]
    begin = max(b for b in boundaries if b <= start)
    finish = min(b for b in boundaries if b > end)

    before = text[begin:start].lstrip()
    link = text[start + 1 : end]
    after = text[end + 1 : finish].rstrip()
    if not link.strip():
        return None
    return _trim(before, link, after)


def _trim(before: str, link: str, after: str) -> tuple[str, str, str]:
    """Shorten a long sentence to a window around the link, at word breaks."""
    spare = MAX_SENTENCE - len(link)
    if len(before) + len(after) <= spare:
        return before, link, after
    if spare <= 0:
        return "…", link[:MAX_SENTENCE], "…"

    keep_before = min(len(before), max(spare // 2, spare - len(after)))
    keep_after = spare - keep_before
    if keep_before < len(before):
        cut = before[-keep_before:] if keep_before else ""
        cut = cut.split(" ", 1)[1] if " " in cut else cut
        before = "…" + cut
    if keep_after < len(after):
        cut = after[:keep_after]
        cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
        after = cut + "…"
    return before, link, after
