"""Instagram source — anonymous, curated, low-volume.

Caveats (read these):
  * No login. Anonymous scraping is heavily rate-limited and may break.
  * We pull only N most-recent posts from a CURATED list of public profiles.
  * We DO NOT try to parse exact dates from captions (that's an unreliable
    LLM/regex job). Instead, posts that look event-y (have dance keywords +
    a date-ish pattern) are surfaced as "advisory" entries with `_TBD_`
    times and a link straight to the IG post — the user reads the post
    themselves to confirm.
  * The orchestrator enforces a 20-hour cool-down so we don't hammer IG
    even if the cron runs more often.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from .base import BaseSource
from ..models import Event

log = logging.getLogger(__name__)

# Lazy import: instaloader is heavy and we only need it if IG is enabled.
_instaloader = None


def _il():
    global _instaloader
    if _instaloader is None:
        import instaloader  # type: ignore
        _instaloader = instaloader
    return _instaloader


_DATE_HINTS = re.compile(
    r"\b("
    r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"   # 12/15, 12-15-25
    r"|"
    r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s+\d{1,2}"
    r"|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}"
    r"|"
    r"\btonight\b|\btomorrow\b|\bthis\s+(?:fri|sat|sun)[a-z]*\b"
    r")",
    re.IGNORECASE,
)

_DANCE_HINT = re.compile(
    r"\b(salsa|bachata|kizomba|merengue|cha[\s-]?cha|latin\s+(?:night|dance|social))\b",
    re.IGNORECASE,
)


class InstagramSource(BaseSource):
    name = "instagram"

    def __init__(self, accounts: list[str], posts_per_account: int = 6,
                 hashtags: list[str] | None = None,
                 hashtags_enabled: bool = False):
        super().__init__()
        self.accounts = accounts
        self.posts_per_account = max(1, min(posts_per_account, 12))
        self.hashtags = hashtags or []
        self.hashtags_enabled = hashtags_enabled

    def fetch(self) -> list[Event]:
        L = _il().Instaloader(
            quiet=True,
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
        )

        events: list[Event] = []
        for handle in self.accounts:
            try:
                events.extend(self._fetch_profile(L, handle))
            except Exception as e:   # noqa: BLE001
                log.warning("[instagram] @%s failed: %s", handle, e)

        if self.hashtags_enabled:
            for tag in self.hashtags:
                try:
                    events.extend(self._fetch_hashtag(L, tag))
                except Exception as e:   # noqa: BLE001
                    log.warning("[instagram] #%s failed: %s", tag, e)

        log.info("[instagram] surfaced %d posts as advisories", len(events))
        return events

    # -----------------------------------------------------------------
    def _fetch_profile(self, L, handle: str) -> list[Event]:
        log.info("[instagram] fetching @%s", handle)
        Profile = _il().Profile
        profile = Profile.from_username(L.context, handle)
        out: list[Event] = []
        for i, post in enumerate(profile.get_posts()):
            if i >= self.posts_per_account:
                break
            ev = self._post_to_event(post, handle, source_kind="profile")
            if ev:
                out.append(ev)
        return out

    def _fetch_hashtag(self, L, tag: str) -> list[Event]:
        log.info("[instagram] fetching #%s", tag)
        Hashtag = _il().Hashtag
        ht = Hashtag.from_name(L.context, tag)
        out: list[Event] = []
        for i, post in enumerate(ht.get_top_posts()):
            if i >= self.posts_per_account:
                break
            ev = self._post_to_event(post, tag, source_kind="hashtag")
            if ev:
                out.append(ev)
        return out

    def _post_to_event(self, post, label: str, source_kind: str) -> Event | None:
        caption = (post.caption or "").strip()
        if not caption:
            return None

        # Only surface posts that look event-y AND mention a dance style.
        if not _DANCE_HINT.search(caption):
            return None
        if not _DATE_HINTS.search(caption):
            return None

        first_line = caption.split("\n", 1)[0][:120].strip()
        prefix = "@" if source_kind == "profile" else "#"
        title = f"{prefix}{label}: {first_line or 'event post'}"

        # We DELIBERATELY don't try to parse the date from the caption.
        # `start` = post date so it sorts somewhere reasonable in the
        # markdown; the user clicks through to read the actual flyer.
        try:
            posted_at = post.date_utc.replace(tzinfo=timezone.utc).isoformat()
        except Exception:   # noqa: BLE001
            posted_at = datetime.now(timezone.utc).isoformat()

        return Event(
            title=title,
            start=posted_at,
            description=caption[:500],
            source=self.name,
            source_url=f"https://www.instagram.com/p/{post.shortcode}/",
            tags=["instagram-advisory"],
        )
