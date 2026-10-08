---
name: Iris
description: Wikipedia path finder, drawn as an instrument on hairline rules.
colors:
  ground: "#0d1117"
  ground-raised: "#161b22"
  ground-sunken-fill: "#21262d"
  rule: "#21262d"
  rule-strong: "#30363d"
  ink: "#f0f6fc"
  ink-muted: "#9198a1"
  ink-faint: "#7d8590"
  path-blue: "#58a6ff"
  path-blue-bright: "#79c0ff"
  from-green: "#3fb950"
  to-red: "#f85149"
  to-red-ink: "#ff7b72"
typography:
  display:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "clamp(28px, 4.4vw, 46px)"
    fontWeight: 600
    lineHeight: 1.12
    letterSpacing: "-0.035em"
  reading:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "22px"
    fontWeight: 500
    letterSpacing: "-0.02em"
    fontFeature: "\"tnum\""
  control:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "18px"
    fontWeight: 500
    letterSpacing: "-0.01em"
  title:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "16px"
    fontWeight: 600
    letterSpacing: "-0.01em"
  body:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.6
    fontFeature: "\"tnum\""
  small:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "12px"
    fontWeight: 600
    letterSpacing: "0.02em"
rounded:
  none: "0px"
  chip: "4px"
  control: "6px"
  dot: "50%"
spacing:
  sp-1: "4px"
  sp-2: "8px"
  sp-3: "12px"
  sp-4: "16px"
  sp-5: "20px"
  sp-6: "24px"
  sp-8: "32px"
  sp-10: "40px"
  sp-12: "48px"
  sp-14: "56px"
  sp-18: "72px"
  sp-24: "96px"
components:
  button-primary:
    backgroundColor: "{colors.path-blue}"
    textColor: "{colors.ground}"
    rounded: "{rounded.control}"
    padding: "0 20px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.path-blue-bright}"
    textColor: "{colors.ground}"
  button-primary-disabled:
    backgroundColor: "{colors.ground-sunken-fill}"
    textColor: "{colors.ink-faint}"
  button-quiet:
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0 20px"
    height: "44px"
  button-quiet-hover:
    textColor: "{colors.to-red-ink}"
  button-text:
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "44px"
  input-route:
    textColor: "{colors.ink}"
    typography: "{typography.control}"
    rounded: "{rounded.none}"
    padding: "12px 0"
  label-chip:
    backgroundColor: "{colors.ground-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.chip}"
    padding: "4px 12px"
  step-row:
    textColor: "{colors.ink}"
    padding: "12px 0"
    height: "52px"
  tooltip:
    backgroundColor: "{colors.ground-raised}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
---

# Design System: Iris

## Overview

**Creative North Star: "The Bench Instrument"**

Iris is read like a piece of test equipment on a dark bench: one column of figures ruled off by 1px hairlines, a plotted field under the readings, and nothing wrapped in a card. Structure comes from rules, type and spacing alone. The page flows top to bottom in the order a visitor uses it: a display question, an underline-field route form, a run header with live readings, the dot-field plot framed by corner registration marks, and the route as a ruled list.

The world is GitHub dark and JetBrains Mono, both binding brand commitments: the README and OG image are built on them. Colour is almost entirely neutral ground and ink; the only chromatic voices are three signals with fixed meanings (green is where the route starts, red where it ends, blue is the path itself and the one primary action). Motion exists only to show the search working.

The build deliberately refuses the stacked-card SaaS layout the previous page used, neon "cyber" glow, SaaS gradients and glassmorphism, and bouncy graph physics.

**Key Characteristics:**
- One shell-width column (1040px) ruled by hairlines; no sectional cards or boxes inside boxes.
- One typeface (JetBrains Mono) for everything, tabular figures on by default.
- Three signal colours with fixed meanings, repeated across form labels, run header, graph nodes and the route rail.
- Flat ground; the primary button is the only filled control.
- Motion only as search state (sweep, beat, edge draw-in), with a reduced-motion fallback that fades rather than freezes.

## Colors

