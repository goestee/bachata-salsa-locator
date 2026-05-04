"""Common scaffolding for source scrapers."""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..models import Event

log = logging.getLogger(__name__)


# Rotate through a few realistic UAs. Not a stealth measure — just polite
# default-realism so we don't get blackholed by aggressive WAFs.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


class FetchError(RuntimeError):
    pass


class BaseSource:
    """Subclass me, set `name`, implement `fetch()`."""

    name: str = "base"
    request_delay: tuple[float, float] = (1.0, 2.5)   # randomized polite delay
    timeout: float = 20.0
    # If True, the orchestrator skips the city-string geo filter for events
    # produced by this source. Use for sites that are themselves DFW-focused
    # but don't always include "Dallas/Fort Worth" in each event's location text.
    assume_dfw: bool = False

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def _polite_sleep(self) -> None:
        time.sleep(random.uniform(*self.request_delay))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=1, max=10),
        retry=retry_if_exception_type((requests.RequestException, FetchError)),
        reraise=True,
    )
    def get(self, url: str, **kw) -> requests.Response:
        log.debug("[%s] GET %s", self.name, url)
        r = self.session.get(url, timeout=self.timeout, **kw)
        if r.status_code == 429:
            raise FetchError(f"rate-limited at {url}")
        if r.status_code >= 500:
            raise FetchError(f"server {r.status_code} at {url}")
        r.raise_for_status()
        self._polite_sleep()
        return r

    def fetch(self) -> list[Event]:
        raise NotImplementedError

    # -----------------------------------------------------------------
    # Tiny helpers shared across sources.
    # -----------------------------------------------------------------
    @staticmethod
    def _clean(s: Optional[str]) -> Optional[str]:
        if s is None:
            return None
        return " ".join(s.split()).strip() or None
