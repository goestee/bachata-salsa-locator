"""Geo + keyword filters that decide whether an event makes the cut."""
from __future__ import annotations

from typing import Iterable, Optional

from .models import Event


def _haystack(ev: Event, *, include_description: bool = True) -> str:
    parts = [ev.title or "", ev.venue or "", ev.address or "", ev.city or ""]
    if include_description:
        parts.append(ev.description or "")
    return " ".join(parts).lower()


def is_dance_event(ev: Event, must_match_any: Iterable[str]) -> bool:
    """At least one dance keyword must appear somewhere in the event text.
    We *do* check the description here because dance keywords legitimately
    appear in event descriptions (e.g., 'free salsa lesson at 7pm')."""
    h = _haystack(ev, include_description=True)
    return any(k.lower() in h for k in must_match_any)


def is_in_dfw(ev: Event, cities: Iterable[str],
              bbox: Optional[dict] = None) -> bool:
    """City name match against title, venue, address, and city ONLY (not
    description). Many sites' descriptions include promotional boilerplate
    that mentions other cities (or Dallas as part of their site footer);
    matching there causes false positives."""
    h = _haystack(ev, include_description=False)
    return any(c.lower() in h for c in cities)


def classify_tags(ev: Event, type_tags: dict[str, list[str]]) -> list[str]:
    """Return a list of detected event-type tags (social/workshop/lesson/...)
    plus the dance style(s) detected."""
    h = _haystack(ev)
    out: list[str] = []

    for tag, kws in type_tags.items():
        if any(k.lower() in h for k in kws):
            out.append(tag)

    for style in ("salsa", "bachata", "kizomba", "merengue", "cha cha"):
        if style in h:
            out.append(style.replace(" ", "-"))

    # De-dupe, preserve order.
    seen: set[str] = set()
    result: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result