A near-black GitHub-dark ground with cool grey ink and three saturated signals that each carry exactly one meaning.

### Primary
- **Path Blue** (`path-blue`): the path and the act of finding it. Primary button fill, path edges and arrowheads in the graph, intermediate graph nodes, the route rail and step marks, depth dots, the "Reading" link, the activity sweep, the focus ring, and in-text actions ("How it works", "Try again").
- **Bright Path Blue** (`path-blue-bright`): hover state for every Path Blue element. Never used at rest.

### Secondary
- **From Green** (`from-green`): the start page and only the start page. The dot before the "From" label, the first end-mark in the run header, the start chip in the wait scene, the start node, the first step mark. Also the "found" state dot.

### Tertiary
- **To Red** (`to-red`): the end page, and danger. The dot before the "To" label, the end-mark, the end chip and node, the last step mark; also the invalid-field underline, the notice's tone rule, and the hover border of the "Stop search" button.
- **To Red Ink** (`to-red-ink`): the legible text variant of red on the dark ground; notice titles and the "Stop search" hover label.

### Neutral
- **Bench Ground** (`ground`): the page itself, and the knock-out ring that separates step marks from the rail.
- **Raised Ground** (`ground-raised`): the one raised surface tone, used only for graph label chips, wait-scene chips and the tooltip.
- **Sunken Fill** (`ground-sunken-fill`): the disabled primary button. Shares its value with `rule` but is a separate role.
- **Hairline** (`rule`): section rules, reading dividers, step row rules, the masthead and colophon rules.
- **Strong Hairline** (`rule-strong`): rules that must read against interaction: input underlines, the quiet button border, the activity track, the how-it-works rules, chip borders, unexplored depth dots, non-path graph edges.
- **Ink** (`ink`): primary text and figures.
- **Muted Ink** (`ink-muted`): supporting prose, field labels, secondary links, run state.
- **Faint Ink** (`ink-faint`): reading labels, step indices, placeholders, inline arrows, registration marks, the colophon.

### Named Rules
**The Three Signals Rule.** Green means from, red means to, blue means path. The language runs through the form labels, the run header end-marks, the wait chips, the graph nodes and the route-list rail; change one and you change all of them. No signal colour is ever used decoratively.

**The Token Colour Rule.** Every colour comes from a `:root` custom property; D3 reads `--accent-blue` at render time. The only literal colours are translucent tints of a signal (the endpoint node halos, the dot field) and the tooltip shadow.

## Typography

**Display Font:** JetBrains Mono (with ui-monospace, monospace)
**Body Font:** JetBrains Mono (with ui-monospace, monospace)
**Label/Mono Font:** JetBrains Mono

**Character:** A single monospace face carries the whole page, so hierarchy comes from size, weight and tracking alone. Figures are tabular everywhere so readings do not jitter as they count.

### Hierarchy
- **Display** (600, `clamp(28px, 4.4vw, 46px)`, 1.12, -0.035em): the single lead question; capped at 21ch and balanced.
- **Reading** (500, 22px, -0.02em; 18px under 640px): the live figures in the readings row.
- **Control** (500, 18px, -0.01em): text typed into the route fields.
- **Title** (600, 16px, -0.01em): the wordmark, the run header route, and the "Route" list title.
- **Body** (400, 15px, 1.6): lead copy, step titles (at 500), the wide "Reading" value. Prose capped at `--measure` (62ch).
- **Small** (400, 13px, 1.6): masthead links, how-it-works text, notice message and hint, run state, step index, route summary.
- **Label** (600, 12px, +0.02em): field labels and reading labels (reading labels at 400 in Faint Ink); also graph node labels and the tooltip at 500.

### Named Rules
**The One Face Rule.** JetBrains Mono for every glyph on the page, including SVG node labels and the tooltip. No second family, no system display face.

**The Control Floor Rule.** Text inputs never drop below 16px (built at 18px); anything smaller makes iOS Safari zoom the viewport on focus.

## Layout

