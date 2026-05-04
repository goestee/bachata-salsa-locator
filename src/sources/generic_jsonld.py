"""Generic calendar-page source.

For each configured URL, tries (in order):
  1. The Events Calendar (WordPress) iCal feed: appends `?ical=1` to the URL.
  2. JSON-LD <script type="application/ld+json"> blocks on the page itself.

Most DFW dance/event listing sites are either WordPress+TEC (iCal works
beautifully) or hand-built (we can sometimes still pluck JSON-LD). If this
class still produces 0 events for a given site, that site needs a bespoke
scraper.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse, urlunparse

from icalendar import Calendar

from .base import BaseSource
from ._jsonld import extract_jsonld_events, jsonld_to_event
from ._microdata import extract_microdata_events
from ._price import price_from_text
from ..models import Event

log = logging.getLogger(__name__)


_TZ_OFFSET_RE = re.compile(r"([+-]\d{2}:?\d{2}|Z)$")


def _strip_tz(iso: str | None) -> str | None:
    """Return an ISO timestamp with any trailing timezone offset removed.
    Useful for sites that publish DFW events with an incorrect server-side
    offset (e.g., salsavida.com tags Central times with -07:00)."""
    if not iso:
        return iso
    return _TZ_OFFSET_RE.sub("", iso)


class GenericCalendarSource(BaseSource):
    """Hits a list of URLs, tries iCal then JSON-LD then microdata."""

    def __init__(self, name: str, urls: list[str], assume_dfw: bool = False,
                 strip_timezone: bool = False):
        super().__init__()
        self.name = name
        self.urls = urls
        self.assume_dfw = assume_dfw
        # If True, drop any tz offset in start/end so the timestamps are
        # treated as naive local-time (which renders as Central).
        self.strip_timezone = strip_timezone

    def fetch(self) -> list[Event]:
        out: list[Event] = []
        # Dedup by stable_id (title + date + venue), NOT source_url. Many
        # sites publish the same recurring event series with one URL but
        # multiple calendar occurrences (different dates) — URL-dedup would
        # collapse them all to a single entry.
        seen_ids: set[str] = set()

        def _add(ev: Event) -> bool:
            ev = self._post_process(ev)
            ev = ev.fill_id()
            if ev.id in seen_ids:
                return False
            seen_ids.add(ev.id)
            out.append(ev)
            return True

        for url in self.urls:
            ical_events = self._try_ical(url)
            if ical_events:
                for ev in ical_events:
                    _add(ev)
                continue

            try:
                r = self.get(url)
            except Exception as e:   # noqa: BLE001
                log.warning("[%s] %s failed: %s", self.name, url, e)
                continue

            # JSON-LD pass.
            jsonld_count = 0
            for node in extract_jsonld_events(r.text):
                ev = jsonld_to_event(node, source=self.name, fallback_url=url)
                if ev and _add(ev):
                    jsonld_count += 1
            log.info("[%s] %s (json-ld) -> %d events",
                     self.name, url, jsonld_count)

            # Microdata fallback (used by salsavida.com & friends).
            md_count = 0
            for ev in extract_microdata_events(r.text, base_url=url):
                ev.source = self.name
                if _add(ev):
                    md_count += 1
            if md_count:
                log.info("[%s] %s (microdata) -> %d events",
                         self.name, url, md_count)
        return out

    def _post_process(self, ev: Event) -> Event:
        if self.strip_timezone:
            ev.start = _strip_tz(ev.start)
            ev.end = _strip_tz(ev.end)
        return ev

    # -----------------------------------------------------------------
    def _try_ical(self, page_url: str) -> list[Event]:
        """Try the WordPress Events Calendar pattern: `?ical=1`."""
        parsed = urlparse(page_url)
        candidates: list[str] = []
        # Same URL with ?ical=1 (preserves path).
        candidates.append(urlunparse(parsed._replace(query="ical=1")))
        # Some sites also expose /events/?ical=1 at site root.
        candidates.append(f"{parsed.scheme}://{parsed.netloc}/events/?ical=1")

        for cand in dict.fromkeys(candidates):   # dedupe, preserve order
            try:
                r = self.get(cand, headers={"Accept": "text/calendar,*/*"})
            except Exception as e:   # noqa: BLE001
                log.debug("[%s] iCal candidate failed %s: %s",
                          self.name, cand, e)
                continue
            if "BEGIN:VCALENDAR" not in r.text[:200]:
                continue
            try:
                cal = Calendar.from_ical(r.text)
            except Exception as e:   # noqa: BLE001
                log.warning("[%s] iCal parse failed at %s: %s",
                            self.name, cand, e)
                continue
            evs = list(self._ical_to_events(cal, page_url))
            log.info("[%s] iCal %s -> %d events", self.name, cand, len(evs))
            return evs
        return []

    def _ical_to_events(self, cal: Calendar, source_url: str) -> Iterable[Event]:
        for comp in cal.walk("VEVENT"):
            title = str(comp.get("summary") or "").strip()
            if not title:
                continue
            description = _str_or_none(comp.get("description"))
            yield Event(
                title=title,
                start=_ical_dt(comp.get("dtstart")),
                end=_ical_dt(comp.get("dtend")),
                venue=_str_or_none(comp.get("location")),
                address=_str_or_none(comp.get("location")),
                description=description,
                source=self.name,
                source_url=str(comp.get("url") or source_url),
                price=price_from_text(description),
            )


def _str_or_none(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


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
    return dt.isoformat()


# Back-compat alias so existing imports keep working.
GenericJsonLdSource = GenericCalendarSource
