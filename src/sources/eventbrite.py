"""Eventbrite source.

Eventbrite removed their public Event Search API in 2019. The website still
publishes city/keyword listing pages with embedded JSON-LD that's more or
less stable, e.g.:

    https://www.eventbrite.com/d/tx--dallas/salsa/

We parse JSON-LD ItemList -> Event nodes from those pages.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from .base import BaseSource
from ._jsonld import extract_jsonld_events, jsonld_to_event
from ..models import Event

log = logging.getLogger(__name__)


class EventbriteSource(BaseSource):
    name = "eventbrite"

    def __init__(self, queries: list[str], location_slugs: list[str]):
        super().__init__()
        self.queries = queries
        self.location_slugs = location_slugs

    def fetch(self) -> list[Event]:
        out: list[Event] = []
        seen_urls: set[str] = set()

        for slug in self.location_slugs:
            for q in self.queries:
                url = f"https://www.eventbrite.com/d/{slug}/{quote(q)}/"
                try:
                    r = self.get(url)
                except Exception as e:   # noqa: BLE001
                    log.warning("[eventbrite] %s failed: %s", url, e)
                    continue

                count_before = len(out)
                for node in extract_jsonld_events(r.text):
                    ev = jsonld_to_event(node, source=self.name, fallback_url=url)
                    if not ev:
                        continue
                    if ev.source_url in seen_urls:
                        continue
                    seen_urls.add(ev.source_url)
                    out.append(ev)
                log.info("[eventbrite] %s -> %d events", url,
                         len(out) - count_before)

        return out
