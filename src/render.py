"""Renders the persistent event store to a friendly Markdown file."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

from .models import Event

# All display times are in Central. DFW is in America/Chicago which handles
# DST automatically (CST in winter, CDT in summer).
LOCAL_TZ = ZoneInfo("America/Chicago")

# ---------------------------------------------------------------------------
# Dark-mode doodle background
# ---------------------------------------------------------------------------
# A 600x600 SVG tile of small dance/social line icons (music, hearts,
# drinks, cowboy hats, dancers, etc.) sprinkled across the canvas at
# varied positions, scales, and rotations. We bake it into a single data
# URI and use it as the body background only in dark mode — the pattern
# stays fixed while content scrolls. Stroke color is intentionally close
# to (but slightly lighter than) the dark background so the texture reads
# as subtle, not as foreground noise.

# Lucide-flavored 24x24 icon paths (no outer <svg> or <g>). The cowboy
# hat is hand-drawn for the Texas-meets-dance theme; everything else is
# stock Lucide. We previously tried boot / heel / dancer / single-note
# icons but their stroke paths read as ambiguous at small sizes, so they
# were dropped in favor of cleaner shapes.
_DOODLE_ICONS: dict[str, str] = {
    "music_double": (
        "<path d='M9 18V5l12-2v13'/>"
        "<circle cx='6' cy='18' r='3'/><circle cx='18' cy='16' r='3'/>"
    ),
    "heart": (
        "<path d='M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3"
        "c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5"
        "c0 2.3 1.5 4.05 3 5.5l7 7Z'/>"
    ),
    "star": (
        "<polygon points='12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02"
        " 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2'/>"
    ),
    "sparkles": (
        "<path d='M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582"
        "a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135"
        "a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.582"
        "a.5.5 0 0 1 0 .962L15.5 14.063a2 2 0 0 0-1.437 1.437"
        "l-1.582 6.135a.5.5 0 0 1-.963 0z'/>"
        "<path d='M20 3v4'/><path d='M22 5h-4'/>"
        "<path d='M4 17v2'/><path d='M5 18H3'/>"
    ),
    "wine": (
        "<path d='M8 22h8'/><path d='M7 10h10'/><path d='M12 15v7'/>"
        "<path d='M12 15a5 5 0 0 0 5-5c0-2-.5-4-2-8H9c-1.5 4-2 6-2 8"
        "a5 5 0 0 0 5 5Z'/>"
    ),
    "martini": (
        "<path d='M8 22h8'/><path d='M12 11v11'/>"
        "<path d='M19 3l-7 8-7-8Z'/>"
    ),
    "coffee": (
        "<path d='M10 2v2'/><path d='M14 2v2'/>"
        "<path d='M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9"
        "a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1'/>"
        "<path d='M6 2v2'/>"
    ),
    "map_pin": (
        "<path d='M20 10c0 4.993-5.539 10.193-7.399 11.799"
        "a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0'/>"
        "<circle cx='12' cy='10' r='3'/>"
    ),
    "mic": (
        "<path d='M12 19v3'/>"
        "<path d='M19 10v2a7 7 0 0 1-14 0v-2'/>"
        "<rect x='9' y='2' width='6' height='13' rx='3'/>"
    ),
    "vinyl": (
        "<circle cx='12' cy='12' r='10'/>"
        "<circle cx='12' cy='12' r='6'/>"
        "<circle cx='12' cy='12' r='3'/>"
    ),
    "calendar": (
        "<path d='M8 2v4'/><path d='M16 2v4'/>"
        "<rect width='18' height='18' x='3' y='4' rx='2'/>"
        "<path d='M3 10h18'/>"
    ),
    "smile": (
        "<circle cx='12' cy='12' r='10'/>"
        "<path d='M8 14s1.5 2 4 2 4-2 4-2'/>"
        "<line x1='9' x2='9.01' y1='9' y2='9'/>"
        "<line x1='15' x2='15.01' y1='9' y2='9'/>"
    ),
    "sound_waves": (
        "<polygon points='11 5 6 9 2 9 2 15 6 15 11 19 11 5'/>"
        "<path d='M15.54 8.46a5 5 0 0 1 0 7.07'/>"
        "<path d='M19.07 4.93a10 10 0 0 1 0 14.14'/>"
    ),
    # Cowboy hat — brim (with slight downward curl) + crown + crease.
    "cowboy_hat": (
        "<path d='M3 16C6 14 18 14 21 16C18 18 6 18 3 16Z'/>"
        "<path d='M7 16V11C7 7 9 5 12 5C15 5 17 7 17 11V16'/>"
        "<path d='M9 13C11 14 13 14 15 13'/>"
    ),
}

# (icon_name, x, y, rotation_deg, scale). x/y is the icon's top-left,
# rotation is around the icon's center (12, 12). The layout is a brick
# grid (alternating 6/5 across 6 rows) to break up obvious column
# alignment, with light jitter for an organic feel.
_DOODLE_PLACEMENTS: list[tuple[str, int, int, int, float]] = [
    # Row 1
    ("music_double", 40, 50, -12, 1.0),
    ("heart", 138, 65, 8, 1.1),
    ("cowboy_hat", 245, 55, -5, 1.0),
    ("smile", 342, 70, 12, 0.95),
    ("martini", 438, 60, -8, 1.0),
    ("martini", 540, 68, 5, 1.0),
    # Row 2 (offset 5 icons)
    ("vinyl", 88, 158, 0, 1.0),
    ("smile", 188, 145, -12, 1.0),
    ("sparkles", 290, 152, 10, 0.95),
    ("sparkles", 388, 155, -10, 0.9),
    ("map_pin", 490, 148, 8, 0.95),
    # Row 3
    ("music_double", 38, 235, 5, 1.0),
    ("smile", 142, 250, -15, 0.85),
    ("star", 240, 230, 12, 0.9),
    ("heart", 340, 245, -8, 0.95),
    ("music_double", 440, 240, 18, 1.05),
    ("wine", 540, 235, -5, 1.0),
    # Row 4 (offset)
    ("wine", 85, 340, 15, 0.9),
    ("vinyl", 190, 325, -10, 1.0),
    ("calendar", 290, 335, 8, 0.95),
    ("smile", 390, 330, -8, 0.9),
    ("cowboy_hat", 492, 340, 12, 0.95),
    # Row 5
    ("mic", 38, 425, -15, 1.0),
    ("martini", 140, 415, 10, 0.95),
    ("vinyl", 242, 420, 0, 0.85),
    ("cowboy_hat", 340, 430, -10, 0.9),
    ("sparkles", 440, 415, 12, 1.0),
    ("heart", 540, 425, -5, 1.05),
    # Row 6 (offset)
    ("calendar", 88, 515, -8, 0.9),
    ("music_double", 188, 505, 15, 1.0),
    ("coffee", 290, 520, 5, 0.95),
    ("sound_waves", 388, 510, -12, 1.0),
    ("star", 490, 505, 10, 0.85),
]


def _build_doodle_svg() -> str:
    """Assemble the dark-mode doodle tile from the icon dict + placement list."""
    parts: list[str] = [
        "<svg xmlns='http://www.w3.org/2000/svg' width='600' height='600'"
        " viewBox='0 0 600 600'>",
        "<g fill='none' stroke='#334155' stroke-width='1.4'"
        " stroke-linecap='round' stroke-linejoin='round'>",
    ]
    for name, x, y, rot, scale in _DOODLE_PLACEMENTS:
        transform = f"translate({x} {y})"
        if rot:
            transform += f" rotate({rot} 12 12)"
        if scale != 1.0:
            transform += f" scale({scale})"
        parts.append(f"<g transform='{transform}'>{_DOODLE_ICONS[name]}</g>")
    parts.append("</g></svg>")
    return "".join(parts)


_DARK_DOODLE_SVG = _build_doodle_svg()

# Pre-build the data-URI string once. We URL-encode aggressively (just keep
# a handful of common SVG punctuation characters as-is) so the result is
# safe to drop straight into a CSS `url(...)` expression regardless of how
# strict the browser is. `#` MUST be encoded because it's the URL anchor
# delimiter — without that the browser cuts the SVG off at the first hex
# color.
_DARK_DOODLE_URL = (
    'url("data:image/svg+xml;utf8,'
    + quote(_DARK_DOODLE_SVG, safe="<>=:/'.,()-+ ")
    + '")'
)

import re

# Tags we consider "dance genres" for the detail-modal "Style" pill.
# Everything else (social, lesson, workshop, free, …) lives in the tag
# pill strip and is excluded from this field.
_DANCE_GENRE_TAGS = {
    "salsa", "bachata", "kizomba", "merengue", "cumbia",
    "cha-cha", "cha cha", "zouk", "reggaeton", "mambo",
}

# Strip markdown link syntax `[text](url)` -> `text`. Captions and
# Eventbrite descriptions come back peppered with this, and we don't
# want naked URLs cluttering the modal's About section.
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# Collapse 3+ newlines down to a single blank line for tighter rendering.
_MULTI_BLANKS = re.compile(r"\n{3,}")


def _clean_description(text: str | None, max_chars: int = 800) -> str:
    """Render a description as plain prose, no markdown link clutter."""
    if not text:
        return ""
    cleaned = _MD_LINK.sub(r"\1", text)
    cleaned = _MULTI_BLANKS.sub("\n\n", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rsplit(" ", 1)[0].rstrip() + "…"
    return cleaned


_HEADER = """# DFW Bachata & Salsa Events

