"""GoLatinDance — DFW-specific Latin dance calendar.

This site appears to use The Events Calendar (WordPress plugin), which often
exposes:
  * an .ics feed (preferred)
  * REST API at /wp-json/tribe/events/v1/events
  * SSR HTML with JSON-LD as a fallback
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from icalendar import Calendar

from .base import BaseSource, FetchError
from ._jsonld import extract_jsonld_events, jsonld_to_event
from ._price import price_from_text
from ..models import Event

log = logging.getLogger(__name__)


class GoLatinDanceSource(BaseSource):
    name = "golatindance"

    def __init__(self, page_url: str, ical_candidates: Iterable[str]):
        super().__init__()
        self.page_url = page_url
        self.ical_candidates = list(ical_candidates)

    def fetch(self) -> list[Event]:
        events = self._try_ical()
        if events:
            return events
        return self._try_html()

    # -----------------------------------------------------------------
    def _try_ical(self) -> list[Event]:
        for url in self.ical_candidates:
            try:
                r = self.get(url, headers={"Accept": "text/calendar,*/*"})
            except Exception as e:   # noqa: BLE001
                log.debug("[golatindance] iCal candidate failed %s: %s", url, e)
                continue
            if "BEGIN:VCALENDAR" not in r.text[:200]:
                continue
            try:
                cal = Calendar.from_ical(r.text)
            except Exception as e:   # noqa: BLE001
                log.warning("[golatindance] iCal parse failed: %s", e)
                continue
            out = list(self._ical_to_events(cal, url))
            log.info("[golatindance] iCal %s -> %d events", url, len(out))
            return out
        return []

    def _ical_to_events(self, cal: Calendar, source_url: str) -> Iterable[Event]:
        for comp in cal.walk("VEVENT"):
            title = str(comp.get("summary") or "").strip()
            if not title:
                continue
            start = self._ical_dt(comp.get("dtstart"))
            end = self._ical_dt(comp.get("dtend"))
            url = str(comp.get("url") or source_url)
            location = str(comp.get("location") or "") or None
            description = str(comp.get("description") or "") or None
            yield Event(
                title=title, start=start, end=end,
                venue=location, address=location,
                description=description,
                source=self.name, source_url=url,
                price=price_from_text(description),
            )

    @staticmethod
    def _ical_dt(prop) -> str | None:
        if prop is None:
            return None
        try:
            dt = prop.dt
        except AttributeError:
            return None
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        # date (no time)
        return dt.isoformat()

    # -----------------------------------------------------------------
    def _try_html(self) -> list[Event]:
        try:
            r = self.get(self.page_url)
        except Exception as e:   # noqa: BLE001
            log.warning("[golatindance] HTML fetch failed: %s", e)
            return []
        out: list[Event] = []
        for node in extract_jsonld_events(r.text):
            ev = jsonld_to_event(node, source=self.name,
                                 fallback_url=self.page_url)
            if ev:
                out.append(ev)
        log.info("[golatindance] HTML JSON-LD -> %d events", len(out))
        return out
