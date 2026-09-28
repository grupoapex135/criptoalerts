"""
Signal tracking: did each alert reach its target or its invalidation first?

Uses Binance 1h candles since the alert, so a wick that touched a level
between checks still counts. Rules that keep the score honest:
- a signal only counts after price touches the entry zone (a move straight to
  the target without an entry was never a trade);
- in the candle that enters the zone only a loss can be confirmed, and a candle
  touching both levels counts as INVALIDATED (order inside it is unknown);
- candles after the expiry date are ignored; EXPIRED closes at the expiry price.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from config import settings

log = logging.getLogger(__name__)

OPEN, TARGET_HIT, INVALIDATED, EXPIRED = "OPEN", "TARGET_HIT", "INVALIDATED", "EXPIRED"


def expiry_of(opp: dict[str, Any]) -> datetime:
    return opp.get("expires_at") or (opp["detected_at"] + timedelta(days=settings.opportunity_expiry_days))


def evaluate(opp: dict[str, Any], candles: list[dict[str, float]], now: datetime) -> tuple[str, float | None, bool]:
    """Returns (status, close_price, entered). OPEN means nothing decided yet."""
    target = float(opp["target_usd"])
    invalidation = float(opp["invalidation_usd"])
    entry_max = opp.get("entry_max_usd")
    expires = expiry_of(opp)
    expires_ms = expires.timestamp() * 1000
    entered = entry_max is None
    last_close = None
    for c in candles:
        if c["open_time"] >= expires_ms:
            break
        last_close = c["close"]
        if c["low"] <= invalidation and (entered or c["low"] <= float(entry_max)):
            return INVALIDATED, invalidation, True
        if not entered:
            entered = c["low"] <= float(entry_max)
            continue  # entry candle: a win inside it can't be confirmed
        if c["high"] >= target:
            return TARGET_HIT, target, True
    if now >= expires:
        return EXPIRED, last_close, entered
    return OPEN, None, entered


def entry_price(opp: dict[str, Any]) -> float | None:
    """Middle of the entry zone; the alert price when there is no zone."""
    lo, hi = opp.get("entry_min_usd"), opp.get("entry_max_usd")
    if lo is not None and hi is not None:
        return (float(lo) + float(hi)) / 2
    return opp.get("price_usd")


def result_pct(entry: float | None, close_price: float | None) -> float | None:
    if not entry or close_price is None:
        return None
    return round((close_price / float(entry) - 1) * 100, 2)


def check_open(db, binance, now: datetime | None = None) -> list[dict[str, Any]]:
    """Updates every OPEN signal; returns the ones that changed status."""
    now = now or datetime.now(timezone.utc)
    changed = []
    for opp in db.open_opportunities():
        if opp.get("target_usd") is None or opp.get("invalidation_usd") is None or not opp.get("detected_at"):
            continue
        pair = f"{opp['symbol']}USDT"
        try:
            candles = binance.klines(pair, int(opp["detected_at"].timestamp() * 1000))
        except Exception as exc:
            log.warning("tracking %s: velas indisponíveis (%s)", pair, exc)
            continue
        status, close_price, entered = evaluate(opp, candles, now)
        if status == OPEN:
            continue
        pct = result_pct(entry_price(opp), close_price) if entered else None
        db.close_opportunity(opp["id"], status, close_price, pct)
        changed.append({**opp, "status": status, "close_price": close_price, "result_pct": pct, "entered": entered})
    return changed


def stats(rows: list[dict[str, Any]], now: datetime | None = None, days: int = 30) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)

    def summarize(rs):
        counts = {s: sum(1 for r in rs if r.get("status") == s) for s in (OPEN, TARGET_HIT, INVALIDATED, EXPIRED)}
        decided = counts[TARGET_HIT] + counts[INVALIDATED]
        return {
            "total": len(rs),
            **counts,
            # Expired signals are neither wins nor losses; they are shown, not hidden.
            "win_rate": round(counts[TARGET_HIT] / decided * 100) if decided else None,
        }

    since = now - timedelta(days=days)
    recent = [r for r in rows if r.get("detected_at") and r["detected_at"] >= since]
    return {"all": summarize(rows), "recent": summarize(recent), "days": days}
