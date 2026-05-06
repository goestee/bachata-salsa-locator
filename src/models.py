"""Domain models for events."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


@dataclass
class Event:
    """A single dance event from any source."""

    title: str
    start: Optional[str]                # ISO 8601 string (or None if unknown)
    source: str                         # e.g. "eventbrite", "instagram"
    source_url: str                     # link back to the original posting

    end: Optional[str] = None
    venue: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    tags: list[str] = field(default_factory=list)   # e.g. ["social", "salsa"]
    price: Optional[str] = None     # display string: "Free", "$10", "$20-$25"

    # Populated by the storage layer.
    id: Optional[str] = None
    first_seen_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    # ISO timestamp of when this event was successfully submitted to the
    # downstream Supabase pending_events queue. None means "not yet sent."
    # Set by the supabase sink in main.py after a successful insert so we
    # don't re-submit the same event on every cron run.
    submitted_to_supabase: Optional[str] = None

    def stable_id(self) -> str:
        """Deterministic ID. Same event from different sources should collide
        when title + date + venue (city as fallback) match."""
        date_part = (self.start or "")[:10]   # YYYY-MM-DD
        loc_part = _norm(self.venue) or _norm(self.city)
        key = f"{_norm(self.title)}|{date_part}|{loc_part}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    def fill_id(self) -> "Event":
        if not self.id:
            self.id = self.stable_id()
        return self

    def stamp_seen(self, now: Optional[datetime] = None) -> "Event":
        ts = (now or datetime.now(timezone.utc)).isoformat()
        if not self.first_seen_at:
            self.first_seen_at = ts
        self.last_seen_at = ts
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        # Allow forward-compat: ignore unknown keys.
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in d.items() if k in known}
        return cls(**clean)