A single centred column: `--shell` (1040px) plus a fluid gutter (`clamp(16px, 4vw, 40px)`) each side. Every section shares that width so rules line up edge to edge; prose inside is capped by `--measure` (62ch) rather than by narrowing the shell. The graph field is the full shell width. The route list sits below the graph, not beside it, at every width.

Spacing comes from the `--sp-*` scale (4px base; the step number is the multiple). Vertical rhythm is generous between movements and tight within them: 72px above the lead, 48px to the route form, 56px to the run, 32px to the field, 40px to the route list, 96px to the colophon; 4 to 12px within a group.

Responsive behaviour:
- **Under 860px:** how-it-works becomes two columns; the wide "Reading" reading drops to its own row, ruled on top.
- **Under 640px:** lead and section gaps tighten one or two steps; the route fields stack and the inline arrow disappears; primary and quiet buttons share the row equally; readings go to two columns; the wait scene and its dashed line turn vertical.
- **Under 520px of graph width (script):** the graph lays the path out top to bottom with label chips hanging below nodes, and edges start beneath the source chip.
- **Short landscape (height under 500px):** lead padding and field minimum height shrink.

The masthead is 64px; every interactive target is at least 44px.

### Named Rules
**The Hairline Rule.** Sections, readings, steps and notices are separated by 1px rules (`rule` or `rule-strong`), never by bordered or filled panels. A new section is a rule and a gap, not a card.

**The Token Spacing Rule.** Reach for an `--sp-*` step, not a literal; ad-hoc pixel values are how the rhythm flattened before.

## Elevation & Depth

The page is flat. Depth is conveyed by tone (Raised Ground for chips) and by the rules, not by shadow. Two shadow-shaped values exist and neither is elevation: the focus ring and the step-mark knock-out ring. One true shadow exists, on the floating tooltip, because it sits over the graph at the pointer.

### Shadow Vocabulary
- **Focus ring** (`box-shadow: 0 0 0 2px #0d1117, 0 0 0 4px #58a6ff`): every `:focus-visible`; inputs instead swap their underline to blue with a 1px blue under-shadow.
- **Knock-out ring** (`box-shadow: 0 0 0 3px #0d1117`): step marks, so the rail appears to pass behind them.
- **Tooltip lift** (`box-shadow: 0 8px 24px rgba(1, 4, 9, 0.6)`): the graph tooltip only.

### Named Rules
**The Flat Ground Rule.** Nothing on the page casts a shadow except the pointer tooltip. Do not lift sections, buttons or chips.

## Shapes

Square by default. Inputs, notices, rules and step rows have no radius. Controls (buttons, icon buttons, skip link, tooltip) take a gentle 6px corner; graph and wait-scene label chips take 4px. Signals are perfect circles: 8px label and end-mark dots, 9px step marks, 6px state dots, 8px depth dots, 14px-radius graph nodes. The graph field's only frame is four 14px corner registration marks drawn in 1px Faint Ink. Chevrons and arrows are drawn strokes (1.5 to 1.6px, round caps), never glyphs.

## Components

### Buttons
Restrained and exact: one filled action, everything else outlined or bare.
- **Shape:** gentle 6px corners, 44px minimum height, 600 weight at 14px.
- **Primary:** Path Blue fill with Bench Ground text, 20px side padding. Disabled only while a search runs or a field is empty, then Sunken Fill with Faint Ink.
- **Hover / Focus / Active:** hover brightens to Bright Path Blue; focus is the shared ring; active nudges down 1px. Transitions 0.15s.
- **Quiet ("Stop search"):** transparent with a Strong Hairline border; on hover the border turns To Red and the label To Red Ink.
- **Text ("Clear", "Try again"):** bare Muted Ink, 12px side padding; inside a notice it becomes Path Blue.
- **Icon button:** 44px square, 16px stroked SVG in Faint Ink, Ink on hover.

