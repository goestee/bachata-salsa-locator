"""Instagram source backed by Apify's `apify/instagram-scraper` actor.

Replaces the previous `instaloader` implementation, which broke when
Instagram started blocking anonymous GraphQL requests with 403 in 2026.
Apify handles the IP rotation + session warming on their side; we just
POST a list of profile URLs and parse event info out of the captions
that come back.

Cost: ~$0.30 per 1,000 results from this actor. With 8 accounts × 6 posts
× twice/day, that's ~30,000 results/year ≈ $9/year.

Set the `APIFY_TOKEN` env var (locally) or repo secret (in CI) to enable.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests
from dateutil import parser as dtparser

from ..models import Event
from ._price import price_from_text
from .base import BaseSource

log = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("America/Chicago")


# --- caption pattern banks -------------------------------------------------

_DAY_NAMES = {
    "monday": 0, "mondays": 0, "tuesday": 1, "tuesdays": 1,
    "wednesday": 2, "wednesdays": 2, "thursday": 3, "thursdays": 3,
    "friday": 4, "fridays": 4, "saturday": 5, "saturdays": 5,
    "sunday": 6, "sundays": 6,
}

_MONTH_RE = (
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
)

# "April 17" / "April 17, 2026" / "Nov 20-22" / "Nov 20–22"
_MONTH_DATE_RE = re.compile(
    rf"\b{_MONTH_RE}\s+\d{{1,2}}(?:\s*[-–—]\s*\d{{1,2}})?(?:,?\s+\d{{4}})?",
    re.IGNORECASE,
)

# "Friday, April 17" / "Fri April 17"
_FULL_DATE_RE = re.compile(
    rf"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day|nesday|sday|urday)?,?\s+"
    rf"{_MONTH_RE}\s+\d{{1,2}}(?:,?\s+\d{{4}})?",
    re.IGNORECASE,
)

# 5/15 or 5/15/26
_SLASH_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")

# "8 PM" / "8-11pm" / "6 - 10 PM" / "9:30 PM"
_TIME_RE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?)\s*(?:[-–—]\s*(\d{1,2}(?::\d{2})?))?\s*(am|pm)\b",
    re.IGNORECASE,
)

# "every Monday" / "each Friday" / "Mondays"
_RECUR_RE = re.compile(
    r"\b(?:every|each)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b",
    re.IGNORECASE,
)

_TONIGHT_RE = re.compile(r"\btonight\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)

_DANCE_WORDS = (
    "salsa", "bachata", "kizomba", "merengue", "cha-cha", "cha cha",
    "latin dance", "reggaeton", "cumbia", "mambo", "zouk",
)

# Words that indicate the post is announcing an actual event vs. just being
# a marketing/inspiration post that happens to use a dance word. We require
# at least one of these in addition to a date hit, to filter out false
# positives like "People think we just dance... tomorrow, who knows" — which
# has both "salsa" hashtags and the word "tomorrow", but isn't an event.
_EVENT_HINT_WORDS = (
    "social", "class", "classes", "lesson", "lessons", "workshop",
    "workshops", "party", "parties", "fiesta", "festival", "congress",
    "bootcamp", "boot camp", "intensive", "showcase", "performance",
    "live music", "concert", "dj ", "rsvp", "doors", "cover",
    "tickets", "ticket", "join us", "free entry", "no cover",
    "register", "sign up", "spots", "passes",
    # Day-name + "night" patterns ("salsa night", "latin night")
    "salsa night", "latin night", "bachata night", "dance night",
    "dance party", "dance social",
)

# Lines that aren't really titles — IG promoters often label translated
# / abbreviated copies of the same announcement.
_TITLE_NOISE_PREFIXES = (
    "short version", "full version", "english", "en espa", "spanish",
    "translation", "____", "----", "====",
)

# Words a venue handle would typically contain (so we can pluck it from
# @-mentions in the caption rather than misattribute to a person tag).
_VENUE_HANDLE_HINTS = (
    "dance", "dallas", "salsa", "bachata", "club", "studio", "bar",
    "grill", "stratos", "vidorra", "hangout", "merkado", "kumbala",
    "alphamidway", "studio22", "ocho", "midway", "lounge", "tx",
    "park", "warren",
)


def _strip_emojis(s: str) -> str:
    """Remove emoji + symbol code points for cleaner title extraction."""
    return re.sub(r"[\U00010000-\U0010ffff]", "", s)


def _strip_hashtags(text: str) -> str:
    """Return caption body with hashtag noise removed.

    IG posts routinely pile dance hashtags (`#salsadallas #dancelessonsdallas`)
    onto every post — including non-event content. Treating those tags as
    evidence of dance intent leaks lots of false positives. Strip both
    standalone tag-only lines and inline `#word` tokens before any keyword
    matching.
    """
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(re.sub(r"#\w+", "", line))
    return "\n".join(out)


class InstagramSource(BaseSource):
    """Fetches recent posts from a curated list of DFW dance Instagram
    accounts via Apify, then heuristically parses captions into Events.

    Captions without a parseable date OR without any dance keyword are
    silently dropped — better to miss a few real events than to publish
    bogus dates.
    """

    name = "instagram"
    # We hand-curate accounts as DFW-local, so skip the city-string filter.
    assume_dfw = True

    APIFY_ACTOR = "apify~instagram-scraper"
    APIFY_TIMEOUT = 240

    def __init__(
        self,
        accounts: list[str],
        posts_per_account: int = 8,
        max_age_days: int = 60,
        hashtags: list[str] | None = None,
        hashtags_enabled: bool = False,
        token_env: str = "APIFY_TOKEN",
    ) -> None:
        super().__init__()
        self.accounts = [a.strip().lstrip("@") for a in accounts if a and a.strip()]
        self.posts_per_account = posts_per_account
        self.max_age_days = max_age_days
        # Accepted for back-compat with old config; not yet wired through.
        self.hashtags = hashtags or []
        self.hashtags_enabled = hashtags_enabled
        self.token = (os.environ.get(token_env) or "").strip()

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    def fetch(self) -> List[Event]:
        if not self.token:
            log.warning("[%s] APIFY_TOKEN not set — skipping IG fetch", self.name)
            return []
        if not self.accounts:
            log.warning("[%s] no accounts configured — skipping", self.name)
            return []

        urls = [f"https://www.instagram.com/{a}/" for a in self.accounts]
        endpoint = (
            "https://api.apify.com/v2/acts/"
            f"{self.APIFY_ACTOR}/run-sync-get-dataset-items"
            f"?token={self.token}&timeout={self.APIFY_TIMEOUT}"
        )
        payload = {
            "directUrls": urls,
            "resultsType": "posts",
            "resultsLimit": self.posts_per_account,
            "addParentData": False,
        }

        log.info("[%s] querying Apify for %d account(s), %d posts each",
                 self.name, len(urls), self.posts_per_account)
        try:
            r = requests.post(endpoint, json=payload,
                              timeout=self.APIFY_TIMEOUT + 30)
            r.raise_for_status()
            posts = r.json() or []
        except Exception as e:    # noqa: BLE001
            log.error("[%s] Apify call failed: %s", self.name, e)
            return []

        log.info("[%s] %d raw posts back from Apify", self.name, len(posts))

        cutoff = datetime.now(LOCAL_TZ) - timedelta(days=self.max_age_days)
        events: list[Event] = []
        skipped = {"old": 0, "no_date": 0, "no_dance": 0, "missing": 0}

        for p in posts:
            if p.get("error"):
                skipped["missing"] += 1
                continue
            try:
                evs, reason = self._post_to_events(p, cutoff)
            except Exception as e:    # noqa: BLE001
                log.debug("[%s] post parse error %s: %s",
                          self.name, p.get("url"), e)
                skipped["no_date"] += 1
                continue
            if not evs:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            events.extend(evs)

        log.info(
            "[%s] %d event(s) extracted (skipped: %d old, %d no-date, "
            "%d non-dance, %d missing)",
            self.name, len(events), skipped["old"], skipped["no_date"],
            skipped["no_dance"], skipped["missing"],
        )
        return events

    # ------------------------------------------------------------------
    # Caption -> Event(s)
    # ------------------------------------------------------------------

    def _post_to_events(
        self, post: dict, cutoff: datetime,
    ) -> tuple[List[Event], str]:
        caption = (post.get("caption") or "").strip()
        if not caption:
            return [], "no_date"

        try:
            posted_at = dtparser.parse(post["timestamp"]).astimezone(LOCAL_TZ)
        except Exception:
            return [], "no_date"
        if posted_at < cutoff:
            return [], "old"

        # Dance keywords must appear in the post BODY, not just in hashtag
        # lines — IG users sprinkle `#salsadallas` on every post regardless
        # of topic, so hashtag-only matches are too weak a signal.
        body = _strip_hashtags(caption).lower()
        if not any(w in body for w in _DANCE_WORDS):
            return [], "no_dance"

        dates = self._extract_dates(caption, posted_at)
        if not dates:
            return [], "no_date"

        time_range = self._extract_time_range(caption)
        owner = post.get("ownerUsername") or ""
        title = self._extract_title(caption, owner)
        venue = self._extract_venue(caption, owner)
        price = price_from_text(caption)
        tags = self._tags_from_caption(body)
        url = post.get("url") or post.get("inputUrl") or ""
        image = post.get("displayUrl")

        evs: list[Event] = []
        for d in dates:
            start = d
            end = None
            if time_range:
                sh, sm, eh, em = time_range
                start = d.replace(hour=sh, minute=sm or 0)
                if eh is not None:
                    end_dt = d.replace(hour=eh, minute=em or 0)
                    if end_dt <= start:   # crosses midnight
                        end_dt += timedelta(days=1)
                    end = end_dt

            ev = Event(
                title=title,
                # Event model stores datetimes as ISO strings; we render
                # them back into datetimes downstream.
                start=start.isoformat(),
                end=end.isoformat() if end else None,
                venue=venue,
                city=None,
                description=caption[:500],
                image_url=image,
                tags=tags,
                source=self.name,
                source_url=url,
                price=price,
            )
            ev.fill_id()
            evs.append(ev)

        return evs, "ok"

    # ------------------------------------------------------------------
    # Extractors
    # ------------------------------------------------------------------

    @staticmethod
    def _tags_from_caption(c_lower: str) -> list[str]:
        tags: list[str] = []
        for w in ("salsa", "bachata", "kizomba", "merengue"):
            if w in c_lower:
                tags.append(w)
        if any(w in c_lower for w in ("class", "lesson", "workshop", "bootcamp")):
            tags.append("lesson")
        if any(w in c_lower for w in ("social", "party", "fiesta", "night")):
            tags.append("social")
        return tags

    @staticmethod
    def _extract_title(caption: str, owner: str) -> str:
        cleaned = _strip_emojis(caption)
        for raw_line in cleaned.splitlines():
            line = raw_line.strip("-*•·… \t").strip()
            if len(line) < 5 or line.startswith("#"):
                continue
            low = line.lower()
            if any(low.startswith(p) for p in _TITLE_NOISE_PREFIXES):
                continue
            return line[:120]
        return f"{owner or 'instagram'} post"

    @staticmethod
    def _extract_venue(caption: str, owner: str) -> Optional[str]:
        for m in re.finditer(r"@([a-zA-Z0-9._]{4,30})", caption):
            handle = m.group(1)
            if any(k in handle.lower() for k in _VENUE_HANDLE_HINTS):
                return handle.replace("_", " ").replace(".", " ").title()
        m = re.search(r"\bat\s+([A-Z][\w& '.-]{2,40})", caption)
        if m:
            cand = m.group(1).strip().rstrip(".,!?")
            return cand
        if owner:
            return owner.replace("_", " ").replace(".", " ").title()
        return None

    def _extract_dates(
        self, caption: str, posted_at: datetime,
    ) -> list[datetime]:
        results: list[datetime] = []

        # Only honor "tonight"/"tomorrow" when the caption also has a
        # clock-time. Otherwise these match colloquial usage like
        # "tomorrow, who knows" and produce phantom events.
        has_time = bool(_TIME_RE.search(caption))

        if has_time and _TONIGHT_RE.search(caption):
            results.append(posted_at.replace(
                hour=0, minute=0, second=0, microsecond=0))

        if has_time and _TOMORROW_RE.search(caption):
            d = posted_at + timedelta(days=1)
            results.append(d.replace(
                hour=0, minute=0, second=0, microsecond=0))

        for m in _RECUR_RE.finditer(caption):
            day = m.group(1).lower().rstrip("s")
            if day in _DAY_NAMES:
                results.extend(self._next_weekly_dates(_DAY_NAMES[day], posted_at))

        absolutes: list[str] = []
        for pat in (_FULL_DATE_RE, _MONTH_DATE_RE, _SLASH_DATE_RE):
            for m in pat.finditer(caption):
                absolutes.append(m.group(0))
        for txt in absolutes:
            d = self._parse_absolute(txt, posted_at)
            if d:
                results.append(d)

        seen = set()
        unique: list[datetime] = []
        for d in results:
            key = d.date()
            if key in seen:
                continue
            seen.add(key)
            unique.append(d)
        return unique[:10]

    @staticmethod
    def _next_weekly_dates(target_dow: int,
                           posted_at: datetime) -> list[datetime]:
        today = posted_at.date()
        offset = (target_dow - today.weekday()) % 7
        if offset == 0:
            offset = 7
        return [
            datetime(d.year, d.month, d.day, 0, 0, tzinfo=LOCAL_TZ)
            for d in (today + timedelta(days=offset + 7 * i) for i in range(6))
        ]

    @staticmethod
    def _parse_absolute(txt: str,
                        posted_at: datetime) -> Optional[datetime]:
        head = re.split(r"\s*[-–—]\s*", txt, maxsplit=1)[0]
        try:
            parsed = dtparser.parse(
                head, default=datetime(posted_at.year, 1, 1), fuzzy=True,
            )
        except Exception:
            return None
        parsed = parsed.replace(tzinfo=LOCAL_TZ, hour=0, minute=0,
                                second=0, microsecond=0)
        if parsed.date() < posted_at.date() and str(posted_at.year) not in txt:
            parsed = parsed.replace(year=posted_at.year + 1)
        return parsed

    def _extract_time_range(self, caption: str):
        m = _TIME_RE.search(caption)
        if not m:
            return None
        start_clock, end_clock, ampm = m.group(1), m.group(2), m.group(3).lower()
        try:
            sh, sm = self._parse_clock(start_clock, ampm)
            eh = em = None
            if end_clock:
                eh, em = self._parse_clock(end_clock, ampm)
            return (sh, sm, eh, em)
        except Exception:
            return None

    @staticmethod
    def _parse_clock(txt: str, ampm: str) -> tuple[int, int]:
        if ":" in txt:
            h_s, m_s = txt.split(":")
            h, m = int(h_s), int(m_s)
        else:
            h = int(re.sub(r"\D", "", txt))
            m = 0
        if ampm == "pm" and h < 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
        return h, m