> Auto-generated. Last update: **{updated}**
> Sources: {sources}
> {total} upcoming events tracked. **{new} new** since last run.
> All times shown in **Central** (DFW local).

---
"""

_FOOTER = """

---
*Generated by the DFW Dance Aggregator. Each event links back to where it was
found. If something looks off, the source link is the source of truth.*
"""


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DFW Bachata &amp; Salsa Events</title>
<script>
  /* Apply saved theme BEFORE first paint so dark-mode users don't see a
     light flash. We deliberately ignore prefers-color-scheme; Chrome's
     "force dark mode" experiment used to clobber our colors when we let
     it through, so dark mode is strictly an opt-in toggle. */
  (function () {{
    try {{
      if (localStorage.getItem('theme') === 'dark') {{
        document.documentElement.setAttribute('data-theme', 'dark');
      }}
    }} catch (e) {{}}
  }})();
</script>
<style>
  /* Default to light scheme. We only flip to dark when the user toggles
     it (via data-theme="dark" set on <html>). This stops Chrome's
     "force dark mode" from tinting our light theme. */
  :root {{ color-scheme: light; }}
  html[data-theme="dark"] {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  html {{ background: #f7f7f8; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    max-width: 880px; margin: 0 auto; padding: 24px 16px;
    line-height: 1.5; color: #111827; background: #f7f7f8;
  }}
  h1 {{ margin: 0 0 4px; font-size: 28px; color: #111827; }}
  .meta {{ color: #4b5563; font-size: 14px; margin-bottom: 24px; }}
  .filters {{ margin: 16px 0 24px; display: flex; gap: 8px; flex-wrap: wrap; }}
  .filters input[type=search] {{
    padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px;
    font: inherit; flex: 1; min-width: 200px;
    background: #ffffff; color: #111827;
  }}
  .filters button {{
    padding: 8px 14px; border: 1px solid #d1d5db; border-radius: 6px;
    cursor: pointer; background: #ffffff; color: #111827; font: inherit;
  }}
  .filters button.active {{
    background: #2563eb; color: #ffffff; border-color: #2563eb;
  }}
  .new-banner {{
    background: #fff7ed; border: 1px solid #fdba74; color: #7c2d12;
    padding: 10px 14px; border-radius: 8px; margin-bottom: 18px; font-size: 14px;
  }}
  .day {{ border-top: 1px solid #e5e7eb; padding-top: 14px; margin-top: 24px; }}
  .day h2 {{ margin: 0 0 12px; font-size: 18px; color: #111827; }}
  .card {{
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px;
    padding: 14px 16px; margin-bottom: 10px;
    display: flex; gap: 14px; align-items: flex-start;
    cursor: pointer; transition: box-shadow 0.15s ease, transform 0.15s ease;
  }}
  .card:hover {{
    box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
    transform: translateY(-1px);
  }}
  .card.is-new {{ border-left: 4px solid #f97316; }}
  .card-body {{ flex: 1; min-width: 0; }}
  .card-head {{ display: flex; align-items: flex-start; gap: 10px; flex-wrap: wrap; }}
  .card-title {{
    color: #0f172a; font-weight: 600; font-size: 16px; line-height: 1.3;
    flex: 1; min-width: 0;
  }}
  .card .where {{ color: #4b5563; font-size: 14px; margin-top: 4px; }}
  .card .when {{ color: #4b5563; font-size: 14px; margin-top: 2px; }}
  .card .when .time {{ color: #2563eb; font-weight: 500; }}
  .card-thumb {{
    width: 64px; height: 64px; flex-shrink: 0; border-radius: 8px;
    background-size: cover; background-position: center; background-color: #f3f4f6;
  }}
  .tags {{ margin-top: 6px; }}
  .tag {{
    display: inline-block; background: #eef2ff; color: #3730a3;
    padding: 1px 8px; border-radius: 999px; font-size: 11px;
    margin-right: 4px; text-transform: lowercase;
  }}
  .price {{
    display: inline-block; padding: 3px 10px; border-radius: 999px;
    font-size: 12px; font-weight: 600; flex-shrink: 0;
    background: #dbeafe; color: #1e40af;
    border: 1px solid #bfdbfe;
  }}
  .price.free {{ background: #dcfce7; color: #166534; border-color: #bbf7d0; }}
  /* Soft neutral pill for "Varies" — meant to read as "check the link" without
     drawing as much attention as a confirmed price/free badge. */
  .price.varies {{ background: #f3f4f6; color: #6b7280; border-color: #e5e7eb;
                   font-weight: 500; }}
  .src {{ color: #6b7280; font-size: 12px; }}
  .empty {{ color: #6b7280; padding: 40px; text-align: center; }}

  /* ---------- Event detail modal ------------------------------------- */
  .modal-overlay {{
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(15, 23, 42, 0.55);
    display: flex; align-items: center; justify-content: center;
    padding: 16px;
    opacity: 0; pointer-events: none; transition: opacity 0.15s ease;
  }}
  .modal-overlay.open {{ opacity: 1; pointer-events: auto; }}
  .modal-card {{
    background: #ffffff; color: #111827;
    width: 100%; max-width: 440px; max-height: 92vh;
    border-radius: 16px; overflow: hidden;
    display: flex; flex-direction: column;
    box-shadow: 0 20px 50px rgba(15, 23, 42, 0.25);
    transform: translateY(8px); transition: transform 0.15s ease;
  }}
  .modal-overlay.open .modal-card {{ transform: translateY(0); }}
  .modal-header {{
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; padding: 16px 18px;
    border-bottom: 1px solid #f1f5f9;
  }}
  .modal-title {{ font-size: 18px; font-weight: 700; color: #0f172a;
                  margin: 0; line-height: 1.3; }}
  .modal-close {{
    background: transparent; border: 0; font-size: 24px;
    line-height: 1; color: #94a3b8; cursor: pointer; padding: 4px 8px;
    border-radius: 6px;
  }}
  .modal-close:hover {{ background: #f1f5f9; color: #0f172a; }}
  .modal-body {{ overflow-y: auto; padding: 0 18px 8px; }}
  .modal-image-wrap {{
    width: 100%; background: #f1f5f9; border-radius: 12px;
    overflow: hidden; margin: 8px 0 16px;
    display: block;
  }}
  .modal-image {{
    width: 100%; height: auto; display: block;
  }}
  .modal-image-empty {{
    width: 100%; padding: 64px 12px; text-align: center;
    color: #94a3b8; font-size: 13px; font-style: italic;
    background: #f1f5f9;
  }}
  .modal-venue {{ margin-bottom: 4px; }}
  .modal-venue-name {{ font-weight: 700; color: #0f172a; font-size: 16px;
                        letter-spacing: 0.01em; }}
  .modal-address {{ color: #64748b; font-size: 13px; margin-top: 4px;
                    line-height: 1.45; }}
  .modal-meta {{
    margin: 14px 0 0; padding: 0;
  }}
  .modal-meta .row {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 0; border-bottom: 1px solid #f1f5f9;
    gap: 12px; font-size: 14px;
  }}
  .modal-meta .row:last-child {{ border-bottom: 0; }}
  .modal-meta .row .label {{
    color: #64748b; display: flex; align-items: center; gap: 10px;
    font-weight: 500;
  }}
  .modal-meta .row .label .icon {{
    display: inline-flex; width: 18px; justify-content: center;
    font-size: 16px; color: #64748b;
  }}
  .modal-meta .row .value {{ color: #0f172a; font-weight: 600; text-align: right; }}
  .modal-meta .row .value-pill {{
    display: inline-block; padding: 4px 12px; border-radius: 999px;
    background: #f1f5f9; color: #0f172a; font-size: 13px;
    font-weight: 500;
  }}
  .modal-about {{ padding-top: 14px; }}
  .modal-about-label {{
    color: #64748b; font-size: 13px; font-weight: 500;
    margin-bottom: 6px;
  }}
  .modal-about-body {{
    color: #0f172a; font-size: 14px; line-height: 1.55;
    white-space: pre-wrap; word-break: break-word;
  }}
  .modal-actions {{
    padding: 14px 18px 18px; border-top: 1px solid #f1f5f9;
    display: flex; flex-direction: column; gap: 10px;
  }}
  .modal-btn {{
    display: flex; align-items: center; justify-content: center; gap: 10px;
    padding: 14px 16px; border-radius: 999px; text-decoration: none;
    font-weight: 600; font-size: 15px; line-height: 1;
    transition: opacity 0.15s ease, background 0.15s ease;
  }}
  .modal-btn:hover {{ opacity: 0.92; }}
  .modal-btn svg {{ width: 18px; height: 18px; flex-shrink: 0; }}
  .modal-btn-primary {{ background: #0f172a; color: #ffffff; }}
  .modal-btn-secondary {{
    background: #f1f5f9; color: #0f172a; border: 0;
  }}
  .modal-btn-secondary:hover {{ background: #e2e8f0; opacity: 1; }}
  body.modal-open {{ overflow: hidden; }}
  @media (max-width: 480px) {{
    .modal-overlay {{ padding: 0; align-items: flex-end; }}
    .modal-card {{ max-height: 92vh; max-width: 100%;
                   border-radius: 16px 16px 0 0; }}
  }}

  /* ---------- Theme toggle button (sits next to the H1) -------------- */
  .page-head {{
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; margin-bottom: 4px;
  }}
  .page-head h1 {{ margin: 0; }}
  /* The toggle previews the destination mode: in light mode it looks
     dark (click to go dark); in dark mode it looks light (click to go
     light). So we just paint it in the *opposite* palette to whatever
     theme is currently active. */
  .theme-toggle {{
    background: #0f172a; border: 1px solid #0f172a; border-radius: 999px;
    padding: 6px 12px; cursor: pointer; font: inherit; font-size: 13px;
    display: inline-flex; align-items: center; gap: 6px; color: #f1f5f9;
    transition: background 0.15s ease, border-color 0.15s ease,
                color 0.15s ease;
    flex-shrink: 0;
  }}
  .theme-toggle:hover {{ background: #1e293b; border-color: #1e293b; }}
  .theme-toggle svg {{ width: 16px; height: 16px; }}
  .theme-toggle .icon-sun {{ display: none; }}
  .theme-toggle .icon-moon {{ display: inline-flex; }}

  /* ---------- Dark mode skin (user-toggle, not OS-auto) -------------- *
     Triggered by <html data-theme="dark">. We override colors on the
     existing classes rather than refactoring everything to CSS variables
     so the diff is contained and the light theme is unchanged.
   * ------------------------------------------------------------------- */
  html[data-theme="dark"] {{ background: #0b1220; }}
  html[data-theme="dark"] body {{
    background-color: #0b1220;
    background-image: {doodle_url};
    background-size: 600px 600px;
    background-repeat: repeat;
    /* Stay anchored to the viewport so the doodle doesn't scroll with
       content — keeps the texture feel from looking like a stamped
       wallpaper that moves around. */
    background-attachment: fixed;
    color: #e2e8f0;
  }}
  html[data-theme="dark"] h1 {{ color: #f1f5f9; }}
  html[data-theme="dark"] .meta {{ color: #94a3b8; }}
  html[data-theme="dark"] .filters input[type=search] {{
    background: #111b2e; color: #e2e8f0; border-color: #334155;
  }}
  html[data-theme="dark"] .filters input[type=search]::placeholder {{
    color: #64748b;
  }}
  html[data-theme="dark"] .filters button {{
    background: #111b2e; color: #e2e8f0; border-color: #334155;
  }}
  html[data-theme="dark"] .filters button.active {{
    background: #3b82f6; color: #ffffff; border-color: #3b82f6;
  }}
  html[data-theme="dark"] .new-banner {{
    background: #27200f; border-color: #92400e; color: #fed7aa;
  }}
  html[data-theme="dark"] .day {{ border-top-color: #1f2937; }}
  html[data-theme="dark"] .day h2 {{ color: #f1f5f9; }}
  html[data-theme="dark"] .card {{
    background: #111b2e; border-color: #1f2937;
  }}
  html[data-theme="dark"] .card:hover {{
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.45);
  }}
  html[data-theme="dark"] .card.is-new {{ border-left-color: #fb923c; }}
  html[data-theme="dark"] .card-title {{ color: #f1f5f9; }}
  html[data-theme="dark"] .card .where,
  html[data-theme="dark"] .card .when {{ color: #94a3b8; }}
  html[data-theme="dark"] .card .when .time {{ color: #60a5fa; }}
  html[data-theme="dark"] .card-thumb {{ background-color: #1f2937; }}
  html[data-theme="dark"] .tag {{ background: #1e293b; color: #a5b4fc; }}
  html[data-theme="dark"] .price {{
    background: #1e3a8a; color: #bfdbfe; border-color: #1e40af;
  }}
  html[data-theme="dark"] .price.free {{
    background: #14532d; color: #86efac; border-color: #166534;
  }}
  html[data-theme="dark"] .price.varies {{
    background: #1f2937; color: #9ca3af; border-color: #374151;
  }}
  html[data-theme="dark"] .src,
  html[data-theme="dark"] .empty {{ color: #94a3b8; }}

  /* Modal overrides */
  html[data-theme="dark"] .modal-overlay {{ background: rgba(0, 0, 0, 0.7); }}
  html[data-theme="dark"] .modal-card {{
    background: #111b2e; color: #e2e8f0;
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
  }}
  html[data-theme="dark"] .modal-header {{ border-bottom-color: #1f2937; }}
  html[data-theme="dark"] .modal-title {{ color: #f1f5f9; }}
  html[data-theme="dark"] .modal-close {{ color: #64748b; }}
  html[data-theme="dark"] .modal-close:hover {{
    background: #1e293b; color: #f1f5f9;
  }}
  html[data-theme="dark"] .modal-image-wrap {{ background: #1e293b; }}
  html[data-theme="dark"] .modal-image-empty {{
    background: #1e293b; color: #64748b;
  }}
  html[data-theme="dark"] .modal-venue-name {{ color: #f1f5f9; }}
  html[data-theme="dark"] .modal-address {{ color: #94a3b8; }}
  html[data-theme="dark"] .modal-meta .row {{ border-bottom-color: #1f2937; }}
  html[data-theme="dark"] .modal-meta .row .label,
  html[data-theme="dark"] .modal-meta .row .label .icon {{ color: #94a3b8; }}
  html[data-theme="dark"] .modal-meta .row .value {{ color: #f1f5f9; }}
  html[data-theme="dark"] .modal-meta .row .value-pill {{
    background: #1e293b; color: #e2e8f0;
  }}
  html[data-theme="dark"] .modal-about-label {{ color: #94a3b8; }}
  html[data-theme="dark"] .modal-about-body {{ color: #e2e8f0; }}
  html[data-theme="dark"] .modal-actions {{ border-top-color: #1f2937; }}
  html[data-theme="dark"] .modal-btn-primary {{
    background: #f1f5f9; color: #0f172a;
  }}
  html[data-theme="dark"] .modal-btn-secondary {{
    background: #1e293b; color: #e2e8f0;
  }}
  html[data-theme="dark"] .modal-btn-secondary:hover {{ background: #334155; }}

  /* In dark mode the button flips to a light-painted pill so it now
     previews the light theme you'd jump to on click. */
  html[data-theme="dark"] .theme-toggle {{
    background: #f1f5f9; border-color: #f1f5f9; color: #0f172a;
  }}
  html[data-theme="dark"] .theme-toggle:hover {{
    background: #ffffff; border-color: #ffffff;
  }}
  html[data-theme="dark"] .theme-toggle .icon-sun {{ display: inline-flex; }}
  html[data-theme="dark"] .theme-toggle .icon-moon {{ display: none; }}
</style>
</head>
<body>
<div class="page-head">
  <h1>DFW Bachata &amp; Salsa Events</h1>
  <button type="button" class="theme-toggle" id="theme-toggle"
          aria-label="Toggle dark mode" aria-pressed="false">
    <!-- Moon icon shows in light mode (click to go dark). -->
    <svg class="icon-moon" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2" stroke-linecap="round"
         stroke-linejoin="round" aria-hidden="true">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z"/>
    </svg>
    <!-- Sun icon shows in dark mode (click to go light). -->
    <svg class="icon-sun" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2" stroke-linecap="round"
         stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4"/>
      <path d="M12 2v2"/><path d="M12 20v2"/>
      <path d="m4.93 4.93 1.41 1.41"/>
      <path d="m17.66 17.66 1.41 1.41"/>
      <path d="M2 12h2"/><path d="M20 12h2"/>
      <path d="m6.34 17.66-1.41 1.41"/>
      <path d="m19.07 4.93-1.41 1.41"/>
    </svg>
    <span class="theme-toggle-label" id="theme-toggle-label">Dark</span>
  </button>
</div>
<div class="meta">
  Last update: <strong>{updated}</strong> &middot; {total} upcoming events &middot;
  Sources: {sources} &middot; All times Central (DFW local)
</div>

{new_banner}

<div class="filters">
  <input type="search" id="q" placeholder="Filter by venue, title, source...">
  <button data-tag="" class="active">All</button>
  <button data-tag="salsa">Salsa</button>
  <button data-tag="bachata">Bachata</button>
  <button data-tag="social">Socials</button>
  <button data-tag="lesson">Lessons</button>
  <button data-tag="workshop">Workshops</button>
  <button data-tag="free">Free only</button>
</div>

<div id="events">
{body}
</div>

<!-- Event detail modal. Populated by JS on card click. -->
<div class="modal-overlay" id="event-modal" aria-hidden="true">
  <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <div class="modal-header">
      <h2 class="modal-title" id="modal-title"></h2>
      <button class="modal-close" type="button" aria-label="Close">&times;</button>
    </div>
    <div class="modal-body">
      <div class="modal-image-wrap" id="modal-image-wrap"></div>
      <div class="modal-venue">
        <div class="modal-venue-name" id="modal-venue-name"></div>
        <div class="modal-address" id="modal-address"></div>
      </div>
      <div class="modal-meta">
        <div class="row">
          <span class="label"><span class="icon">&#128197;</span>Date</span>
          <span class="value" id="modal-date"></span>
        </div>
        <div class="row">
          <span class="label"><span class="icon">&#128336;</span>Time</span>
          <span class="value" id="modal-time"></span>
        </div>
        <div class="row">
          <span class="label"><span class="icon">$</span>Cover</span>
          <span class="value" id="modal-cover"></span>
        </div>
        <div class="row" id="modal-row-lesson">
          <span class="label"><span class="icon">&#127891;</span>Lesson</span>
          <span class="value" id="modal-lesson"></span>
        </div>
        <div class="row" id="modal-row-style">
          <span class="label"><span class="icon">&#10024;</span>Style</span>
          <span class="value"><span class="value-pill" id="modal-style"></span></span>
        </div>
      </div>
      <div class="modal-about" id="modal-about-section">
        <div class="modal-about-label">About</div>
        <div class="modal-about-body" id="modal-about-body"></div>
      </div>
    </div>
    <div class="modal-actions">
      <a class="modal-btn modal-btn-secondary" id="modal-source-btn"
         target="_blank" rel="noopener noreferrer">
        <span id="modal-source-icon" aria-hidden="true"></span>
        <span id="modal-source-label">View source</span>
      </a>
      <a class="modal-btn modal-btn-primary" id="modal-map-btn"
         target="_blank" rel="noopener noreferrer">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
             aria-hidden="true">
          <path d="M20 10c0 7-8 13-8 13s-8-6-8-13a8 8 0 0 1 16 0Z"/>
          <circle cx="12" cy="10" r="3"/>
        </svg>
        <span>Open in Maps</span>
      </a>
    </div>
  </div>
</div>

<script>
  /* ---------- Theme toggle (light <-> dark) ----------------------- */
  const themeBtn = document.getElementById('theme-toggle');
  const themeLabel = document.getElementById('theme-toggle-label');
  function applyTheme(theme) {{
    if (theme === 'dark') {{
      document.documentElement.setAttribute('data-theme', 'dark');
      themeBtn.setAttribute('aria-pressed', 'true');
      themeLabel.textContent = 'Light';
    }} else {{
      document.documentElement.removeAttribute('data-theme');
      themeBtn.setAttribute('aria-pressed', 'false');
      themeLabel.textContent = 'Dark';
    }}
  }}
  /* Sync label with whatever the head-script already applied. */
  applyTheme(document.documentElement.getAttribute('data-theme') === 'dark'
             ? 'dark' : 'light');
  themeBtn.addEventListener('click', () => {{
    const next = document.documentElement.getAttribute('data-theme') === 'dark'
                 ? 'light' : 'dark';
    applyTheme(next);
    try {{ localStorage.setItem('theme', next); }} catch (e) {{}}
  }});

  /* ---------- Filtering / search (unchanged behavior) -------------- */
  const q = document.getElementById('q');
  const buttons = document.querySelectorAll('.filters button');
  let activeTag = '';
  function apply() {{
    const term = q.value.trim().toLowerCase();
    document.querySelectorAll('.card').forEach(card => {{
      const matchesText = !term || card.dataset.search.includes(term);
      const matchesTag = !activeTag || (card.dataset.tags || '').split(' ').includes(activeTag);
      card.style.display = (matchesText && matchesTag) ? '' : 'none';
    }});
    document.querySelectorAll('.day').forEach(day => {{
      const visible = day.querySelectorAll('.card:not([style*="display: none"])').length;
      day.style.display = visible ? '' : 'none';
    }});
  }}
  q.addEventListener('input', apply);
  buttons.forEach(b => b.addEventListener('click', () => {{
    buttons.forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    activeTag = b.dataset.tag;
    apply();
  }}));

  /* ---------- Event detail modal ----------------------------------- */
  const modal = document.getElementById('event-modal');
  const elTitle = document.getElementById('modal-title');
  const elImgWrap = document.getElementById('modal-image-wrap');
  const elVenue = document.getElementById('modal-venue-name');
  const elAddr = document.getElementById('modal-address');
  const elDate = document.getElementById('modal-date');
  const elTime = document.getElementById('modal-time');
  const elCover = document.getElementById('modal-cover');
  const elLesson = document.getElementById('modal-lesson');
  const elStyle = document.getElementById('modal-style');
  const elRowLesson = document.getElementById('modal-row-lesson');
  const elRowStyle = document.getElementById('modal-row-style');
  const elAboutSection = document.getElementById('modal-about-section');
  const elAboutBody = document.getElementById('modal-about-body');
  const elMapBtn = document.getElementById('modal-map-btn');
  const elSrcBtn = document.getElementById('modal-source-btn');
  const elSrcLabel = document.getElementById('modal-source-label');
  const elSrcIcon = document.getElementById('modal-source-icon');

  /* Stroked line icons (Lucide-flavored) — kept inline to avoid an extra
     network request. `currentColor` makes them inherit button text color. */
  const ICON_INSTAGRAM = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="20" height="20" x="2" y="2" rx="5" ry="5"/><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37Z"/><line x1="17.5" x2="17.51" y1="6.5" y2="6.5"/></svg>';
  const ICON_TICKET = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M13 5v2"/><path d="M13 17v2"/><path d="M13 11v2"/></svg>';
  const ICON_USERS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>';
  const ICON_LINK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" x2="21" y1="14" y2="3"/></svg>';

  /* Friendly labels + per-source icons. Keep keys lowercase. */
  const SOURCE_META = {{
    instagram:    {{ label: 'Instagram',    icon: ICON_INSTAGRAM }},
    eventbrite:   {{ label: 'Eventbrite',   icon: ICON_TICKET    }},
    meetup:       {{ label: 'Meetup',       icon: ICON_USERS     }},
    danceus:      {{ label: 'DanceUS',      icon: ICON_LINK      }},
    golatindance: {{ label: 'GoLatinDance', icon: ICON_LINK      }},
    salsavida:    {{ label: 'SalsaVida',    icon: ICON_LINK      }},
    studio22:     {{ label: 'Studio 22',    icon: ICON_LINK      }},
  }};

  function openModal(card) {{
    const d = card.dataset;
    elTitle.textContent = d.title || 'Event';

    // Image area: real flyer if we have one, otherwise a subtle empty state.
    if (d.image) {{
      const img = new Image();
      img.className = 'modal-image';
      img.alt = '';
      img.src = d.image;
      elImgWrap.innerHTML = '';
      elImgWrap.appendChild(img);
      elImgWrap.style.display = '';
    }} else {{
      elImgWrap.innerHTML = '<div class="modal-image-empty">No flyer was posted for this event</div>';
      elImgWrap.style.display = '';
    }}

    elVenue.textContent = d.venue || '';
    elVenue.style.display = d.venue ? '' : 'none';
    elAddr.textContent = d.address || '';
    elAddr.style.display = d.address ? '' : 'none';

    elDate.textContent = d.date || '—';
    elTime.textContent = d.time || '—';
    elCover.textContent = d.cover || 'Varies';

    /* Lesson row: only show when this event includes a lesson. */
    if (d.hasLesson === '1') {{
      elLesson.textContent = 'Dance lesson included';
      elRowLesson.style.display = '';
    }} else {{
      elRowLesson.style.display = 'none';
    }}

    /* Style pill: only show when we know the dance genre(s). */
    if (d.style) {{
      elStyle.textContent = d.style;
      elRowStyle.style.display = '';
    }} else {{
      elRowStyle.style.display = 'none';
    }}

    /* About section: hide entirely if no description. */
    if (d.about) {{
      elAboutBody.textContent = d.about;
      elAboutSection.style.display = '';
    }} else {{
      elAboutSection.style.display = 'none';
    }}

    /* Map button: link to Google Maps using whatever location info we have. */
    const mapQuery = [d.venue, d.address].filter(Boolean).join(', ');
    if (mapQuery) {{
      elMapBtn.href = 'https://www.google.com/maps/search/?api=1&query='
                       + encodeURIComponent(mapQuery);
      elMapBtn.style.display = '';
    }} else {{
      elMapBtn.style.display = 'none';
    }}

    /* Source button: label + line-icon based on which scraper found the event. */
    if (d.sourceUrl) {{
      const meta = SOURCE_META[(d.source || '').toLowerCase()]
                     || {{ label: 'source', icon: ICON_LINK }};
      elSrcBtn.href = d.sourceUrl;
      elSrcIcon.innerHTML = meta.icon;
      elSrcLabel.textContent = meta.label;
      elSrcBtn.style.display = '';
    }} else {{
      elSrcBtn.style.display = 'none';
    }}

    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
  }}

  function closeModal() {{
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
  }}

  document.querySelectorAll('.card').forEach(card => {{
    card.addEventListener('click', e => {{
      if (e.target.closest('a')) return;  /* don't intercept inner links */
      openModal(card);
    }});
  }});

  modal.addEventListener('click', e => {{
    if (e.target === modal) closeModal();
  }});
  modal.querySelector('.modal-close').addEventListener('click', closeModal);
  document.addEventListener('keydown', e => {{
    if (e.key === 'Escape' && modal.classList.contains('open')) closeModal();
  }});
</script>
</body>
</html>
"""


