# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Curious people and developers exploring how Wikipedia topics connect. They type two page titles, wait through a live search (seconds to a couple of minutes), then explore the resulting path. Used casually in a browser, desktop and mobile, mostly in dark environments (the UI is dark-first).

A second audience arrives to look at the work rather than to use it: peers, recruiters and other developers following a link to see how it is built. Both land on the same single page, so it has to be usable cold and worth inspecting.

## Product Purpose

Iris finds the shortest hyperlink path between two Wikipedia pages using bidirectional BFS over the live Wikipedia link graph, streams search progress in real time, and visualizes the found path as an interactive D3 force graph. Success: the search feels alive while running, and the resulting path is instantly readable and explorable.

## Positioning

Iris walks the **live** Wikipedia API at request time rather than querying a periodic database dump, and streams the expanding frontier while it does. That is the difference a neighbouring tool built on a precomputed index cannot truthfully copy: Iris is always current, and it shows the algorithm working instead of only presenting an answer. The trade is deliberate and stated below — liveness and visible progress in exchange for a path that is the first one found, not a proven global shortest.

The implementation is itself part of the point. The project stands as a demonstration of the engineering — bidirectional BFS with Redis-backed frontiers, Celery-streamed progress — not only as a utility.

## Operating Context

- Single page, no accounts, no persistence beyond the browser. A visitor types two titles and presses one button.
- Searches run as background Celery tasks and are polled by the UI; a search survives a page refresh because the latest one is kept in `localStorage`.
- Deployed publicly at `iris.hishaam.dev` (Railway, Docker; `entrypoint.sh` switches role on `SERVICE_TYPE`). It is live but not actively promoted, so traffic is incidental and stakes are low — strangers can still arrive cold with no context.
- A run takes seconds to a couple of minutes. Waiting is a normal part of the experience, not an error state.
- The API is public and documented via Swagger at `/api/docs`; some visitors will use it directly rather than the UI.

## Capabilities and Constraints

- Two algorithms: `bidirectional` (default; forward links plus backlinks, meeting in the middle) and `bfs` (forward only). Both are the same code path over one or two frontiers.
- **English Wikipedia only.** No other language editions or wikis.
- **The result is the first path found, not a guaranteed global shortest.** Wikipedia caps how many links one request reports and Iris follows at most three continuations per article (~1,500 links), so BFS can miss edges out of very highly connected pages. Future work must not claim provable optimality.
- Depth is capped (6 in production, 4 in development). A bidirectional search splits that budget across the two frontiers.
- Requests to Wikipedia are globally rate-limited across worker threads, so search time is dominated by the API, not by compute.
- Celery enforces soft and hard time limits (300s/600s in production); a long search can be cut off.
- Titles the user types are resolved to canonical Wikipedia titles before searching. Casing and redirects are handled at the boundary, not inside the search.
- A disambiguation page is rejected as a target but allowed as a start, because its links are still useful for getting somewhere else.
- Search state, link caches and results all live in Redis with TTLs. Nothing is durable; a cold cache is a normal state.
- Terminology, as used in the UI and the API: *start page* / *end page*, *path*, *step*, *nodes explored*, *queue size*, *depth*, *frontier*.

## Brand Commitments

- Name: **Iris**; full title "Iris: Wikipedia Path Finder".
- Existing identity assets, all in `static/`: `logo.svg`, `favicon-dark.svg` / `favicon-light.svg` (switched on `prefers-color-scheme`), `og-image.svg` / `og-image.png`.
- The GitHub-dark palette and JetBrains Mono are binding: the README and OG image are built on them, so a change to the theme is a change to published assets.
- GPL-3.0 licensed (see `LICENSE`), open source at `github.com/mdhishaamakhtar/iris`.
- Built by Md Hishaam Akhtar and Sharanya Mukherjee under DSC VIT; the README carries that attribution.

## Evidence on Hand

- Real, working artifacts: the live deployment, the D3 path visualization, streamed search progress, and Swagger API documentation at `/api/docs`.
- Identity assets listed under Brand Commitments; README preview image at `static/og-image.svg`.
- Test suite and coverage tooling in the repo (`uv run pytest --cov=app`).
- **Absent, and not to be fabricated:** usage metrics, user counts, traffic figures, testimonials, case studies, press, benchmarks against other tools, uptime claims, and any named users or customers. None exist.

## Product Principles

1. **Liveness over provable optimality.** The live graph and the visible search are the reason to use Iris. Never trade them for a precomputed index, and never overstate the result as the proven shortest path.
2. **Show the work.** The wait is the product's most distinctive moment. Progress must read as an algorithm advancing — depth, frontier size, current page — not as a generic spinner.
3. **The answer is explorable.** A found path is something to open, drag and follow, and it must stay legible without the graph for anyone who cannot or does not use it.
4. **Usable cold.** A stranger with no context is a normal visitor. Labels, empty states and errors must explain themselves without prior knowledge.
5. **Degrade honestly.** Rate limits, depth caps, timeouts and cold caches are ordinary. Say what happened and what to try, rather than failing silently or pretending to still be working.

## Brand Personality

Technical, precise, calm. A developer-tool aesthetic: GitHub-dark palette, JetBrains Mono everywhere, restrained accent blue (#58A6FF), green for start/success, red for end/danger. The interface should feel like a well-built instrument, not a toy.

## Anti-references

- Neon "cyber" dashboards with glow on everything.
- Generic SaaS gradients and glassmorphism.
- Cartoonish bouncy physics; the graph should settle with confidence, not jiggle.

## Design Principles

1. **Motion conveys search state.** Animation exists to show the algorithm working (frontiers expanding, path found), never as decoration.
2. **The path is the hero.** Once found, the start → end chain must be readable at a glance: direction, order, endpoints.
3. **Earned familiarity.** Standard affordances (drag, click to open, hover tooltips); no invented controls.
4. **Dark-first, token-driven.** All colors flow from the CSS variables in styles.css; D3 reads tokens at render time.

## Accessibility & Inclusion

- Respect `prefers-reduced-motion` for every animation. The loading state must stay perceivable when motion is reduced, not freeze.
- Keyboard access for interactive elements (path steps, currently-exploring link).
- ARIA labels on the SVG graph, and a live region for errors.
- Search progress is deliberately **not** a live region: it updates once per fetched page and would flood a screen reader. The panel is labelled and findable instead.
- Touch: drag must not fight page scroll; 44px minimum touch targets.
- Form controls hold at 16px so iOS Safari does not zoom the viewport on focus.
