"""JSON-backed persistence with dedup + new-since-last-run tracking."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Event

log = logging.getLogger(__name__)


class EventStore:
    """Thin wrapper around a JSON file. One row per unique event id."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._by_id: dict[str, Event] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not read %s (%s); starting fresh.", self.path, e)
            return
        for row in raw.get("events", []):
            ev = Event.from_dict(row).fill_id()
            self._by_id[ev.id] = ev

    def save(self) -> None:
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(self._by_id),
            "events": [e.to_dict() for e in self._by_id.values()],
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)

    def upsert_many(self, events: Iterable[Event]) -> tuple[list[Event], list[Event]]:
        """Insert/update events. Returns (new_events, updated_events)."""
        new: list[Event] = []
        updated: list[Event] = []
        for ev in events:
            ev = ev.fill_id().stamp_seen()
            existing = self._by_id.get(ev.id)
            if existing is None:
                self._by_id[ev.id] = ev
                new.append(ev)
            else:
                # Keep the original first_seen_at; refresh the rest.
                ev.first_seen_at = existing.first_seen_at or ev.first_seen_at
                self._by_id[ev.id] = ev
                updated.append(ev)
        return new, updated

    def prune_past(self, before_iso_date: str) -> int:
        """Drop events whose start date is strictly before `before_iso_date`
        (YYYY-MM-DD). Events with no start date are kept."""
        keep: dict[str, Event] = {}
        dropped = 0
        for eid, ev in self._by_id.items():
            if ev.start and ev.start[:10] < before_iso_date:
                dropped += 1
                continue
            keep[eid] = ev
        self._by_id = keep
        return dropped

    def all(self) -> list[Event]:
        return list(self._by_id.values())
