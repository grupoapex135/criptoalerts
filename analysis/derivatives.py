"""
Derivatives positioning. The goal is to avoid late longs
(price +15%, OI surging, hot funding) and favour corrections where leverage
has already been flushed (OI down, funding normal).
"""
from __future__ import annotations
from typing import Any
from analysis.params import DERIVATIVES as D


def build_derivatives(raw: dict[str, Any] | None, p24: float | None, p7: float | None) -> dict[str, Any]:
    if not raw:
        return {"available": False, "positioning": None}
    funding = raw.get("funding_rate_pct")
    oi24 = raw.get("oi_change_24h")
    ratio = raw.get("long_short_ratio")

    if (funding is not None and funding >= D["crowded_long_funding_pct"]) or \
            (oi24 is not None and p24 is not None and oi24 >= D["oi_surge_24h_pct"] and p24 >= D["price_pump_24h_pct"]) or \
            (ratio is not None and ratio >= D["crowded_long_ratio"]):
        positioning = "crowded_long"
    elif (funding is not None and funding <= D["crowded_short_funding_pct"]) or \
            (ratio is not None and ratio <= D["crowded_short_ratio"]):
        positioning = "crowded_short"
    elif oi24 is not None and oi24 <= D["deleveraged_oi_24h_pct"] and \
            (funding is None or abs(funding) <= D["neutral_funding_abs_pct"]) and (p7 is None or p7 < 0):
        positioning = "deleveraged"
    elif funding is None and oi24 is None:
        positioning = None
    else:
        positioning = "healthy"

    return {
        "available": positioning is not None,
        "source": raw.get("source"),
        "open_interest_usd": raw.get("open_interest_usd"),
        "oi_change_1h": raw.get("oi_change_1h"),
        "oi_change_4h": raw.get("oi_change_4h"),
        "oi_change_24h": oi24,
        "funding_rate_pct": funding,
        "liquidations_24h_usd": raw.get("liquidations_24h_usd"),
        "long_short_ratio": ratio,
        "futures_volume_24h_usd": raw.get("futures_volume_24h_usd"),
        "positioning": positioning,
    }


def derivatives_score(d: dict[str, Any]) -> float | None:
    positioning = d.get("positioning")
    if not positioning:
        return None
    funding = d.get("funding_rate_pct")
    if funding is not None and funding >= D["extreme_funding_pct"]:
        return 10.0
    return {"deleveraged": 80.0, "healthy": 65.0, "crowded_short": 55.0, "crowded_long": 20.0}[positioning]


def is_overheated(d: dict[str, Any], p24: float | None) -> bool:
    funding, oi24 = d.get("funding_rate_pct"), d.get("oi_change_24h")
    return (p24 is not None and p24 >= D["veto_price_24h_pct"]
            and oi24 is not None and oi24 >= D["veto_oi_24h_pct"]
            and funding is not None and funding >= D["crowded_long_funding_pct"])
