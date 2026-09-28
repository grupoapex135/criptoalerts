"""
Subscores → composite. Each layer scores 0-100 or None (no data). The
composite averages only the layers with data, re-weighting the rest away:
missing data lowers confidence (data_coverage), never the score itself.
`risk` (0-100, higher = riskier) is reported apart from the average.
"""
from __future__ import annotations
from typing import Any
from analysis.params import MAX_COVERAGE_PENALTY, WEIGHTS
from analysis.derivatives import derivatives_score
from analysis.fundamentals import fundamentals_score
from analysis.listing import listing_score
from analysis.onchain import onchain_score
from analysis.social import social_score
from analysis.tokenomics import tokenomics_score
from analysis.trend import trend_score


def market_quality_score(market: dict[str, Any]) -> float:
    """Liquidity and size: can the asset absorb a position and exit cleanly?"""
    mcap = market.get("market_cap_usd") or 0
    volume = market.get("daily_volume_usd") or 0
    ratio = volume / mcap if mcap else 0
    s = 50.0
    s += 25 if ratio >= 0.10 else (15 if ratio >= 0.04 else (-20 if ratio < 0.01 else 0))
    s += 15 if mcap >= 1_000_000_000 else (5 if mcap >= 300_000_000 else 0)
    if volume >= 50_000_000:
        s += 10
    return float(max(0, min(100, s)))


def compute_risk(d: dict[str, Any]) -> float:
    regime = (d.get("market_regime") or {}).get("status")
    trend = d.get("trend") or {}
    tok = d.get("tokenomics") or {}
    der = d.get("derivatives") or {}
    soc = d.get("social") or {}
    cat = d.get("catalysts") or {}
    r = 20.0
    r += {"risk_off": 20, "neutral": 5}.get(regime, 0)
    r += {"DOWNTREND": 20, "CAPITULATION": 25}.get(trend.get("state"), 0)
    if trend.get("extended"):
        r += 10
    if trend.get("parabolic"):
        r += 10
    r += {"high": 15, "critical": 30}.get(tok.get("dilution_risk"), 0)
    if (tok.get("fdv_mcap_ratio") or 0) > 2:
        r += 10
    r += {"crowded_long": 15, "crowded_short": 5}.get(der.get("positioning"), 0)
    if soc.get("state") == "euphoric":
        r += 15
    r += min(20, 10 * len(cat.get("negative") or []))
    if cat.get("critical_risk"):
        r += 30
    contract = d.get("contract") or {}
    r += min(15, 5 * len(contract.get("warnings") or []))
    if (d.get("listing") or {}).get("dex_only"):
        r += 10
    high, low = trend.get("high_30d"), trend.get("low_30d")
    if high and low and high / low - 1 > 0.6:
        r += 10  # very wide 30d range = volatile
    return float(max(0, min(100, r)))


def risk_label(risk: float) -> str:
    return "baixo" if risk < 35 else ("medio" if risk < 60 else "alto")


def compute_scores(d: dict[str, Any], enabled_layers: set[str] | None = None) -> dict[str, Any]:
    subs = {
        "listing": listing_score(d.get("listing") or {}),
        "market_quality": market_quality_score(d.get("market") or {}),
        "trend_quality": trend_score(d.get("trend") or {}),
        "fundamentals": fundamentals_score(d.get("fundamentals") or {}),
        "tokenomics": tokenomics_score(d.get("tokenomics") or {}),
        "derivatives": derivatives_score(d.get("derivatives") or {}),
        "onchain": onchain_score(d.get("onchain") or {}),
        "social": social_score(d.get("social") or {}),
    }
    # Layers without any configured source don't count against coverage.
    enabled = enabled_layers if enabled_layers is not None else set(WEIGHTS)
    available = {k: v for k, v in subs.items() if v is not None}
    weight_sum = sum(WEIGHTS[k] for k in available)
    composite = round(sum(WEIGHTS[k] * v for k, v in available.items()) / weight_sum, 1) if weight_sum else 0.0
    enabled_weight = sum(WEIGHTS[k] for k in enabled) or 1
    coverage = round(sum(WEIGHTS[k] for k in available if k in enabled) / enabled_weight, 2)

    return {
        **{k: (round(v, 1) if v is not None else None) for k, v in subs.items()},
        "risk": compute_risk(d),
        "composite": composite,
        "data_coverage": min(1.0, coverage),
    }


def coverage_penalty(scores: dict[str, Any]) -> int:
    return round((1 - (scores.get("data_coverage") or 0)) * MAX_COVERAGE_PENALTY)
