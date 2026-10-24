"""Share previews: the image and the words a link unfurls into.

A shared link (``/?from=A&to=B``) unfurls in Slack, Discord or a social feed
as a card showing that route. Crawlers do not run JavaScript, so both the
``<meta>`` tags and the image are produced here, from the route kept in Redis
under ``route:{start}:{end}`` when the search finished. A link to a route that
was never searched (or has expired) still gets a card: it asks the question
instead of answering it.

The card follows the published OG image: GitHub-dark ground, JetBrains Mono,
green start, red end, blue path, on a faint dot field.
"""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
SCALE = 2
"""Drawn at twice the size and downsampled: Pillow does not antialias shapes."""

FONTS = Path(__file__).parent / "fonts"

GROUND = "#0d1117"
RAISED = "#161b22"
RULE = "#21262d"
RULE_STRONG = "#30363d"
INK = "#f0f6fc"
INK_MUTED = "#9198a1"
INK_FAINT = "#7d8590"
BLUE = "#58a6ff"
GREEN = "#3fb950"
RED = "#f85149"
DOT = (139, 148, 158, 40)

MAX_ROWS = 7
SITE = "IRIS.HISHAAM.DEV"


@dataclass(frozen=True, slots=True)
class ShareMeta:
    title: str
    description: str


def describe(start: str, end: str, path: list[str] | None) -> ShareMeta:
    if path:
        hops = len(path) - 1
        links = f"{hops} link{'s' if hops != 1 else ''}"
        return ShareMeta(
            title=f"{start} → {end} in {links} · Iris",
            description=f"{' → '.join(path)}. A route through Wikipedia, found live by Iris.",
        )
    return ShareMeta(
        title=f"{start} → {end} · Iris",
        description=f"How does {start} lead to {end} on Wikipedia? Iris follows the links live and draws the route.",
    )


def with_share_meta(page: str, meta: ShareMeta, url: str, image: str) -> str:
    """Point the page's title and Open Graph / Twitter tags at one route."""
    values = {
        "og:title": meta.title,
        "twitter:title": meta.title,
        "og:description": meta.description,
        "twitter:description": meta.description,
        "description": meta.description,
        "og:url": url,
        "og:image": image,
        "twitter:image": image,
    }
    for key, value in values.items():
        page = re.sub(
            rf'(<meta (?:property|name)="{re.escape(key)}" content=")[^"]*(")',
            lambda m, v=value: m.group(1) + html.escape(v, quote=True) + m.group(2),
            page,
            count=1,
        )
    return re.sub(
        r"<title>[^<]*</title>",
        lambda _: f"<title>{html.escape(meta.title)}</title>",
        page,
        count=1,
    )


# --- The card -------------------------------------------------------------


@cache
def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"JetBrainsMono-{weight}.ttf"), size * SCALE)


def render_card(start: str, end: str, path: list[str] | None) -> bytes:
    image = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE), GROUND)
    draw = ImageDraw.Draw(image, "RGBA")

    _dot_field(draw)
    _masthead(draw)
    if path:
        _route(draw, path)
    else:
        _question(draw, start, end)
    _footer(draw, "WIKIPEDIA PATH  •  FOUND LIVE" if path else "WIKIPEDIA PATH")

    image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _s(*values: float) -> list[float]:
    """Scale card coordinates to the drawing surface."""
    return [v * SCALE for v in values]


def _xy(x: float, y: float) -> tuple[float, float]:
    return x * SCALE, y * SCALE


def _dot_field(draw: ImageDraw.ImageDraw) -> None:
    for x in range(20, WIDTH, 20):
        for y in range(20, HEIGHT, 20):
            draw.ellipse(_s(x - 0.75, y - 0.75, x + 0.75, y + 0.75), fill=DOT)


def _masthead(draw: ImageDraw.ImageDraw) -> None:
    # The logo: start and end on top, the path node below, joined by lines.
    a, b, c = (78, 52), (100, 52), (89, 72)
    for p, q in ((a, b), (a, c), (b, c)):
        draw.line(_s(*p, *q), fill=INK_MUTED, width=2 * SCALE)
    for (x, y), colour in ((a, GREEN), (b, RED), (c, BLUE)):
        draw.ellipse(_s(x - 5, y - 5, x + 5, y + 5), fill=colour)
    _tracked(draw, (118, 50), "IRIS", _font("Regular", 18), INK_MUTED, 0.18)