def render_html(
    events: Iterable[Event],
    out_path: Path,
    horizon_days: int = 90,
    new_window_hours: int = 26,
) -> None:
    """Write a self-contained interactive HTML page for browser viewing."""
    today = datetime.now(LOCAL_TZ).date()
    horizon = today + timedelta(days=horizon_days)
    new_since = datetime.now(timezone.utc) - timedelta(hours=new_window_hours)

    upcoming: list[Event] = []
    for ev in events:
        d = _local(_parse_dt(ev.start))
        if d is None or d.date() < today or d.date() > horizon:
            continue
        upcoming.append(ev)
    upcoming.sort(key=lambda e: _local(_parse_dt(e.start)) or _FAR_FUTURE)

    new_count = sum(1 for e in upcoming if _is_recent(e.first_seen_at, new_since))

    by_day: dict[date, list[Event]] = defaultdict(list)
    for ev in upcoming:
        d = _local(_parse_dt(ev.start))
        if d is not None:
            by_day[d.date()].append(ev)

    parts: list[str] = []
    if not by_day:
        parts.append('<div class="empty">No upcoming events found yet.</div>')
    for day in sorted(by_day.keys()):
        parts.append(f'<section class="day"><h2>{_fmt_day_header(day)}</h2>')
        for ev in by_day[day]:
            is_new = _is_recent(ev.first_seen_at, new_since)
            tags_html = "".join(
                f'<span class="tag">{_html_escape(t)}</span>'
                for t in (ev.tags or [])
            )
            where_bits = []
            if ev.venue:
                where_bits.append(_html_escape(ev.venue))
            if ev.city and (not ev.venue or ev.city.lower() not in ev.venue.lower()):
                where_bits.append(_html_escape(ev.city))
            where_html = (f'<div class="where">@ {", ".join(where_bits)}</div>'
                          if where_bits else "")
            tag_set = list(ev.tags or [])
            if ev.price:
                is_free = ev.price.lower().startswith("free")
                price_html = (
                    f'<span class="price{" free" if is_free else ""}">'
                    f'{_html_escape(ev.price)}</span>'
                )
                if is_free:
                    tag_set.append("free")
            else:
                # No structured price was extracted. Show a neutral "Varies"
                # pill so the user knows pricing exists but we couldn't pin
                # it down — they should click through to confirm.
                price_html = '<span class="price varies">Varies</span>'
            search_blob = " ".join(filter(None, [
                ev.title, ev.venue, ev.city, ev.source, ev.price,
                " ".join(tag_set),
            ])).lower()

            # Optional thumbnail (event flyer image, when available).
            thumb_html = ""
            if ev.image_url:
                thumb_html = (
                    f'<div class="card-thumb" '
                    f'style="background-image:url(\'{_html_escape(ev.image_url, attr=True)}\')">'
                    f'</div>'
                )

            # Inline "Day, Date • Time" line. Day comes from start_dt date.
            start_dt = _local(_parse_dt(ev.start))
            when_bits = []
            if start_dt:
                when_bits.append(_fmt_day_inline(start_dt.date()))
            when_bits.append(f'<span class="time">{_html_escape(_fmt_time(ev))}</span>')
            when_html = ' • '.join(when_bits)

            # Data the modal needs when this card is clicked. We stash
            # everything on data-* attributes so we don't have to ship a
            # separate JSON blob or look up rows by id.
            modal_date = _fmt_day_inline(start_dt.date()) if start_dt else ""
            modal_time = _fmt_time(ev)
            modal_cover = ev.price or "Varies"
            tags_lower = {t.lower() for t in (ev.tags or [])}
            modal_style = " & ".join(
                t.title() for t in (ev.tags or [])
                if t.lower() in _DANCE_GENRE_TAGS
            )
            modal_has_lesson = "1" if "lesson" in tags_lower else "0"
            modal_about = _clean_description(ev.description)

            parts.append(
                f'<div class="card{" is-new" if is_new else ""}" '
                f'data-search="{_html_escape(search_blob, attr=True)}" '
                f'data-tags="{_html_escape(" ".join(tag_set), attr=True)}" '
                f'data-title="{_html_escape(ev.title, attr=True)}" '
                f'data-image="{_html_escape(ev.image_url or "", attr=True)}" '
                f'data-venue="{_html_escape(ev.venue or "", attr=True)}" '
                f'data-address="{_html_escape(ev.address or "", attr=True)}" '
                f'data-date="{_html_escape(modal_date, attr=True)}" '
                f'data-time="{_html_escape(modal_time, attr=True)}" '
                f'data-cover="{_html_escape(modal_cover, attr=True)}" '
                f'data-style="{_html_escape(modal_style, attr=True)}" '
                f'data-has-lesson="{modal_has_lesson}" '
                f'data-about="{_html_escape(modal_about, attr=True)}" '
                f'data-source="{_html_escape(ev.source, attr=True)}" '
                f'data-source-url="{_html_escape(ev.source_url or "", attr=True)}">'
                f'{thumb_html}'
                f'<div class="card-body">'
                f'<div class="card-head">'
                f'<span class="card-title">{_html_escape(ev.title)}</span>'
                f'{price_html}'
                f'</div>'
                f'{where_html}'
                f'<div class="when">{when_html}</div>'
                f'<div class="tags">{tags_html} <span class="src">via {_html_escape(ev.source)}</span></div>'
                f'</div>'   # /card-body
                f'</div>'   # /card
            )
        parts.append("</section>")

    sources = sorted({e.source for e in upcoming}) or ["—"]
    new_banner = (
        f'<div class="new-banner"><strong>{new_count} new event'
        f'{"s" if new_count != 1 else ""}</strong> since the last run.</div>'
        if new_count else ""
    )

    html = _HTML_TEMPLATE.format(
        updated=datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %I:%M %p %Z"),
        total=len(upcoming),
        sources=", ".join(sources),
        new_banner=new_banner,
        body="\n".join(parts),
        doodle_url=_DARK_DOODLE_URL,
    )
    out_path.write_text(html, encoding="utf-8")


