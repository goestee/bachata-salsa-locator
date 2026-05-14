"""Shared JSON-LD parsing helpers.

Many event sites embed schema.org Event objects via
`<script type="application/ld+json">`. Parsing those is far more reliable
than chasing CSS selectors that change every release.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterator
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

from ..models import Event
from ._price import price_from_offers

log = logging.getLogger(__name__)


# Known "this is a stock placeholder, not a real flyer" URL fragments.
# When we see these in an Event's `image` field we treat the event as having
# no flyer rather than passing the placeholder downstream. Add new patterns
# here as we discover them — keep them specific enough not to false-match.
_PLACEHOLDER_IMAGE_FRAGMENTS = (
    "/images/fallbacks/",        # meetup.com generic group-cover squares
)


# Pattern: Eventbrite's image proxy `img.evbuc.com/<percent-encoded-cdn-url>`.
# Stripping the proxy gives us the un-cropped original on cdn.evbuc.com.
_EVENTBRITE_PROXY = re.compile(r"^https?://img\.evbuc\.com/(https?[^?]+)")

# Pattern: Meetup `classic-events` thumbnails like .../<id>/676x676.jpg.
# The /original.jpg sibling on the same dir is the un-cropped upload.
_MEETUP_CLASSIC = re.compile(
    r"^(https?://secure-content\.meetupstatic\.com/images/classic-events/\d+/)"
    r"\d+x\d+\.jpe?g$"
)

# Pattern: Meetup photo URLs like .../<dirs>/600_<id>.jpeg.
# Swapping the size prefix to `highres_` upgrades to the original aspect.
_MEETUP_PHOTOS = re.compile(
    r"^(https?://secure\.meetupstatic\.com/photos/event/(?:[^/]+/)+)"
    r"\d+_(\d+\.jpe?g)$"
)


def _upgrade_image_url(url: str) -> str:
    """Rewrite known thumbnail URLs to their full-size original.

    All transformations are pure string rewrites; we never need to fetch
    the URL just to discover a better one. Failed matches return the
    URL unchanged.
    """
    m = _EVENTBRITE_PROXY.match(url)
    if m:
        return unquote(m.group(1))

    m = _MEETUP_CLASSIC.match(url)
    if m:
        return m.group(1) + "original.jpg"

    m = _MEETUP_PHOTOS.match(url)
    if m:
        return m.group(1) + "highres_" + m.group(2)

    return url


def _normalize_image(image, fallback_url: str) -> str | None:
    """Clean up an image URL pulled from JSON-LD.

    - Returns None if the URL looks like a known stock placeholder.
    - Resolves protocol-relative and path-relative URLs against the page
      they were scraped from, so downstream consumers get something they
      can actually render.
    - Swaps known thumbnail URLs (Eventbrite img.evbuc.com proxies,
      Meetup 676x676 crops) for their un-cropped originals.
    """
    if not isinstance(image, str):
        return None
    image = image.strip()
    if not image:
        return None
    if any(frag in image for frag in _PLACEHOLDER_IMAGE_FRAGMENTS):
        return None
    if not image.startswith(("http://", "https://")):
        image = urljoin(fallback_url, image)
    return _upgrade_image_url(image)


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
    image = _normalize_image(image, fallback_url)

    price = price_from_offers(node)

    return Event(
        title=title,
        start=start,
        end=end,
        venue=venue,
        address=address,
        city=city,
        description=description if isinstance(description, str) else None,
        image_url=image,
        source=source,
        source_url=url,
        price=price,
    )
