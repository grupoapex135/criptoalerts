"""
Market regime: reads the whole market before any single asset, so a general
sell-off never looks like an individual opportunity. risk_off does not stop
the radar; it raises the bar (see REGIME_ADJUSTMENTS).
"""
from __future__ import annotations
from typing import Any
from analysis.params import REGIME, REGIME_ADJUSTMENTS
from analysis.trend import compute_trend
from providers.base import to_float


def _pct(v: float | None) -> str:
    return f"{v:+.1f}%" if v is not None else "?"


def build_market_regime(btc: dict[str, Any] | None, eth: dict[str, Any] | None,
                        global_data: dict[str, Any] | None,
                        btc_closes: list[float] | None,
                        fear_greed: dict[str, Any] | None = None) -> dict[str, Any]:
    if not btc:
        return {
            "status": "neutral", "available": False,
            "reason": "Dados do BTC indisponíveis; critérios normais.",
            "adjustments": REGIME_ADJUSTMENTS["neutral"],
        }

    btc_price = to_float(btc.get("current_price")) or 0
    btc_24h = to_float(btc.get("price_change_percentage_24h_in_currency"))
    btc_7d = to_float(btc.get("price_change_percentage_7d_in_currency"))
    btc_30d = to_float(btc.get("price_change_percentage_30d_in_currency"))
    eth = eth or {}
    eth_price = to_float(eth.get("current_price"))
    eth_7d = to_float(eth.get("price_change_percentage_7d_in_currency"))
    eth_30d = to_float(eth.get("price_change_percentage_30d_in_currency"))

    eth_btc_ratio = round(eth_price / btc_price, 6) if eth_price and btc_price else None
    # ETH/BTC change derived from both 30d changes: exact, no extra history needed.
    eth_btc_30d = (round(((1 + eth_30d / 100) / (1 + btc_30d / 100) - 1) * 100, 2)
                   if eth_30d is not None and btc_30d is not None else None)

    g = global_data or {}
    total_mcap = to_float((g.get("total_market_cap") or {}).get("usd"))
    total_mcap_24h = to_float(g.get("market_cap_change_percentage_24h_usd"))

    btc_trend = compute_trend(btc_closes, btc_price, btc_24h, btc_7d, btc_30d)
    r = REGIME

    if btc_7d is None:
        short = "flat"
    elif btc_7d >= r["short_up_btc_7d"]:
        short = "up"
    elif btc_7d <= r["short_down_btc_7d"]:
        short = "down"
    else:
        short = "flat"

    dist_ma50 = btc_trend.get("dist_ma50_pct")
    below_ma50 = dist_ma50 is not None and dist_ma50 <= r["below_ma50_pct"]
    above_ma50 = dist_ma50 is not None and dist_ma50 > 0
    if (btc_30d is not None and btc_30d <= r["mid_down_btc_30d"]) or below_ma50:
        intermediate = "down"
    elif btc_30d is not None and btc_30d >= r["mid_up_btc_30d"] and (above_ma50 or dist_ma50 is None):
        intermediate = "up"
    else:
        intermediate = "flat"

    reasons = []
    if btc_7d is not None and btc_7d <= r["risk_off_btc_7d"]:
        reasons.append(f"BTC {_pct(btc_7d)} em 7d")
    if btc_30d is not None and btc_30d <= r["risk_off_btc_30d"]:
        reasons.append(f"BTC {_pct(btc_30d)} em 30d")
    if total_mcap_24h is not None and total_mcap_24h <= r["risk_off_total_mcap_24h"]:
        reasons.append(f"mercado total {_pct(total_mcap_24h)} em 24h")
    if short == "down" and intermediate == "down":
        reasons.append("BTC em queda no curto e no médio prazo")

    if reasons:
        status = "risk_off"
        reason = "Mercado em risco: " + "; ".join(reasons) + "."
    elif short != "down" and intermediate == "up" and (btc_24h is None or btc_24h > -5):
        status = "risk_on"
        reason = f"BTC {_pct(btc_7d)} em 7d e {_pct(btc_30d)} em 30d, acima da média de 50 dias."
    else:
        status = "neutral"
        reason = f"BTC {_pct(btc_7d)} em 7d e {_pct(btc_30d)} em 30d, sem tendência clara."

    return {
        "status": status,
        "available": True,
        "btc_price": btc_price,
        "btc_24h": btc_24h,
        "btc_7d": btc_7d,
        "btc_30d": btc_30d,
        "btc_trend": btc_trend.get("state"),
        "btc_dist_ma50_pct": dist_ma50,
        "eth_7d": eth_7d,
        "eth_30d": eth_30d,
        "eth_btc_ratio": eth_btc_ratio,
        "eth_btc_30d_pct": eth_btc_30d,
        "total_market_cap_usd": total_mcap,
        "total_market_cap_24h_pct": total_mcap_24h,
        "short_term": short,
        "intermediate_term": intermediate,
        # Sentiment is context for the reader and the AI; it does not move the status.
        "fear_greed": (fear_greed or {}).get("value"),
        "fear_greed_label": (fear_greed or {}).get("label"),
        "reason": reason,
        "adjustments": REGIME_ADJUSTMENTS[status],
    }
