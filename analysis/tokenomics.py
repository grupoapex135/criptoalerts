"""
Tokenomics: supply structure (CoinGecko, always), trailing supply growth
(from daily market caps / prices), upcoming unlocks (Tokenomist, optional)
and value capture (DefiLlama holders revenue, when present).
"""
from __future__ import annotations
from typing import Any
from analysis.params import DILUTION
from providers.base import to_float


def supply_growth_30d(market_caps: list[float] | None, prices: list[float] | None) -> float | None:
    """Circulating supply ≈ market cap / price; compares today with ~30 days ago."""
    if not market_caps or not prices or len(market_caps) < 31 or len(prices) < 31:
        return None
    try:
        now = market_caps[-1] / prices[-1]
        before = market_caps[-31] / prices[-31]
    except ZeroDivisionError:
        return None
    if before <= 0:
        return None
    return round((now / before - 1) * 100, 2)


def dilution_level(pct_30d: float | None) -> str | None:
    if pct_30d is None:
        return None
    if pct_30d >= DILUTION["critical_from"]:
        return "critical"
    if pct_30d >= DILUTION["medium_below"]:
        return "high"
    if pct_30d >= DILUTION["low_below"]:
        return "medium"
    return "low"


def build_tokenomics(coin: dict[str, Any], supply_growth: float | None,
                     unlocks: dict[str, Any] | None, holders_revenue_30d: float | None) -> dict[str, Any]:
    circ = to_float(coin.get("circulating_supply"))
    total = to_float(coin.get("total_supply"))
    max_supply = to_float(coin.get("max_supply"))
    mcap = to_float(coin.get("market_cap"))
    fdv = to_float(coin.get("fully_diluted_valuation"))
    denom = max_supply or total
    unlocks = unlocks or {}

    unlock_30d = unlocks.get("unlock_30d_pct")
    # Scheduled cliffs (Tokenomist) and dilution already happening (trailing supply
    # growth, which also captures linear emissions) both count: the worse one wins.
    candidates = []
    if unlock_30d is not None:
        candidates.append((unlock_30d, "unlock_schedule"))
    if supply_growth is not None:
        candidates.append((max(supply_growth, 0), "trailing_supply_growth"))
    if candidates:
        worst, basis = max(candidates, key=lambda c: c[0])
        risk = dilution_level(worst)
    else:
        risk, basis = None, None

    return {
        "tokenomics_available": bool(unlocks.get("available")),
        "source": unlocks.get("source"),
        "circulating_supply": circ,
        "total_supply": total,
        "max_supply": max_supply,
        "circulating_pct": round(circ / denom * 100, 2) if circ and denom else None,
        "fdv_mcap_ratio": round(fdv / mcap, 3) if fdv and mcap else None,
        "supply_growth_30d": supply_growth,
        "unlock_7d_pct": unlocks.get("unlock_7d_pct"),
        "unlock_30d_pct": unlock_30d,
        "next_unlock_date": unlocks.get("next_unlock_date"),
        "next_unlock_pct": unlocks.get("next_unlock_pct"),
        "unlock_recipients": unlocks.get("unlock_recipients") or [],
        "vesting_schedule": (unlocks.get("vesting_schedule") or [])[:5],
        "dilution_risk": risk,
        "dilution_basis": basis,
        "value_capture": value_capture(holders_revenue_30d),
    }


def value_capture(holders_revenue_30d: float | None) -> dict[str, Any]:
    # Only a structured source counts. Absence here is "unknown", not "no capture".
    if holders_revenue_30d and holders_revenue_30d > 0:
        return {
            "available": True,
            "type": "holders_revenue",
            "holders_revenue_30d": holders_revenue_30d,
            "note": "receita repassada a holders (buyback, burn ou distribuição) segundo o DefiLlama",
        }
    return {"available": False, "type": None}


def tokenomics_score(t: dict[str, Any]) -> float | None:
    s = 50.0
    evidence = 0
    cp = t.get("circulating_pct")
    if cp is not None:
        evidence += 1
        s += 12 if cp >= 80 else (5 if cp >= 60 else (-12 if cp < 40 else 0))
    ratio = t.get("fdv_mcap_ratio")
    if ratio is not None:
        evidence += 1
        s += 12 if ratio <= 1.25 else (5 if ratio <= 1.75 else (-12 if ratio > 2.5 else 0))
    risk = t.get("dilution_risk")
    if risk is not None:
        evidence += 1
        s += {"low": 10, "medium": 0, "high": -20, "critical": -40}[risk]
    if (t.get("value_capture") or {}).get("available"):
        s += 8
    if not evidence:
        return None
    return float(max(0, min(100, s)))