### Inputs / Fields
- **Style:** underline fields. Transparent, no box, no radius, a 1px Strong Hairline underline, 12px vertical padding, Control type. Each label carries its signal dot (green "From", red "To"); between the fields sits a single 22px stroked arrow.
- **Focus:** underline turns Path Blue with a 1px blue under-shadow, effectively a 2px line.
- **Hover:** underline lifts to Faint Ink.
- **Error:** `aria-invalid="true"` turns the underline To Red with the same 1px under-shadow.

### Notice
The error and "stopped" message, ruled not boxed. A three-column row: 20px stroked icon in the tone colour, body, actions. A tone rule on top (the tone at 55% via `color-mix`), a Hairline below, 16px vertical padding, no fill, no radius. The title names the problem (To Red Ink, 600), the message adds a new fact (Ink, Small), the hint gives recovery (Muted Ink, Small); empty lines collapse. The neutral variant swaps the tone to Faint Ink and the title to Ink. It enters with a 4px drop-in over 0.35s.

### Navigation
The masthead is a 64px bar ruled below: the wordmark (24px logo plus "Iris" at Title weight 700) at left, Small Muted Ink links at right that turn Ink on hover, 24px apart.

### Readings
The signature of the instrument: a row of figures separated by vertical Hairlines, no boxes. Four equal columns plus a double-width "Reading" column holding the page currently being read as a Path Blue link (Faint Ink and inert when empty). Labels are 12px Faint Ink; values are Reading type. Depth is shown as a row of 8px circles: hollow (Strong Hairline), active (blue outline, beating), completed (blue fill). Above the readings a 1px activity track carries a blue sweep while searching.

### The Field (graph plot)
A dot field (1px dots at 16% grey on a 20px grid) framed by corner registration marks, 360px minimum height (300px on phones). While searching it shows only the two ends as label chips joined by a dashed 1px line with a travelling blue highlight; when the path arrives the D3 graph replaces it. Nodes are blue with a 2px ground stroke; the start and end nodes take their signal colour with a translucent 5px halo of the same hue. Path edges are 2.5px Path Blue with a single SVG arrowhead; other edges are 2px Strong Hairline at 60%. Label chips are Raised Ground with a 1px Strong Hairline border and 4px corners.

The force simulation is tuned and fixed: link distance spans the available axis with strength 1; charge -240 (horizontal) or -160 (vertical); x/y position forces 0.22/0.10 (horizontal) or 0.16/0.22 (vertical); collision strength 0.8; alphaDecay 0.03; velocityDecay 0.45. These values are part of the system, not tuning knobs.

### Route List
The found route as a ruled ordered list: each step is a 52px row ruled on top, with a 9px mark, a 36px index column, the title and a stroked open-arrow. A 1px blue rail at 45% opacity threads the marks, starting at the first mark and ending at the last; first mark green, last red, the rest blue. Hover turns the title Path Blue and nudges the arrow 2px up-right.

## Do's and Don'ts

### Do:
- **Do** separate every section, reading and step with a 1px `rule` or `rule-strong` hairline and a spacing step.
- **Do** keep green for the start, red for the end and blue for the path, everywhere at once.
- **Do** set every glyph in JetBrains Mono with tabular figures.
- **Do** take spacing from `--sp-*` and type sizes from `--fs-*`; keep inputs at 16px or above.
- **Do** keep every touch target at 44px or more.
- **Do** give every animation a reduced-motion form that fades rather than freezes, so a running search stays visible.
- **Do** draw arrows, chevrons and icons as stroked SVG paths with round caps.
- **Do** leave the D3 force parameters exactly as recorded unless the task is about graph physics.

### Don't:
- **Don't** wrap page sections, readings or notices in filled or bordered cards; chips belong only to graph node labels, the wait-scene ends and the tooltip.
- **Don't** add drop shadows to anything but the pointer tooltip.
- **Don't** add blurred glows, gradients as surfaces, or glassmorphism; the only gradients are the 1px activity sweeps and the dot field.
- **Don't** use a signal colour for decoration or for a meaning other than its one role.
- **Don't** add a second typeface or a light theme; the GitHub-dark palette and JetBrains Mono are published brand assets.
- **Don't** animate anything that is not search state.
