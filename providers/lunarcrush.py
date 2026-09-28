"""
LunarCrush API v4 — social context. Auth: Authorization: Bearer <key>.
One call lists every coin (galaxy score, sentiment, dominance, topic); a
second per finalist reads the hourly topic series to compare the last 24h
with the 24h before. Topic endpoints need the Individual/Builder plans.
"""
from __future__ import annotations
from typing import Any
from config import settings
from analysis.params import CACHE_TTL, MIN_INTERVAL_S
from providers.base import RateLimiter, cache, http_get_json, pct_change, to_float

BASE = "https://lunarcrush.com/api4"
_limiter = RateLimiter(MIN_INTERVAL_S["lunarcrush"])


class LunarCrushClient:
    source = "lunarcrush"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else settings.lunarcrush_api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict | None = None) -> Any:
        _limiter.wait()
        payload = http_get_json(f"{BASE}{path}", params=params,
                                headers={"Authorization": f"Bearer {self.api_key}"}, retries=1)
        return payload.get("data") if isinstance(payload, dict) else payload

    def coins(self) -> list[dict[str, Any]]:
        return cache.get_or_set("lunarcrush:coins", CACHE_TTL["social"],
                                lambda: self._get("/public/coins/list/v2", {"limit": 1000}) or [])

    def social(self, symbol: str, market_cap: float | None) -> dict[str, Any] | None:
        same = [c for c in self.coins() if str(c.get("symbol") or "").upper() == symbol.upper()]
        if not same:
            return None
        # Tickers repeat; the one closest to CoinGecko's market cap is ours.
        coin = min(same, key=lambda c: abs((to_float(c.get("market_cap")) or 0) - (market_cap or 0)))
        topic = coin.get("topic") or symbol.lower()

        def fetch():
            series = self._get(f"/public/topic/{topic}/time-series/v2", {"bucket": "hour", "interval": "1w"}) or []
            return self.normalize(coin, series)
        return cache.get_or_set(f"lunarcrush:topic:{topic}", CACHE_TTL["social"], fetch)

    @staticmethod
    def normalize(coin: dict[str, Any], series: list[dict[str, Any]]) -> dict[str, Any]:
        rows = sorted(series or [], key=lambda r: r.get("time") or 0)
        last24, prev24 = rows[-24:], rows[-48:-24]

        def total(rs, field):
            vals = [to_float(r.get(field)) for r in rs]
            vals = [v for v in vals if v is not None]
            return sum(vals) if vals else None

        return {
            "source": "lunarcrush",
            "mentions_24h": total(last24, "posts_active") or to_float(coin.get("social_volume_24h")),
            "mentions_change_24h": pct_change(total(last24, "posts_active"), total(prev24, "posts_active")),
            "engagement_24h": total(last24, "interactions") or to_float(coin.get("interactions_24h")),
            "engagement_change_24h": pct_change(total(last24, "interactions"), total(prev24, "interactions")),
            "unique_creators": to_float((last24[-1] if last24 else {}).get("contributors_active")),
            "sentiment": to_float(coin.get("sentiment")),
            "social_dominance": to_float(coin.get("social_dominance")),
            "galaxy_score": to_float(coin.get("galaxy_score")),
            "trending": None,
        }
