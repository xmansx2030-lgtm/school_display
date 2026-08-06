"""Generate the display board's legacy stylesheet.

The board's design leans on CSS that post-dates the browsers shipped in the
televisions it runs on: `clamp()` (Chrome 79), `color-mix()` (Chrome 111),
CSS grid (Chrome 57) and flex `gap` (Chrome 84). None of those declarations
carried a fallback, so on a Samsung Tizen 3.0 panel — Chrome 56, which
`display.js` deliberately supports — every clamped font size collapsed to its
inherited value and the two-column layout stacked into one.

This command derives `static/css/display-legacy.css` from `display-board.css`.
Everything it emits is wrapped in `@supports not (…)`, so a modern browser
parses the file and applies **nothing**: the legacy sheet cannot regress the
normal rendering path, which is the whole point of generating it separately
instead of editing 137 declarations in place.

    python manage.py build_legacy_css
    python manage.py build_legacy_css --check     # CI: fail if out of date

Flex `gap` is the one feature `@supports` cannot express — `@supports (gap:1px)`
is true from Chrome 57 even though flex ignored it until 84 — so display.js
measures it and sets `body[data-nogap="1"]`, which the layout section below
keys on.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

SOURCE = ("static", "css", "display-board.css")
TARGET = ("static", "css", "display-legacy.css")

# The board is laid out on a fixed 1920x1080 canvas (#fitStage) that is then
# scaled to fit, so evaluating viewport units against the design canvas gives
# the proportions the design was drawn at — independent of the real panel size.
DESIGN_WIDTH_PX = 1920
DESIGN_HEIGHT_PX = 1080
ROOT_FONT_PX = 16

UNIT_TO_PX = {
    "px": 1.0,
    "rem": float(ROOT_FONT_PX),
    "em": float(ROOT_FONT_PX),
    "vw": DESIGN_WIDTH_PX / 100.0,
    "vh": DESIGN_HEIGHT_PX / 100.0,
    "vmin": min(DESIGN_WIDTH_PX, DESIGN_HEIGHT_PX) / 100.0,
    "vmax": max(DESIGN_WIDTH_PX, DESIGN_HEIGHT_PX) / 100.0,
}

CLAMP_GATE = "@supports not (font-size: clamp(1px, 2px, 3px))"
COLOR_MIX_GATE = "@supports not (color: color-mix(in srgb, red, blue))"
GRID_GATE = "@supports not (display: grid)"

LENGTH_RE = re.compile(r"^(-?[\d.]+)(px|rem|em|vw|vh|vmin|vmax)$", re.I)
CLAMP_RE = re.compile(r"clamp\(([^()]*)\)", re.I)

HEADER = """/* Legacy fallbacks for the display board — GENERATED FILE, do not edit.
 *
 * Regenerate with:  python manage.py build_legacy_css
 *
 * Every block here sits behind `@supports not (…)`, so browsers that implement
 * the feature apply none of it. Load this file *after* display-board.css.
 *
 * Why it exists: display.js is deliberately built for Chrome 49+ so it runs on
 * Samsung Tizen 3.0/4.0 (Chrome 56) and LG webOS 4 (Chrome 53). The stylesheet
 * was not — clamp(), color-mix(), grid and flex gap all arrived later, and none
 * of them had a fallback, so the very panels the JS supported rendered a broken
 * board.
 *
 * Values for viewport units are evaluated against the 1920x1080 design canvas
 * the board is drawn on and then scaled by #fitStage, so they keep the intended
 * proportions on any panel size.
 */
"""

# Hand-written, because a grid template cannot be derived mechanically.
# Ordered to mirror the board: page shell, then the two columns, then the
# smaller grids inside the cards.
LEGACY_LAYOUT = """
/* ---------------------------------------------------------------------------
 * Layout: CSS grid -> flexbox            (grid landed in Chrome 57)
 * ------------------------------------------------------------------------ */