def _footer(draw: ImageDraw.ImageDraw, left: str) -> None:
    draw.line(_s(72, 548, WIDTH - 72, 548), fill=RULE, width=SCALE)
    font = _font("Regular", 14)
    _tracked(draw, (72, 566), left, font, INK_FAINT, 0.14)
    width = _tracked_width(SITE, font, 0.14)
    _tracked(draw, (WIDTH - 72 - width / SCALE, 566), SITE, font, BLUE, 0.14)


def _route(draw: ImageDraw.ImageDraw, path: list[str]) -> None:
    rows = _rows(path)
    top, bottom = 118, 520
    step = min(62, (bottom - top) / max(len(rows), 1))
    first_y = (top + bottom) / 2 - step * (len(rows) - 1) / 2
    last_y = first_y + step * (len(rows) - 1)

    # The rail, then a ruled row and a mark per step, as in the route list.
    rail_x = 80
    draw.line(
        _s(rail_x, first_y, rail_x, last_y), fill=(88, 166, 255, 115), width=SCALE
    )
    title_font = _font("SemiBold", 26 if len(rows) <= 5 else 23)
    index_font = _font("Regular", 16)
    for i, (number, title) in enumerate(rows):
        y = first_y + i * step
        if i:
            draw.line(_s(104, y - step / 2, 760, y - step / 2), fill=RULE, width=SCALE)
        colour = GREEN if i == 0 else RED if i == len(rows) - 1 else BLUE
        if number is None:
            colour = RULE_STRONG
        r = 7
        draw.ellipse(
            _s(rail_x - r - 4, y - r - 4, rail_x + r + 4, y + r + 4), fill=GROUND
        )
        draw.ellipse(_s(rail_x - r, y - r, rail_x + r, y + r), fill=colour)
        label = f"{number:02d}" if number is not None else "··"
        draw.text(_xy(110, y), label, font=index_font, fill=INK_FAINT, anchor="lm")
        text = _fit(title, title_font, 600)
        draw.text(
            _xy(160, y),
            text,
            font=title_font,
            fill=INK if number is not None else INK_FAINT,
            anchor="lm",
        )

    hops = len(path) - 1
    figure = str(hops)
    draw.text(
        _xy(1128, 300), figure, font=_font("SemiBold", 168), fill=INK, anchor="rs"
    )
    caption = "link" if hops == 1 else "links"
    draw.text(
        _xy(1128, 344), caption, font=_font("Regular", 26), fill=INK_MUTED, anchor="rs"
    )
    draw.text(
        _xy(1128, 380),
        f"{len(path)} pages",
        font=_font("Regular", 18),
        fill=INK_FAINT,
        anchor="rs",
    )


def _rows(path: list[str]) -> list[tuple[int | None, str]]:
    """Every step when they fit; otherwise both ends and an elision between."""
    numbered: list[tuple[int | None, str]] = [
        (i + 1, title) for i, title in enumerate(path)
    ]
    if len(numbered) <= MAX_ROWS:
        return numbered
    hidden = len(path) - (MAX_ROWS - 1)
    head, tail = numbered[:3], numbered[-(MAX_ROWS - 4) :]
    return [*head, (None, f"{hidden} more"), *tail]


def _question(draw: ImageDraw.ImageDraw, start: str, end: str) -> None:
    font = _font("SemiBold", 40)
    for y, title, colour in ((236, start, GREEN), (396, end, RED)):
        draw.ellipse(_s(72, y - 8, 88, y + 8), fill=colour)
        draw.text(_xy(110, y), _fit(title, font, 980), font=font, fill=INK, anchor="lm")
    # The dashed connector the page draws while a search is running.
    for y in range(262, 372, 12):
        draw.line(_s(80, y, 80, y + 6), fill=RULE_STRONG, width=2 * SCALE)
    draw.text(
        _xy(110, 316),
        "How does one lead to the other?",
        font=_font("Regular", 22),
        fill=INK_MUTED,
        anchor="lm",
    )


# --- Text helpers -----------------------------------------------------------


def _fit(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    if font.getlength(text) <= max_width * SCALE:
        return text
    while len(text) > 1 and font.getlength(text + "…") > max_width * SCALE:
        text = text[:-1]
    return text.rstrip() + "…"


def _tracked_width(text: str, font: ImageFont.FreeTypeFont, tracking: float) -> float:
    gap = font.size * tracking
    return sum(font.getlength(ch) for ch in text) + gap * (len(text) - 1)


def _tracked(
    draw: ImageDraw.ImageDraw,
    at: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str,
    tracking: float,
) -> None:
    """Letter-spaced capitals, as the published OG image sets its labels."""
    x, y = at[0] * SCALE, at[1] * SCALE
    gap = font.size * tracking
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + gap
