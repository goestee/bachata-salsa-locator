"""Orchestrator. Run me to refresh EVENTS.md.

    python -m src.main             # normal run
    python -m src.main --dry-run   # don't write files
    python -m src.main --only=eventbrite,danceus
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from .filters import classify_tags, is_dance_event, is_in_dfw
from .models import Event
from .render import render_html, render_markdown
from .sources.base import BaseSource
from .sources.eventbrite import EventbriteSource
from .sources.generic_jsonld import GenericCalendarSource
from .sources.golatindance import GoLatinDanceSource
from .sources.instagram import InstagramSource
from .sources.meetup import MeetupSource
from .storage import EventStore

log = logging.getLogger("dfw_dance")

ROOT = Path(__file__).resolve().parent.parent


def _load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_sources(cfg: dict, only: set[str] | None) -> list[BaseSource]:
    s = cfg["sources"]
    out: list[BaseSource] = []

    def want(name: str, default_cfg: dict) -> bool:
        if not default_cfg.get("enabled", False):
            return False
        if only and name not in only:
            return False
        return True

    if want("eventbrite", s["eventbrite"]):
        out.append(EventbriteSource(
            queries=s["eventbrite"]["queries"],
            location_slugs=s["eventbrite"]["location_slugs"],
        ))
    if want("meetup", s["meetup"]):
        # Back-compat: accept either `locations` (list) or `location` (str).
        m_cfg = s["meetup"]
        locs = m_cfg.get("locations") or [m_cfg["location"]]
        out.append(MeetupSource(
            queries=m_cfg["queries"],
            locations=locs,
        ))
    if want("danceus", s["danceus"]):
        out.append(GenericCalendarSource("danceus", s["danceus"]["urls"],
                                         assume_dfw=True))
    if want("golatindance", s["golatindance"]):
        # NOTE: assume_dfw is intentionally False. The GoLatinDance iCal feed
        # at /events/?ical=1 returns events worldwide, not just DFW. The
        # city-string filter in filters.py will keep only DFW events.
        out.append(GoLatinDanceSource(
            page_url=s["golatindance"]["url"],
            ical_candidates=s["golatindance"].get("ical_candidates", []),
        ))
    if want("salsavida", s["salsavida"]):
        # SalsaVida tags Central times with -07:00 offsets — strip the bogus
        # timezone so the literal clock-time is preserved as local Central.
        out.append(GenericCalendarSource(
            "salsavida", [s["salsavida"]["url"]],
            assume_dfw=True, strip_timezone=True,
        ))
    if want("salsadallas", s["salsadallas"]):
        out.append(GenericCalendarSource("salsadallas",
                                         [s["salsadallas"]["url"]],
                                         assume_dfw=True))
    if want("studio22", s["studio22"]):
        out.append(GenericCalendarSource("studio22", [s["studio22"]["url"]],
                                         assume_dfw=True))
    if want("instagram", s["instagram"]):
        out.append(InstagramSource(
            accounts=s["instagram"]["accounts"],
            posts_per_account=s["instagram"].get("posts_per_account", 8),
            hashtags=s["instagram"].get("hashtags", []),
            hashtags_enabled=s["instagram"].get("hashtags_enabled", False),
        ))
    return out


def _filter_events(raw: list[Event], cfg: dict,
                   trusted_sources: set[str]) -> list[Event]:
    kw_cfg = cfg["keywords"]
    geo_cfg = cfg["geo"]

    must_match = kw_cfg["must_match_any"]
    type_tags = kw_cfg["type_tags"]
    cities = geo_cfg["cities"]

    out: list[Event] = []
    drop_keyword = drop_geo = 0
    for ev in raw:
        # Instagram advisories already passed a stricter filter upstream.
        if ev.source != "instagram":
            if not is_dance_event(ev, must_match):
                drop_keyword += 1
                continue
            # Sources flagged as DFW-focused (e.g. local aggregators) are
            # trusted to be in DFW even if individual events lack a city
            # string in their location.
            if ev.source not in trusted_sources:
                if not is_in_dfw(ev, cities, geo_cfg.get("bbox")):
                    drop_geo += 1
                    continue
        ev.tags = list(dict.fromkeys((ev.tags or []) + classify_tags(ev, type_tags)))
        out.append(ev)

    log.info("Filter pass: kept %d / dropped kw=%d geo=%d",
             len(out), drop_keyword, drop_geo)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DFW Bachata/Salsa aggregator")
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--only",
                   help="Comma-separated source names to run (others skipped)")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write EVENTS.md or events.json")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = _load_config(Path(args.config))
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    sources = _build_sources(cfg, only)

    if not sources:
        log.error("No sources enabled. Edit config.yaml.")
        return 2

    started = time.monotonic()
    raw_events: list[Event] = []
    per_source_counts: dict[str, int] = {}
    failures: list[str] = []
    for src in sources:
        log.info("=== Running source: %s ===", src.name)
        t0 = time.monotonic()
        try:
            evs = src.fetch()
        except Exception as e:   # noqa: BLE001
            log.exception("[%s] crashed: %s", src.name, e)
            failures.append(src.name)
            continue
        per_source_counts[src.name] = len(evs)
        raw_events.extend(evs)
        log.info("[%s] %d events in %.1fs", src.name, len(evs),
                 time.monotonic() - t0)

    log.info("Collected %d raw events from %d sources",
             len(raw_events), len(sources))

    trusted = {s.name for s in sources if getattr(s, "assume_dfw", False)}
    filtered = _filter_events(raw_events, cfg, trusted_sources=trusted)

    out_cfg = cfg["output"]
    data_path = ROOT / out_cfg["data_path"]
    md_path = ROOT / out_cfg["markdown_path"]

    store = EventStore(data_path)
    new, updated = store.upsert_many(filtered)
    cutoff = (datetime.now(timezone.utc).date()
              - timedelta(days=out_cfg.get("prune_past_days", 1))).isoformat()
    pruned = store.prune_past(cutoff)

    log.info("Store delta: +%d new, %d refreshed, %d pruned",
             len(new), len(updated), pruned)

    if args.dry_run:
        log.info("Dry-run: skipping writes.")
    else:
        store.save()
        render_markdown(
            store.all(),
            md_path,
            horizon_days=out_cfg.get("horizon_days", 90),
        )
        outputs = [str(md_path), str(data_path)]
        html_rel = out_cfg.get("html_path")
        if html_rel:
            html_path = ROOT / html_rel
            render_html(
                store.all(),
                html_path,
                horizon_days=out_cfg.get("horizon_days", 90),
            )
            outputs.append(str(html_path))
        log.info("Wrote %s", ", ".join(outputs))

    elapsed = time.monotonic() - started
    summary = {
        "elapsed_sec": round(elapsed, 1),
        "raw_total": len(raw_events),
        "kept": len(filtered),
        "new": len(new),
        "updated": len(updated),
        "pruned": pruned,
        "by_source": per_source_counts,
        "failures": failures,
    }
    log.info("Run summary: %s", json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