def _html_escape(s: str | None, *, attr: bool = False) -> str:
    if not s:
        return ""
    s = (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    if attr:
        s = s.replace('"', "&quot;")
    return s


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return dateparser.parse(s)
    except (ValueError, TypeError):
        return None


def _aware(dt: datetime | None) -> datetime | None:
    """Force tz-awareness so sorts/compares don't blow up when some
    sources return naive datetimes and others return UTC-aware. Naive
    datetimes from iCal `DTSTART;VALUE=DATE` are assumed to be local
    Central time (DFW), which is the most useful default for this app."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_TZ)
    return dt


def _local(dt: datetime | None) -> datetime | None:
    """Convert to America/Chicago for display."""
    a = _aware(dt)
    return a.astimezone(LOCAL_TZ) if a else None


_FAR_FUTURE = datetime(9999, 1, 1, tzinfo=timezone.utc)


def _fmt_clock(dt: datetime) -> str:
    """7:00 PM (Windows strftime doesn't support %-I, so lstrip the zero)."""
    return dt.strftime("%I:%M %p").lstrip("0")


def _fmt_time(ev: Event) -> str:
    """Render start [- end] in local time. Examples:
       '7:00 PM', '7:00 PM – 11:00 PM', '11:00 PM – 2:00 AM', 'all day'."""
    start = _local(_parse_dt(ev.start))
    if not start:
        return "_TBD_"
    if start.hour == 0 and start.minute == 0:
        return "all day"
    end = _local(_parse_dt(ev.end))
    if not end or end <= start:
        return _fmt_clock(start)
    # Drop the date suffix even when the event crosses midnight; the day
    # heading already provides date context.
    return f"{_fmt_clock(start)} – {_fmt_clock(end)}"


def _fmt_day_header(d: date) -> str:
    return d.strftime("%A, %B %d, %Y").replace(" 0", " ")


def _fmt_day_inline(d: date) -> str:
    """Compact in-card date: 'Wed, May 6, 2026'."""
    return d.strftime("%a, %b %d, %Y").replace(" 0", " ")


def _is_recent(iso_ts: str | None, since: datetime) -> bool:
    dt = _aware(_parse_dt(iso_ts))
    if dt is None:
        return False
    return dt >= since


def render_markdown(
    events: Iterable[Event],
    out_path: Path,
    horizon_days: int = 90,
    new_window_hours: int = 26,   # a hair > 24h so daily cron always catches its own deltas
) -> None:
    """Write a single Markdown file grouped by date."""
    # All boundaries computed in local DFW time so a Friday 11pm event
    # doesn't get rolled over to "Saturday" by UTC.
    today = datetime.now(LOCAL_TZ).date()
    horizon = today + timedelta(days=horizon_days)
    new_since = datetime.now(timezone.utc) - timedelta(hours=new_window_hours)

    # Filter to upcoming, in-horizon events (local time).
    upcoming: list[Event] = []
    for ev in events:
        d = _local(_parse_dt(ev.start))
        if d is None:
            continue
        d_date = d.date()
        if d_date < today or d_date > horizon:
            continue
        upcoming.append(ev)

    upcoming.sort(key=lambda e: _local(_parse_dt(e.start)) or _FAR_FUTURE)

    # Group by day (local).
    by_day: dict[date, list[Event]] = defaultdict(list)
    for ev in upcoming:
        d = _local(_parse_dt(ev.start))
        if d is None:
            continue
        by_day[d.date()].append(ev)

    new_events = [e for e in upcoming if _is_recent(e.first_seen_at, new_since)]
    sources = sorted({e.source for e in upcoming}) or ["—"]

    parts: list[str] = []
    parts.append(_HEADER.format(
        updated=datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %I:%M %p %Z"),
        sources=", ".join(sources),
        total=len(upcoming),
        new=len(new_events),
    ))

    if new_events:
        parts.append("## New since last run\n")
        for ev in new_events[:25]:
            parts.append(_render_event_line(ev, prefix="- "))
        parts.append("\n---\n")

    if not by_day:
        parts.append("\n_No upcoming events found yet. The aggregator will populate this on its next run._\n")
    else:
        parts.append("## Upcoming\n")
        for day in sorted(by_day.keys()):
            parts.append(f"\n### {_fmt_day_header(day)}\n")
            for ev in by_day[day]:
                parts.append(_render_event_line(ev, prefix="- "))

    parts.append(_FOOTER)
    out_path.write_text("\n".join(parts), encoding="utf-8")


def _render_event_line(ev: Event, prefix: str = "") -> str:
    """Stacked multi-line markdown entry, matching the visual hierarchy
    used by the latin-dance-guide reference app:
        - **[Title](url)** **_Free_**
          Venue · City
          Wed, May 6, 2026 • 7:00 PM – 11:00 PM
          `social` `salsa` _via salsavida_

    (Trailing two-space line-breaks render as <br> in markdown.)"""
    title_line_bits: list[str] = [f"**[{ev.title.strip()}]({ev.source_url})**"]
    title_line_bits.append(f"**_{ev.price or 'Varies'}_**")

    where_bits: list[str] = []
    if ev.venue:
        where_bits.append(ev.venue)
    if ev.city and (not ev.venue or ev.city.lower() not in ev.venue.lower()):
        where_bits.append(ev.city)
    where_line = " · ".join(where_bits) if where_bits else ""

    when_line_bits: list[str] = []
    start_dt = _local(_parse_dt(ev.start))
    if start_dt:
        when_line_bits.append(_fmt_day_inline(start_dt.date()))
    when_line_bits.append(_fmt_time(ev))
    when_line = " • ".join(when_line_bits)

    tags_line_bits: list[str] = []
    tag_str = " ".join(f"`{t}`" for t in (ev.tags or []))
    if tag_str:
        tags_line_bits.append(tag_str)
    tags_line_bits.append(f"_via {ev.source}_")
    tags_line = " ".join(tags_line_bits)

    # Build final block. Two trailing spaces force a markdown <br>.
    indent = " " * len(prefix.rstrip())   # keep continuation lines aligned
    lines: list[str] = [prefix + " ".join(title_line_bits) + "  "]
    if where_line:
        lines.append(f"{indent}  {where_line}  ")
    lines.append(f"{indent}  {when_line}  ")
    lines.append(f"{indent}  {tags_line}")
    return "\n".join(lines)
