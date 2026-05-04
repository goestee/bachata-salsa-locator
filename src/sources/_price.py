"""Shared helpers for normalizing event pricing into display strings."""
from __future__ import annotations

import re
from typing import Iterable


def _to_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        # Strings like "Free" / "FREE" / "no cover".
        low = s.lower()
        if low in ("free", "no cover", "0", "0.00"):
            return 0.0
        return None


def _fmt_amount(amount: float, currency: str = "USD") -> str:
    """Render a single dollar amount cleanly: $0 -> 'Free', $10 -> '$10',
    $10.50 -> '$10.50'."""
    if amount <= 0:
        return "Free"
    sym = "$" if currency.upper() == "USD" else (currency + " ")
    if amount == int(amount):
        return f"{sym}{int(amount)}"
    return f"{sym}{amount:.2f}"


def format_price_range(amounts: Iterable[float], currency: str = "USD") -> str | None:
    """Given a set of price points, render them as 'Free' / '$10' / '$10-$25'.
    Returns None if no usable prices."""
    nums = sorted({a for a in amounts if a is not None})
    if not nums:
        return None
    if len(nums) == 1 or nums[0] == nums[-1]:
        return _fmt_amount(nums[0], currency)
    if nums[0] == 0:
        # $0 - $25 reads cleaner as "Free-$25"
        return f"Free–{_fmt_amount(nums[-1], currency)}"
    return f"{_fmt_amount(nums[0], currency)}–{_fmt_amount(nums[-1], currency)}"


def price_from_offers(node: dict) -> str | None:
    """Pull a price string out of a schema.org Event node's `offers` field.

    Handles a single Offer dict, a list of Offers, or AggregateOffer with
    lowPrice/highPrice.
    """
    offers = node.get("offers")
    if not offers:
        return None
    if isinstance(offers, dict):
        offers = [offers]
    if not isinstance(offers, list):
        return None

    amounts: list[float] = []
    currency = "USD"
    for o in offers:
        if not isinstance(o, dict):
            continue
        cur = o.get("priceCurrency") or currency
        if isinstance(cur, str):
            currency = cur
        # AggregateOffer fields.
        for f in ("lowPrice", "highPrice", "price"):
            v = _to_float(o.get(f))
            if v is not None:
                amounts.append(v)
        # priceSpecification (sometimes a list).
        ps = o.get("priceSpecification")
        if isinstance(ps, dict):
            ps = [ps]
        if isinstance(ps, list):
            for p in ps:
                if isinstance(p, dict):
                    v = _to_float(p.get("price"))
                    if v is not None:
                        amounts.append(v)
    return format_price_range(amounts, currency)


# Best-effort regex for "Free" / "$10" / "$10 cover" / "presale $20 / door $25"
# in unstructured text (iCal DESCRIPTION fields, etc.).
_PRICE_RE = re.compile(
    r"\$\s?(\d{1,3}(?:\.\d{1,2})?)|\b(no\s+cover|free\s+entry|free)\b",
    re.IGNORECASE,
)


def price_from_text(text: str | None) -> str | None:
    """Crude extraction from event description text. Picks up dollar amounts
    and 'free' / 'no cover'. Returns 'Free', '$N', or '$N-$M'."""
    if not text:
        return None
    amounts: list[float] = []
    has_free_word = False
    for m in _PRICE_RE.finditer(text):
        if m.group(1):
            try:
                amounts.append(float(m.group(1)))
            except ValueError:
                pass
        elif m.group(2):
            has_free_word = True
    if has_free_word and not amounts:
        return "Free"
    return format_price_range(amounts)
