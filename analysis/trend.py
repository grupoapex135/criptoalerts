"""
Trend quality: tells a healthy pullback apart from a structural decline.
Uses daily closes (MA20/MA50, 30/90d range). Without history it falls back
to the 7d/30d changes and says so in `source`.
"""
from __future__ import annotations
from statistics import mean
from typing import Any
from analysis.params import TREND


def _pos(price: float, low: float, high: float) -> float | None:
    return round((price - low) / (high - low), 3) if high > low else None


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI on the given closes (daily or weekly)."""
    if len(closes) < period + 1:
        return None
    deltas = [b - a for a, b in zip(closes[:-1], closes[1:])]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain, avg_loss = mean(gains[:period]), mean(losses[:period])
    for g, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 1)


def ma_cross(closes: list[float]) -> str | None:
    """MA50 vs MA200 today and `cross_lookback_days` ago: golden/death cross or side."""
    lb = TREND["cross_lookback_days"]
    if len(closes) < 200:
        return None
    above_now = mean(closes[-50:]) > mean(closes[-200:])
    if len(closes) >= 200 + lb:
        above_before = mean(closes[-50 - lb:-lb]) > mean(closes[-200 - lb:-lb])
        if above_now and not above_before:
            return "golden_cross_recent"
        if not above_now and above_before:
            return "death_cross_recent"
    return "above" if above_now else "below"


def compute_trend(closes: list[float] | None, price: float,
                  p24: float | None = None, p7: float | None = None, p30: float | None = None) -> dict[str, Any]:
    closes = [c for c in (closes or []) if c]
    if price <= 0 and closes:
        price = closes[-1]

    if len(closes) < 20 or price <= 0:
        return _fallback(p24, p7, p30)

    ma20 = mean(closes[-20:])
    ma50 = mean(closes[-50:]) if len(closes) >= 50 else None
    ma200 = mean(closes[-200:]) if len(closes) >= 200 else None
    weekly = closes[::-7][::-1]  # every 7th close, ending today
    last30, last90 = closes[-30:], closes[-90:]
    high_30d, low_30d = max(last30), min(last30)
    dist_ma20 = round((price / ma20 - 1) * 100, 2)
    dist_ma50 = round((price / ma50 - 1) * 100, 2) if ma50 else None
    drawdown_30d = round((price / high_30d - 1) * 100, 2)

    t = TREND
    if dist_ma50 is not None and dist_ma50 <= t["capitulation_dist_ma50"] and (
            (p7 is not None and p7 <= t["capitulation_p7"]) or drawdown_30d <= t["capitulation_drawdown_30d"]):
        state = "CAPITULATION"
    elif ma50 and ((price < ma50 and ma20 < ma50) or dist_ma50 <= t["downtrend_dist_ma50"]):
        state = "DOWNTREND"
    elif ma50 and price > ma50 and ma20 > ma50:
        state = "UPTREND"
    else:
        state = "NEUTRAL"

    return {
        "source": "daily_history",
        "state": state,
        "ma20": round(ma20, 8),
        "ma50": round(ma50, 8) if ma50 else None,
        "dist_ma20_pct": dist_ma20,
        "dist_ma50_pct": dist_ma50,
        "high_30d": high_30d,
        "low_30d": low_30d,
        "drawdown_from_30d_high_pct": drawdown_30d,
        "range_position_30d": _pos(price, low_30d, high_30d),
        "range_position_90d": _pos(price, min(last90), max(last90)),
        "ma200": round(ma200, 8) if ma200 else None,
        "dist_ma200_pct": round((price / ma200 - 1) * 100, 2) if ma200 else None,
        "ma50_ma200": ma_cross(closes),
        "rsi_daily": rsi(closes),
        "rsi_weekly": rsi(weekly),
        "pullback_in_uptrend": state == "UPTREND" and (dist_ma20 <= 0 or (p7 is not None and p7 <= -3)),
        "extended": dist_ma20 >= t["extended_dist_ma20"] or _is_hot(p24, p30),
        "parabolic": dist_ma20 >= t["parabolic_dist_ma20"]
        or (dist_ma50 is not None and dist_ma50 >= t["parabolic_dist_ma50"]),
    }


def _is_hot(p24: float | None, p30: float | None) -> bool:
    return (p24 is not None and p24 > TREND["overheated_p24"]) or (p30 is not None and p30 > TREND["extended_p30"])


def _fallback(p24: float | None, p7: float | None, p30: float | None) -> dict[str, Any]:
    if p7 is None or p30 is None:
        state = None
    elif p30 <= -30 and p7 <= -15:
        state = "CAPITULATION"
    elif p30 <= -15:
        state = "DOWNTREND"
    elif p30 >= 10 and p7 >= -10:
        state = "UPTREND"
    else:
        state = "NEUTRAL"
    return {
        "source": "fallback_7d_30d" if state else None,
        "state": state,
        "pullback_in_uptrend": state == "UPTREND" and p7 is not None and p7 <= -3,
        "extended": _is_hot(p24, p30),
    }


def btc_relative(closes: list[float] | None, btc_closes: list[float] | None) -> dict[str, Any]:
    """
    The asset priced in BTC over the shared daily window: how discounted it is
    against Bitcoin (value framing), from data already fetched — no extra call.
    """
    n = min(len(closes or []), len(btc_closes or []))
    if n < 30:
        return {"available": False}
    ratio = [a / b for a, b in zip(closes[-n:], btc_closes[-n:]) if b]
    now, high, low = ratio[-1], max(ratio), min(ratio)
    ma50 = mean(ratio[-50:]) if len(ratio) >= 50 else None
    return {
        "available": True,
        "days": len(ratio),
        "ratio": round(now, 10),
        "from_high_pct": round((now / high - 1) * 100, 2),
        "range_position": _pos(now, low, high),
        "above_ma50": now > ma50 if ma50 else None,
    }


def trend_score(trend: dict[str, Any]) -> float | None:
    state = trend.get("state")
    if not state:
        return None
    if state == "UPTREND":
        score = 85 if trend.get("pullback_in_uptrend") else 70
    elif state == "NEUTRAL":
        pos = trend.get("range_position_30d")
        score = 65 if pos is not None and pos <= 0.35 else 55
    elif state == "DOWNTREND":
        score = 25
    else:  # CAPITULATION
        score = 20
    if trend.get("extended"):
        score -= 25
    if trend.get("parabolic"):
        score = min(score, 20)
    if trend.get("source") != "daily_history":
        score = min(score, 70)  # less evidence, less conviction
    return float(max(0, min(100, score)))
