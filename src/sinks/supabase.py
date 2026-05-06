"""Pushes new events to a Supabase `pending_events` table for human review.

This is one-way: we POST events, the partner app shows them in a pending
queue, a human approves/edits/publishes them on their side. We never read
back from Supabase — local state is the source of truth for "what we've
already sent."

Auth: anon key from `SUPABASE_ANON_KEY` env var. URL from `SUPABASE_URL`.
Both are expected to be GitHub Actions secrets (or local env in dev).
The anon key is fine to expose ONLY because the table has an RLS policy
that grants `anon` INSERT-only access to `pending_events` — no select,
no update, no delete.

Schema mapping (Event field -> pending_events column):
    title                              -> title
    venue                              -> venue_name
    city                               -> area
    address                            -> address
    start (ISO 8601)                   -> start_datetime
    end (ISO 8601)                     -> end_datetime
    price (string -> numeric, see _price_to_numeric) -> price
    tags (first dance genre)           -> music_type
    "lesson" in tags                   -> lesson_included (bool)
    source_url if source == instagram  -> instagram_url
    source_url                         -> source_url
    image_url                          -> flyer_image_url
"""
from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

import requests

from ..models import Event

log = logging.getLogger(__name__)


# Order matters: when an event is tagged with multiple genres (e.g. salsa +
# bachata + merengue), we pick the first match in this list as the canonical
# music_type. Salsa and bachata are the dominant DFW styles so they win first.
_GENRE_PRIORITY = (
    "salsa", "bachata", "kizomba", "merengue", "cumbia",
    "cha-cha", "cha cha", "zouk", "reggaeton", "mambo",
)


def _pick_genre(tags: list[str] | None) -> Optional[str]:
    if not tags:
        return None
    tags_lower = {t.lower() for t in tags}
    for g in _GENRE_PRIORITY:
        if g in tags_lower:
            return g
    return None


def _price_to_numeric(price: str | None) -> Optional[float]:
    """Convert our display-string prices to a numeric the Supabase column
    can accept. Heuristic — when in doubt, return None and let the human
    reviewer fill it in.

    Examples:
        "Free"      -> 0
        "$10"       -> 10.0
        "$10-$25"   -> 10.0     (use lower bound; "starting at $10")
        "Free–$25"  -> 0
        "Varies"    -> None
        None / ""   -> None
    """
    if not price:
        return None
    p = price.strip().lower()
    if p in ("free", "no cover", "0", "$0"):
        return 0.0
    # First $ amount in the string wins (handles ranges and prefix junk).
    m = re.search(r"\$?\s*(\d{1,4}(?:\.\d{1,2})?)", price)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _has_lesson(tags: list[str] | None) -> bool:
    return bool(tags) and any(t.lower() == "lesson" for t in tags)


def event_to_payload(ev: Event) -> dict:
    """Map our Event to Orlando's pending_events column schema."""
    instagram_url = ev.source_url if (ev.source == "instagram") else None
    return {
        "title": ev.title or None,
        "venue_name": ev.venue or None,
        "area": ev.city or None,
        "address": ev.address or None,
        "start_datetime": ev.start or None,
        "end_datetime": ev.end or None,
        "price": _price_to_numeric(ev.price),
        "music_type": _pick_genre(ev.tags),
        "lesson_included": _has_lesson(ev.tags),
        "instagram_url": instagram_url,
        "source_url": ev.source_url or None,
        "flyer_image_url": ev.image_url or None,
    }


class SupabaseSink:
    """POSTs events as bulk inserts to a Supabase REST endpoint.

    Designed to be safe to call repeatedly: the caller passes only events
    that haven't been submitted yet, and tracks success state so we don't
    duplicate. Errors are logged but never raised — the rest of the
    aggregator run finishes regardless.
    """

    def __init__(
        self,
        table: str = "pending_events",
        url_env: str = "SUPABASE_URL",
        key_env: str = "SUPABASE_ANON_KEY",
        chunk_size: int = 50,
    ) -> None:
        self.table = table
        self.base_url = (os.environ.get(url_env) or "").rstrip("/")
        self.key = (os.environ.get(key_env) or "").strip()
        self.chunk_size = chunk_size

    @property
    def configured(self) -> bool:
        """True when we have both URL and anon key. Without either, calling
        `submit()` is a no-op (returns empty list)."""
        return bool(self.base_url and self.key)

    def submit(self, events: List[Event]) -> List[Event]:
        """Submit events to the pending_events table.

        Returns the list of events that were successfully accepted, so the
        caller can mark them as submitted in local state. Events that fail
        (RLS denial, schema mismatch, network error) are simply not in the
        returned list — they'll be retried on the next run.
        """
        if not self.configured:
            log.info("[supabase] not configured (URL or key missing) — skipping")
            return []
        if not events:
            return []

        endpoint = f"{self.base_url}/rest/v1/{self.table}"
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            # `return=minimal` so Supabase doesn't echo back the inserted
            # rows. Saves bandwidth and we don't need the response body.
            "Prefer": "return=minimal",
        }

        sent: list[Event] = []
        for i in range(0, len(events), self.chunk_size):
            chunk = events[i:i + self.chunk_size]
            try:
                payloads = [event_to_payload(e) for e in chunk]
                r = requests.post(endpoint, json=payloads, headers=headers,
                                  timeout=30)
            except requests.RequestException as e:
                log.error("[supabase] network error on chunk %d-%d: %s",
                          i, i + len(chunk), e)
                continue
            if r.status_code >= 400:
                # 401/403 here usually means RLS isn't permitting anon
                # inserts. 400 usually means a schema mismatch (a column
                # rejected the value we sent). Log enough to debug.
                log.error("[supabase] %d on chunk %d-%d: %s",
                          r.status_code, i, i + len(chunk),
                          r.text[:300])
                continue
            sent.extend(chunk)

        log.info("[supabase] sent %d/%d events to %s",
                 len(sent), len(events), self.table)
        return sent