@supports not (display: grid) {
  /* The board shell is Tailwind's `grid grid-cols-12 gap-5`; without grid the
     column spans are ignored and both sections stack full width. */
  main.grid {
    display: flex !important;
    flex-wrap: wrap;
    align-items: stretch;
  }

  #boardMainColumn {
    flex: 0 0 66.4%;
    max-width: 66.4%;
  }

  #boardSideColumn {
    flex: 0 0 32.6%;
    max-width: 32.6%;
    margin-inline-start: 1%;
  }

  /* Two-column card interiors. */
  .header-shell,
  .honor-wrap,
  .occasion-hero,
  .binding-blocker__body,
  .binding-blocker__step {
    display: flex !important;
    align-items: center;
  }

  .header-shell {
    justify-content: space-between;
  }

  .honor-wrap > :last-child,
  .occasion-hero > :last-child,
  .binding-blocker__body > :last-child {
    flex: 1 1 auto;
    min-width: 0;
  }

  .binding-blocker__step {
    align-items: flex-start;
  }

  .binding-blocker__step > :last-child {
    flex: 1 1 auto;
    min-width: 0;
  }

  .binding-blocker__steps {
    display: flex !important;
    flex-direction: column;
  }

  /* `place-items: center` containers. */
  .occasion-hero__mark,
  .board-empty-state,
  .board-empty-state::before,
  .binding-blocker__brand-mark,
  .binding-blocker__device,
  .binding-blocker__connection strong,
  .binding-blocker__step span {
    display: flex !important;
    align-items: center;
    justify-content: center;
  }
}

/* ---------------------------------------------------------------------------
 * Spacing: flex `gap` -> margins         (flex gap landed in Chrome 84)
 * ---------------------------------------------------------------------------
 * `@supports (gap: 1px)` is true from Chrome 57 because grid accepted the
 * property first, so this cannot be feature-queried. display.js measures a
 * probe element and sets data-nogap; see supportsFlexGap().
 * ------------------------------------------------------------------------ */
body[data-nogap="1"] #boardMainColumn > * + *,
body[data-nogap="1"] #boardSideColumn > * + * {
  margin-block-start: 1.25rem;
}

body[data-nogap="1"] .slot-item + .slot-item,
body[data-nogap="1"] .duty-item + .duty-item {
  margin-block-start: 0.5rem;
}

body[data-nogap="1"] #standbyTrack > * + *,
body[data-nogap="1"] #periodClassesTrack > * + *,
body[data-nogap="1"] #dutyTrack > * + * {
  margin-block-start: 0.5rem;
}

body[data-nogap="1"] .header-live-status > * + *,
body[data-nogap="1"] .board-status > * + * {
  margin-inline-start: 0.6rem;
}

/* `margin-block-start` / `margin-inline-start` are Chrome 87. Anything old
   enough to need the gap fallback may also predate logical margins, so repeat
   the rules physically. The board is RTL, hence margin-right for inline-start. */
@supports not (margin-block-start: 1px) {
  body[data-nogap="1"] #boardMainColumn > * + *,
  body[data-nogap="1"] #boardSideColumn > * + * {
    margin-top: 1.25rem;
  }

  body[data-nogap="1"] .slot-item + .slot-item,
  body[data-nogap="1"] .duty-item + .duty-item,
  body[data-nogap="1"] #standbyTrack > * + *,
  body[data-nogap="1"] #periodClassesTrack > * + *,
  body[data-nogap="1"] #dutyTrack > * + * {
    margin-top: 0.5rem;
  }

  body[data-nogap="1"] .header-live-status > * + *,
  body[data-nogap="1"] .board-status > * + * {
    margin-right: 0.6rem;
  }
}

/* ---------------------------------------------------------------------------
 * Logical properties -> physical         (inset-inline landed in Chrome 87)
 * ------------------------------------------------------------------------ */
