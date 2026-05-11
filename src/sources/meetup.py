"""Meetup source — public website search (their API is now Pro-only).

We hit the public search SSR page and extract JSON-LD events. Meetup's
official GraphQL API requires a paid Meetup Pro subscription as of 2025.

Caveat: the search-page JSON-LD only includes Meetup's generic
group-cover placeholder in the `image` field — not the event's actual
flyer. To surface real flyers we follow up with a per-event GET on the
detail page (where both JSON-LD and og:image carry the real upload).
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .base import BaseSource
from ._jsonld import extract_jsonld_events, jsonld_to_event, _normalize_image
from ..models import Event

log = logging.getLogger(__name__)


class MeetupSource(BaseSource):
    name = "meetup"

    def __init__(self, queries: list[str], locations: list[str]):
        super().__init__()
        self.queries = queries
        self.locations = locations

    def fetch(self) -> list[Event]:
        out: list[Event] = []
        seen_urls: set[str] = set()

        for loc in self.locations:
            for q in self.queries:
                qs = urlencode({
                    "keywords": q,
                    "location": loc,
                    "source": "EVENTS",
                })
                url = f"https://www.meetup.com/find/?{qs}"
                try:
                    r = self.get(url)
                except Exception as e:   # noqa: BLE001
                    log.warning("[meetup] %s failed: %s", url, e)
                    continue

                before = len(out)
                for node in extract_jsonld_events(r.text):
                    ev = jsonld_to_event(node, source=self.name,
                                         fallback_url=url)
                    if not ev:
                        continue
                    if ev.source_url in seen_urls:
                        continue
                    seen_urls.add(ev.source_url)
                    out.append(ev)
                log.info("[meetup] %s @ %s -> %d events",
                         q, loc, len(out) - before)

        self._resolve_flyers(out)
        return out

    def _resolve_flyers(self, events: list[Event]) -> None:
        """For events that came back without an image (most of them, since
        the search page only exposes Meetup's placeholder), fetch the
        detail page and pick up the real flyer.

        Mutates events in place. Errors per event are swallowed — a missing
        flyer is not worth failing the run over.
        """
        needs = [e for e in events if not e.image_url]
        if not needs:
            return
        resolved = 0
        for ev in needs:
            flyer = self._flyer_from_event_page(ev.source_url)
            if flyer:
                ev.image_url = flyer
                resolved += 1
        log.info("[meetup] resolved %d/%d real flyers from detail pages",
                 resolved, len(needs))

    def _flyer_from_event_page(self, event_url: str) -> str | None:
        """Return the real flyer URL for a single Meetup event, or None."""
        try:
            r = self.get(event_url)
        except Exception as e:   # noqa: BLE001
            log.debug("[meetup] flyer fetch failed for %s: %s", event_url, e)
            return None

        # Prefer JSON-LD `image` — it tends to point at a larger asset
        # than og:image, and the schema is unambiguous.
        for node in extract_jsonld_events(r.text):
            img = node.get("image")
            if isinstance(img, list) and img:
                img = img[0]
            if isinstance(img, dict):
                img = img.get("url")
            normalized = _normalize_image(img, event_url)
            if normalized:
                return normalized

        # Last resort: og:image meta tag (smaller but reliable).
        soup = BeautifulSoup(r.text, "lxml")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return _normalize_image(og["content"], event_url)
        return None
