"""Shared JSON-LD parsing helpers.

Many event sites embed schema.org Event objects via
`<script type="application/ld+json">`. Parsing those is far more reliable
than chasing CSS selectors that change every release.
"""
from __future__ import annotations

import json
import logging
from typing import Iterator

from bs4 import BeautifulSoup

from ..models import Event
from ._price import price_from_offers

log = logging.getLogger(__name__)


def _walk(node, out: list) -> None:
    """Recursively flatten anything that looks like an Event."""
    if isinstance(node, list):
        for x in node:
            _walk(x, out)
        return
    if not isinstance(node, dict):
        return

    t = node.get("@type")
    types = t if isinstance(t, list) else [t] if t else []
    if any(str(x).lower().endswith("event") for x in types):
        out.append(node)

    # Dive into common containers (ItemList, @graph, etc.).
    for key in ("@graph", "itemListElement", "item", "subEvent"):
        if key in node:
            _walk(node[key], out)


def extract_jsonld_events(html: str) -> Iterator[dict]:
    """Yield raw schema.org Event dicts from a page's JSON-LD blocks.

    Uses json.loads(strict=False) so that real-world JSON-LD with literal
    control characters (newlines/tabs in string values) still parses; we
    saw this on danceus.org's calendar page.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string if tag.string else tag.get_text()
        if not raw:
            continue
        data = None
        for attempt in (raw, raw.strip()):
            try:
                data = json.loads(attempt, strict=False)
                break
            except json.JSONDecodeError:
                continue
        if data is None:
            log.debug("JSON-LD block could not be parsed (%d chars)", len(raw))
            continue
        out: list = []
        _walk(data, out)
        yield from out


def jsonld_to_event(node: dict, source: str, fallback_url: str) -> Event | None:
    """Convert a schema.org Event dict to our Event model."""
    title = (node.get("name") or "").strip()
    if not title:
        return None

    start = node.get("startDate") or node.get("start_date")
    end = node.get("endDate") or node.get("end_date")
    url = node.get("url") or fallback_url
    description = node.get("description")

    venue = None
    address = None
    city = None
    loc = node.get("location")
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, dict):
        venue = loc.get("name")
        addr = loc.get("address")
        if isinstance(addr, list) and addr:
            addr = addr[0]
        if isinstance(addr, dict):
            parts = [
                addr.get("streetAddress"),
                addr.get("addressLocality"),
                addr.get("addressRegion"),
                addr.get("postalCode"),
            ]
            address = ", ".join(p for p in parts if p)
            city = addr.get("addressLocality")
        elif isinstance(addr, str):
            address = addr

    image = node.get("image")
    if isinstance(image, list) and image:
        image = image[0]
    if isinstance(image, dict):
        image = image.get("url")

    price = price_from_offers(node)

    return Event(
        title=title,
        start=start,
        end=end,
        venue=venue,
        address=address,
        city=city,
        description=description if isinstance(description, str) else None,
        image_url=image if isinstance(image, str) else None,
        source=source,
        source_url=url,
        price=price,
    )