@supports not (inset-inline-end: 0) {
  .hero-card__orb--one {
    right: -120px;
  }

  .hero-card__orb--two {
    left: 25%;
  }
}
"""


def _length_to_px(token: str) -> float | None:
    match = LENGTH_RE.match(token.strip())
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2).lower()
    factor = UNIT_TO_PX.get(unit)
    return None if factor is None else value * factor


def _format_px(value: float) -> str:
    rounded = round(value, 1)
    if rounded == int(rounded):
        return f"{int(rounded)}px"
    return f"{rounded}px"


def resolve_clamp(value: str) -> str | None:
    """Evaluate every clamp() in a declaration against the design canvas.

    Returns None when any argument cannot be resolved to a length (percentages
    depend on the parent box), so the caller can skip that declaration rather
    than emit a wrong value.
    """
    resolved = value
    for match in CLAMP_RE.finditer(value):
        parts = [p.strip() for p in match.group(1).split(",")]
        if len(parts) != 3:
            return None
        lengths = [_length_to_px(p) for p in parts]
        if any(length is None for length in lengths):
            return None
        low, preferred, high = lengths
        resolved = resolved.replace(match.group(0), _format_px(min(max(low, preferred), high)))
    # CLAMP_RE only matches clamps without nested parentheses. If one survives,
    # the value would ship a function the target browser cannot parse.
    return None if "clamp(" in resolved.lower() else resolved


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are not inside parentheses."""
    parts, depth, current = [], 0, ""
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [p.strip() for p in parts]


