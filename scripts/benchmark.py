"""Time real searches against live Wikipedia, starting from a cold cache.

Usage:
    uv run python scripts/benchmark.py --label before --out bench-before.json
    uv run python scripts/benchmark.py --label after --out bench-after.json
    uv run python scripts/benchmark.py --compare bench-before.json bench-after.json

Every run flushes a dedicated Redis database first, so each search pays the
full cost of talking to Wikipedia. Production settings are used throughout.
Needs a local Redis and network access; it is not part of the test suite.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.errors import PathNotFoundError  # noqa: E402
from app.pathfinding import PathFinder  # noqa: E402
from app.store import RedisStore  # noqa: E402
from app.wikipedia import WikipediaClient  # noqa: E402

REDIS_URL = "redis://localhost:6379/15"

PAIRS = [
    ("Pikachu", "Quantum chromodynamics"),
    ("Barack Obama", "Mathematics"),
    ("Kangaroo", "Fourier transform"),
    ("Pizza", "Byzantine Empire"),
    ("Taylor Swift", "Photosynthesis"),
    ("Mount Everest", "Jazz"),
]


class CountingSession(requests.Session):
    """A real session that also counts the HTTP requests it sends."""

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:
        self.count += 1
        return super().request(*args, **kwargs)


def run_once(settings: Settings, start: str, end: str) -> dict[str, Any]:
    store = RedisStore.connect(REDIS_URL, settings.links_cache_ttl)
    store._redis.flushdb()
    session = CountingSession()
    # A real requests.Session does not match the narrow HttpSession protocol's
    # signature, which exists to type the test fakes.
    wikipedia = WikipediaClient(settings, cache=store, session=session)  # type: ignore[arg-type]

    began = time.monotonic()
    try:
        result = PathFinder(wikipedia, store, settings).find(start, end)
        path, explored = result.path, result.nodes_explored
    except PathNotFoundError:
        path, explored = [], None
    elapsed = time.monotonic() - began

    return {
        "start": start,
        "end": end,
        "seconds": round(elapsed, 2),
        "requests": session.count,
        "nodes_explored": explored,
        "path_length": len(path),
        "path": path,
    }


def benchmark(label: str, repeats: int) -> dict[str, Any]:
    settings = replace(
        Settings(env="production", secret_key="benchmark", redis_url=REDIS_URL),
        log_level="WARNING",
    )
    # Lets one script measure other tunings: BENCH_WIKIPEDIA_WORKERS=6, ...
    for field in ("wikipedia_workers", "batch_size", "wikipedia_max_pages"):
        if value := os.environ.get(f"BENCH_{field.upper()}"):
            settings = replace(settings, **{field: int(value)})
    runs = []
    for start, end in PAIRS:
        for attempt in range(repeats):
            run = run_once(settings, start, end)
            runs.append(run)
            print(
                f"{label:>7}  {start} -> {end} [{attempt + 1}/{repeats}]  "
                f"{run['seconds']:>6.2f}s  {run['requests']:>4} req  "
                f"{run['path_length']} pages",
                flush=True,
            )
    return {"label": label, "repeats": repeats, "runs": runs}


def compare(before_path: str, after_path: str) -> None:
    before = json.loads(Path(before_path).read_text())
    after = json.loads(Path(after_path).read_text())

    def by_pair(data: dict[str, Any]) -> dict[tuple[str, str], list[dict]]:
        grouped: dict[tuple[str, str], list[dict]] = {}
        for run in data["runs"]:
            grouped.setdefault((run["start"], run["end"]), []).append(run)
        return grouped

    old, new = by_pair(before), by_pair(after)
    header = (
        f"{'pair':<42}{'time before':>12}{'after':>9}{'speedup':>9}"
        f"{'req before':>12}{'after':>7}{'pages':>7}"
    )
    print(header)
    print("-" * len(header))

    totals = {"t0": 0.0, "t1": 0.0, "r0": 0, "r1": 0}
    for pair, runs in old.items():
        t0 = statistics.median(r["seconds"] for r in runs)
        t1 = statistics.median(r["seconds"] for r in new[pair])
        r0 = statistics.median(r["requests"] for r in runs)
        r1 = statistics.median(r["requests"] for r in new[pair])
        lengths = f"{runs[0]['path_length']}/{new[pair][0]['path_length']}"
        totals = {
            "t0": totals["t0"] + t0,
            "t1": totals["t1"] + t1,
            "r0": totals["r0"] + r0,
            "r1": totals["r1"] + r1,
        }
        name = f"{pair[0]} -> {pair[1]}"
        print(
            f"{name:<42}{t0:>11.2f}s{t1:>8.2f}s{t0 / t1:>8.1f}x"
            f"{r0:>12.0f}{r1:>7.0f}{lengths:>7}"
        )

    print("-" * len(header))
    print(
        f"{'total':<42}{totals['t0']:>11.2f}s{totals['t1']:>8.2f}s"
        f"{totals['t0'] / totals['t1']:>8.1f}x"
        f"{totals['r0']:>12.0f}{totals['r1']:>7.0f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Time real searches against live Wikipedia."
    )
    parser.add_argument("--label", default="run")
    parser.add_argument("--out")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
        return

    data = benchmark(args.label, args.repeats)
    if args.out:
        Path(args.out).write_text(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
