"""Schema.org microdata parser.

Some sites (notably salsavida.com) embed events with HTML microdata
(`itemscope`/`itemtype`/`itemprop` attributes) instead of JSON-LD. This
module extracts schema.org/Event entries from microdata-annotated HTML.
"""
from __future__ import annotations

import logging
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..models import Event
from ._price import _to_float, format_price_range

log = logging.getLogger(__name__)


def _is_within_nested_scope(child: Tag, root: Tag) -> bool:
    """True if `child` lives inside a NESTED itemscope under `root` — i.e.
    not directly owned by `root` itself."""
    parent = child.parent
    while parent is not None and parent is not root:
        if isinstance(parent, Tag) and parent.has_attr("itemscope"):
            return True
        parent = parent.parent
    return False


def _itemprop(tag: Tag, name: str, *, find_in: Tag | None = None) -> str | None:
    """Return the value of the first itemprop=`name` descendant of `tag`,
    skipping any descendant that is inside a nested itemscope (which
    belongs to a sub-entity, not `tag` itself).

    For <meta>, the value is in `content`. For <a>, in `href`. For <img>,
    in `src`. For everything else, the visible text.
    """
    scope = find_in or tag
    for found in scope.find_all(attrs={"itemprop": name}):
        if _is_within_nested_scope(found, scope):
            continue
        if found.name == "meta":
            return found.get("content")
        if found.name == "a":
            return found.get("href") or found.get_text(strip=True) or None
        if found.name == "img":
            return found.get("src")
        return found.get_text(strip=True) or None
    return None


def _first_event_link(scope: Tag) -> str | None:
    """Fallback URL finder: return the first <a href> inside `scope` whose
    href looks like an event detail link (path contains '/event'). Skips
    anchors that live inside nested itemscopes. Helps with sites that have
    typo'd or absent itemprop=url attributes."""
    for a in scope.find_all("a", href=True):
        if _is_within_nested_scope(a, scope):
            continue
        href = a["href"]
        if "/event" in href.lower():
            return href
    # Last resort: any anchor at all in this scope.
    for a in scope.find_all("a", href=True):
        if not _is_within_nested_scope(a, scope):
            return a["href"]
    return None


def _is_event_scope(tag: Tag) -> bool:
    if not tag.has_attr("itemscope"):
        return False
    t = (tag.get("itemtype") or "").lower()
    return "schema.org/event" in t


def extract_microdata_events(html: str, base_url: str = "") -> Iterator[Event]:
    """Yield Event objects parsed from schema.org microdata in `html`.

    `base_url` is used to resolve relative URLs.
    """
    soup = BeautifulSoup(html, "lxml")
    for el in soup.find_all(attrs={"itemscope": True}):
        if not _is_event_scope(el):
            continue

        title = _itemprop(el, "name")
        if not title:
            continue

        start = _itemprop(el, "startDate")
        end = _itemprop(el, "endDate")
        url = _itemprop(el, "url") or _first_event_link(el)
        image = _itemprop(el, "image")
        description = _itemprop(el, "description")

        venue = None
        address = None
        city = None
        loc = el.find(attrs={"itemprop": "location"})
        if loc and loc.has_attr("itemscope"):
            venue = _itemprop(loc, "name")
            addr = loc.find(attrs={"itemprop": "address"})
            if addr and addr.has_attr("itemscope"):
                parts = [
                    _itemprop(addr, "streetAddress"),
                    _itemprop(addr, "addressLocality"),
                    _itemprop(addr, "addressRegion"),
                    _itemprop(addr, "postalCode"),
                ]
                address = ", ".join(p for p in parts if p) or None
                city = _itemprop(addr, "addressLocality")
            else:
                address = _itemprop(loc, "address")

        if url and base_url:
            url = urljoin(base_url, url)
        if image and base_url:
            image = urljoin(base_url, image)

        price = _extract_microdata_price(el)

        yield Event(
            title=title,
            start=start,
            end=end,
            venue=venue,
            address=address,
            city=city,
            description=description,
            image_url=image,
            source="",                    # filled in by caller
            source_url=url or base_url,
            price=price,
        )


def _extract_microdata_price(event_scope: Tag) -> str | None:
    """Look for one or more nested Offer itemscopes inside an Event scope and
    pull out price / lowPrice / highPrice."""
    amounts: list[float] = []
    currency = "USD"
    for desc in event_scope.find_all(attrs={"itemscope": True}):
        if desc is event_scope:
            continue
        t = (desc.get("itemtype") or "").lower()
        if "schema.org/offer" not in t:
            continue
        cur = _itemprop(desc, "priceCurrency")
        if cur:
            currency = cur
        for prop in ("price", "lowPrice", "highPrice"):
            v = _to_float(_itemprop(desc, prop))
            if v is not None:
                amounts.append(v)
    return format_price_range(amounts, currency)
