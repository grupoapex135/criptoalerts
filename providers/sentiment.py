"""Crypto Fear & Greed Index (alternative.me). Free, no key, updated daily."""
from __future__ import annotations
from typing import Any
from analysis.params import CACHE_TTL
from providers.base import cache, http_get_json, to_float

URL = "https://api.alternative.me/fng/"
LABELS_PT = {
    "Extreme Fear": "Medo extremo", "Fear": "Medo", "Neutral": "Neutro",
    "Greed": "Ganância", "Extreme Greed": "Ganância extrema",
}


def fear_greed() -> dict[str, Any] | None:
    def fetch():
        row = (http_get_json(URL, params={"limit": 1}).get("data") or [{}])[0]
        value = to_float(row.get("value"))
        if value is None:
            return None
        label = row.get("value_classification")
        return {"value": int(value), "label": LABELS_PT.get(label, label)}
    return cache.get_or_set("fear_greed", CACHE_TTL["fear_greed"], fetch)
