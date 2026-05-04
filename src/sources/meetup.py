"""Meetup source — public website search (their API is now Pro-only).

We hit the public search SSR page and extract JSON-LD events. Meetup's
official GraphQL API requires a paid Meetup Pro subscription as of 2025.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from .base import BaseSource
from ._jsonld import extract_jsonld_events, jsonld_to_event
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

        return out
