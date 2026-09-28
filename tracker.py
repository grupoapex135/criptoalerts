"""
Signal tracking: did each alert reach its target or its invalidation first?

Binance-listed assets use Binance 4h candles since the alert (a wick between
checks still counts). Pre-Binance assets use CoinGecko hourly prices, checked
every few hours to respect the free quota, and are watched for the moment they
get listed on Binance spot. Rules that keep the score honest:
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

from analysis.params import PRE_LISTING_TRACK_EVERY_HOURS
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


_last_checked: dict[str, datetime] = {}


def _candles(opp: dict[str, Any], binance, coingecko, now: datetime) -> list[dict[str, float]] | None:
    since_ms = int(opp["detected_at"].timestamp() * 1000)
    if opp.get("mode") != "pre_listing":
        return binance.klines(f"{opp['symbol']}USDT", since_ms, interval="4h")
    # Pre-Binance: CoinGecko prices, only every few hours (free quota).
    last = _last_checked.get(opp["id"])
    if last and now - last < timedelta(hours=PRE_LISTING_TRACK_EVERY_HOURS):
        return None
    _last_checked[opp["id"]] = now
    days = (now - opp["detected_at"]).days + 1
    return [c for c in coingecko.price_series(opp["coin_id"], days) if c["open_time"] >= since_ms]


def check_open(db, binance, coingecko=None, now: datetime | None = None) -> list[dict[str, Any]]:
    """
    Updates every OPEN signal. Returns events: {"event": "closed", ...} when a signal
    reaches its target/invalidation/expiry and {"event": "listed", ...} when a
    pre-Binance asset shows up on Binance spot.
    """
    now = now or datetime.now(timezone.utc)
    events = []
    for opp in db.open_opportunities():
        if opp.get("target_usd") is None or opp.get("invalidation_usd") is None or not opp.get("detected_at"):
            continue
        if opp.get("mode") == "pre_listing" and not opp.get("binance_listed_at"):
            try:
                if binance.spot_pair(opp["symbol"]):
                    db.mark_listed(opp["id"])
                    events.append({**opp, "event": "listed"})
            except Exception as exc:
                log.warning("tracking %s: checagem de listagem falhou (%s)", opp["symbol"], exc)
        if opp.get("mode") == "pre_listing" and not (coingecko and opp.get("coin_id")):
            continue
        try:
            candles = _candles(opp, binance, coingecko, now)
        except Exception as exc:
            log.warning("tracking %s: preços indisponíveis (%s)", opp["symbol"], exc)
            continue
        if candles is None:
            continue
        status, close_price, entered = evaluate(opp, candles, now)
        if status == OPEN:
            continue
        pct = result_pct(entry_price(opp), close_price) if entered else None
        db.close_opportunity(opp["id"], status, close_price, pct)
        events.append({**opp, "event": "closed", "status": status, "close_price": close_price,
                       "result_pct": pct, "entered": entered})
    return events


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
    pre = [r for r in rows if r.get("mode") == "pre_listing"]
    return {
        "all": summarize(rows), "recent": summarize(recent), "days": days,
        "pre_listing": {"total": len(pre), "listed": sum(1 for r in pre if r.get("binance_listed_at"))},
    }