def resolve_color_mix(value: str) -> str:
    """Replace color-mix() with its first colour, un-mixed.

    Every use in this codebase mixes a theme colour towards white, black or
    transparent to soften it. Dropping the mix keeps the hue the theme asked
    for, which is what matters; the exact tint is not reproducible without the
    variable's runtime value.

    The arguments routinely contain `var(--x, #hex)`, so the closing bracket has
    to be found by balancing parentheses — a non-greedy regex stops at the inner
    `var(` and produces a mangled value.
    """
    resolved = value
    while True:
        start = resolved.lower().find("color-mix(")
        if start == -1:
            return resolved

        depth, end = 0, None
        for i in range(start + len("color-mix"), len(resolved)):
            if resolved[i] == "(":
                depth += 1
            elif resolved[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            # Unbalanced input: leave it alone rather than emit broken CSS.
            return resolved

        args = _split_top_level(resolved[start + len("color-mix(") : end])
        # args[0] is the colour space ("in srgb"); args[1] is "<colour> <pct>".
        first = args[1] if len(args) > 1 else ""
        first = re.sub(r"\s+[\d.]+%\s*$", "", first).strip()
        resolved = resolved[:start] + (first or "currentColor") + resolved[end + 1 :]


def iter_rules(css: str):
    """Yield (at_rule_context, selector, declarations) for every style rule.

    A hand-rolled walker rather than a CSS library: the input is one known file
    and the project has no CSS parser dependency.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    stack: list[str] = []
    i = 0
    buffer = ""
    while i < len(css):
        char = css[i]
        if char == "{":
            prelude = buffer.strip()
            buffer = ""
            if prelude.startswith("@"):
                stack.append(prelude)
            else:
                end = css.find("}", i)
                if end == -1:
                    break
                yield tuple(stack), prelude, css[i + 1 : end]
                i = end
        elif char == "}":
            if stack:
                stack.pop()
            buffer = ""
        else:
            buffer += char
        i += 1


def _wrap(context: tuple[str, ...], body: str, indent: str = "  ") -> str:
    """Re-emit a rule inside the @media/@supports context it came from."""
    for at_rule in reversed(context):
        inner = "\n".join(indent + line if line.strip() else line for line in body.splitlines())
        body = f"{at_rule} {{\n{inner}\n}}"
    return body


class Command(BaseCommand):
    help = "Generate static/css/display-legacy.css from display-board.css."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Verify the committed file matches the source; write nothing.",
        )

    def handle(self, *args, **options):
        base = Path(__file__).resolve().parents[3]
        source_path = base.joinpath(*SOURCE)
        target_path = base.joinpath(*TARGET)

        if not source_path.exists():
            raise CommandError(f"{source_path} is missing.")

        generated, stats = self.build(source_path.read_text(encoding="utf-8"))

        if options["check"]:
            if not target_path.exists():
                raise CommandError(f"{target_path} is missing; run without --check.")
            if target_path.read_text(encoding="utf-8") != generated:
                raise CommandError(
                    f"{target_path.name} is out of date. Run: python manage.py build_legacy_css"
                )
            self.stdout.write(self.style.SUCCESS(f"{target_path.name} is up to date."))
            return

        target_path.write_text(generated, encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {target_path.name}: {stats['clamp']} clamp declarations and "
                f"{stats['color_mix']} color-mix declarations resolved"
                + (f", {stats['skipped']} skipped (percentage lengths)" if stats["skipped"] else "")
                + "."
            )
        )

    def build(self, css: str) -> tuple[str, dict]:
        clamp_rules: dict[tuple, list[tuple[str, list[str]]]] = {}
        mix_rules: dict[tuple, list[tuple[str, list[str]]]] = {}
        stats = {"clamp": 0, "color_mix": 0, "skipped": 0}

        for context, selector, declarations in iter_rules(css):
            clamp_decls: list[str] = []
            mix_decls: list[str] = []

            for raw in declarations.split(";"):
                if ":" not in raw:
                    continue
                prop, _, value = raw.partition(":")
                prop, value = prop.strip(), value.strip()
                if not prop or prop.startswith("--"):
                    # Custom properties are substituted at use time; rewriting
                    # them here would not help a browser that cannot parse the
                    # function in the first place.
                    continue

                has_clamp = "clamp(" in value
                has_mix = "color-mix(" in value
                if not (has_clamp or has_mix):
                    continue

                if has_clamp:
                    # A browser without clamp() certainly has no color-mix(),
                    # so resolve both for this gate.
                    resolved = resolve_clamp(resolve_color_mix(value) if has_mix else value)
                    if resolved is None:
                        stats["skipped"] += 1
                    else:
                        clamp_decls.append(f"{prop}: {resolved};")
                        stats["clamp"] += 1

                if has_mix:
                    # Chrome 79-110 has clamp() but not color-mix(); keep the
                    # clamp untouched for that population.
                    mix_decls.append(f"{prop}: {resolve_color_mix(value)};")
                    stats["color_mix"] += 1

            if clamp_decls:
                clamp_rules.setdefault(context, []).append((selector, clamp_decls))
            if mix_decls:
                mix_rules.setdefault(context, []).append((selector, mix_decls))

        parts = [HEADER]
        parts.append(self._gate(CLAMP_GATE, clamp_rules, "clamp() -> px (Chrome 79)"))
        parts.append(self._gate(COLOR_MIX_GATE, mix_rules, "color-mix() -> base colour (Chrome 111)"))
        parts.append(LEGACY_LAYOUT.rstrip() + "\n")
        return "\n".join(p for p in parts if p), stats

    def _gate(self, gate: str, rules: dict, title: str) -> str:
        if not rules:
            return ""
        blocks = []
        for context, entries in rules.items():
            body = "\n\n".join(
                "{} {{\n{}\n}}".format(
                    " ".join(selector.split()),
                    "\n".join(f"  {d}" for d in decls),
                )
                for selector, decls in entries
            )
            blocks.append(_wrap(context, body))
        inner = "\n\n".join(blocks)
        indented = "\n".join("  " + line if line.strip() else line for line in inner.splitlines())
        return (
            f"\n/* ---------------------------------------------------------------------------\n"
            f" * {title}\n"
            f" * ------------------------------------------------------------------------ */\n"
            f"{gate} {{\n{indented}\n}}\n"
        )
