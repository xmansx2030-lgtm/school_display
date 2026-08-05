"""Refresh the self-hosted IBM Plex Sans Arabic faces used by the display board.

The display page must paint without reaching fonts.googleapis.com: school
networks are slow and often filtered, and a render-blocking cross-origin
stylesheet stalls the first frame for as long as that request takes to fail.
So the faces live in ``static/fonts/`` and ``static/css/fonts.css`` is generated
from them.

Run this only when the font needs updating (a new upstream release, or a weight
the design started using). It is a build-time tool, never part of a request.

    python manage.py sync_display_font
    python manage.py sync_display_font --weights 400,500,600,700 --check
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

FAMILY = "IBM Plex Sans Arabic"
FAMILY_QUERY = "IBM+Plex+Sans+Arabic"

# The board is Arabic with Latin digits and the occasional Latin word. The
# Cyrillic and extended-Latin subsets Google also ships would never be used.
SUBSETS = ("arabic", "latin")
# Every weight the display templates ask for. Browsers fetch a face only when a
# rule actually resolves to it, so listing the full range costs nothing at boot.
DEFAULT_WEIGHTS = ("200", "300", "400", "500", "600", "700")

# woff2 is served only to browsers that support it; an older UA gets ttf.
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

HEADER = """/* IBM Plex Sans Arabic — self-hosted.
 *
 * The display boots on school networks that are slow, filtered, or offline, so
 * the board must never wait on fonts.googleapis.com to paint its first frame.
 * These faces are served from our own origin, land in the Service Worker cache
 * on first boot, and carry `font-display: swap` so text is readable instantly
 * and re-renders in IBM Plex once the face arrives.
 *
 * IBM Plex Sans Arabic is licensed under the SIL Open Font License 1.1, which
 * permits redistribution from our own servers.
 *
 * GENERATED FILE — do not edit by hand.
 * Regenerate with: python manage.py sync_display_font
 *
 * The family tops out at 700. CSS asking for 800/900 gets a browser-synthesized
 * bold from the 700 face — intentional, and unchanged from the hosted version.
 */"""

FACE_TEMPLATE = """
/* {subset} */
@font-face {{
  font-family: '{family}';
  font-style: normal;
  font-weight: {weight};
  font-display: swap;
  src: url('../fonts/{file}') format('woff2');
  unicode-range: {range};
}}"""


def _fetch(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": CHROME_UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class Command(BaseCommand):
    help = "Download the display board's font faces and regenerate static/css/fonts.css."

    def add_arguments(self, parser):
        parser.add_argument(
            "--weights",
            default=",".join(DEFAULT_WEIGHTS),
            help=f"Comma-separated weights to fetch (default: {','.join(DEFAULT_WEIGHTS)}).",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=30,
            help="Per-request timeout in seconds (default: 30).",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Verify the committed files are present and valid; download nothing.",
        )

    def handle(self, *args, **options):
        base = Path(__file__).resolve().parents[3]
        fonts_dir = base / "static" / "fonts"
        css_path = base / "static" / "css" / "fonts.css"

        if options["check"]:
            return self._check(fonts_dir, css_path)

        weights = tuple(w.strip() for w in str(options["weights"]).split(",") if w.strip())
        if not weights:
            raise CommandError("--weights must list at least one weight.")

        timeout = int(options["timeout"])
        fonts_dir.mkdir(parents=True, exist_ok=True)

        url = (
            "https://fonts.googleapis.com/css2"
            f"?family={FAMILY_QUERY}:wght@{';'.join(weights)}&display=swap"
        )
        self.stdout.write(f"Fetching face list for weights {', '.join(weights)}…")
        try:
            css = _fetch(url, timeout).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the operator
            raise CommandError(f"Could not reach the font source: {exc}") from exc

        available: dict[str, dict[str, tuple[str, str]]] = {}
        for subset, block in re.findall(
            r"/\*\s*([a-z\-]+)\s*\*/\s*(@font-face\s*\{.*?\})", css, re.S
        ):
            if subset not in SUBSETS:
                continue
            weight = re.search(r"font-weight:\s*(\d+)", block)
            src = re.search(r"url\((https://[^)]+\.woff2)\)", block)
            unicode_range = re.search(r"unicode-range:\s*([^;]+);", block)
            if not (weight and src and unicode_range):
                continue
            available.setdefault(subset, {})[weight.group(1)] = (
                src.group(1),
                unicode_range.group(1).strip(),
            )

        if not available:
            raise CommandError("The font source returned no usable woff2 faces.")

        manifest: list[dict[str, str]] = []
        for subset in SUBSETS:
            for weight in weights:
                face = available.get(subset, {}).get(weight)
                if not face:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  skipped {subset} {weight} — the family does not ship it"
                        )
                    )
                    continue
                src, unicode_range = face
                filename = f"ibm-plex-sans-arabic-{weight}-{subset}.woff2"
                data = _fetch(src, timeout)
                if not data.startswith(b"wOF2"):
                    raise CommandError(f"{filename} is not a woff2 file; aborting.")
                (fonts_dir / filename).write_bytes(data)
                self.stdout.write(f"  saved {filename} ({len(data) / 1024:.1f} KB)")
                manifest.append(
                    {
                        "weight": weight,
                        "subset": subset,
                        "file": filename,
                        "range": unicode_range,
                    }
                )

        if not manifest:
            raise CommandError("No faces were downloaded; fonts.css was left untouched.")

        # Retire faces from a previous run that this run no longer covers.
        keep = {entry["file"] for entry in manifest}
        for stale in fonts_dir.glob("ibm-plex-sans-arabic-*.woff2"):
            if stale.name not in keep:
                stale.unlink()
                self.stdout.write(f"  removed superseded {stale.name}")

        # fonts.css is the only record of what was downloaded — deliberately no
        # side-car manifest, which collectstatic would publish as a static asset.
        self._write_css(css_path, manifest)

        total_kb = sum((fonts_dir / e["file"]).stat().st_size for e in manifest) / 1024
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {len(manifest)} faces ({total_kb:.1f} KB) and regenerated {css_path.name}. "
                "Run `python manage.py collectstatic` to publish them."
            )
        )

    def _write_css(self, css_path: Path, manifest: list[dict[str, str]]) -> None:
        parts = [HEADER]
        for entry in manifest:
            parts.append(
                FACE_TEMPLATE.format(
                    family=FAMILY,
                    subset=entry["subset"],
                    weight=entry["weight"],
                    file=entry["file"],
                    range=entry["range"],
                )
            )
        css_path.write_text("\n".join(parts) + "\n", encoding="utf-8")

    def _check(self, fonts_dir: Path, css_path: Path) -> None:
        """Verify every face fonts.css declares is on disk and readable.

        Worth wiring into CI: a missing woff2 does not fail the build, it just
        makes every screen silently fall back to a system font.
        """
        if not css_path.exists():
            raise CommandError(f"{css_path} is missing; run without --check to generate it.")

        referenced = re.findall(r"url\('\.\./fonts/([^']+)'\)", css_path.read_text(encoding="utf-8"))
        if not referenced:
            raise CommandError(f"{css_path} declares no @font-face sources.")

        problems: list[str] = []
        for filename in referenced:
            path = fonts_dir / filename
            if not path.exists():
                problems.append(f"missing {filename}")
            elif path.read_bytes()[:4] != b"wOF2":
                problems.append(f"{filename} is not a valid woff2 file")

        orphans = {p.name for p in fonts_dir.glob("*.woff2")} - set(referenced)
        for orphan in sorted(orphans):
            problems.append(f"{orphan} is on disk but no longer referenced by fonts.css")

        if problems:
            raise CommandError("Font assets are out of sync:\n  - " + "\n  - ".join(problems))

        self.stdout.write(
            self.style.SUCCESS(f"{len(referenced)} font faces present and referenced by fonts.css.")
        )
