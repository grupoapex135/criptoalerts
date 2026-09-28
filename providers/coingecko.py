from __future__ import annotations
import re
from typing import Any
from config import settings
from analysis.params import CACHE_TTL, HISTORY_DAYS
from providers.base import cache, http_get_json


class CoinGeckoClient:
    def __init__(self):
        self.base = settings.coingecko_base_url.rstrip("/")
        self.headers = {}
        if settings.coingecko_api_key:
            # CoinGecko demo key header. Pro users can change this to x-cg-pro-api-key.
            self.headers["x-cg-demo-api-key"] = settings.coingecko_api_key

    def _get(self, path: str, params: dict | None = None) -> Any:
        return http_get_json(f"{self.base}{path}", params=params, headers=self.headers)

    def markets(self, limit: int = 200) -> list[dict[str, Any]]:
        per_page = min(limit, 250)
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": per_page,
            "page": 1,
            # 7d hourly prices, used to give the AI a real 7d range.
            "sparkline": "true",
            "price_change_percentage": "1h,24h,7d,30d",
        }
        return cache.get_or_set(f"cg:markets:{per_page}", CACHE_TTL["coingecko_markets"],
                                lambda: self._get("/coins/markets", params))

    def global_data(self) -> dict[str, Any]:
        """Total market cap, 24h change and dominance."""
        return cache.get_or_set("cg:global", CACHE_TTL["coingecko_global"],
                                lambda: self._get("/global").get("data") or {})

    def daily_history(self, coin_id: str, days: int = HISTORY_DAYS) -> dict[str, list[float]]:
        """
        Daily closes and market caps (oldest first). The last point is the
        current price. Cached for hours: daily candles barely move within the window.
        """
        def fetch():
            raw = self._get(f"/coins/{coin_id}/market_chart",
                            {"vs_currency": "usd", "days": days, "interval": "daily"})
            return {
                "prices": [p[1] for p in raw.get("prices") or [] if p and p[1] is not None],
                "market_caps": [m[1] for m in raw.get("market_caps") or [] if m and m[1] is not None],
            }
        return cache.get_or_set(f"cg:history:{coin_id}:{days}", CACHE_TTL["coingecko_history"], fetch)

    def coin_profile(self, coin_id: str) -> dict[str, Any]:
        """Official description and categories: the raw material for the 'thesis and utility'."""
        def fetch():
            raw = self._get(f"/coins/{coin_id}", {
                "localization": "false", "tickers": "false", "market_data": "false",
                "community_data": "false", "developer_data": "false", "sparkline": "false",
            })
            text = re.sub(r"<[^>]+>", "", (raw.get("description") or {}).get("en") or "")
            return {
                "description": " ".join(text.split())[:700] or None,
                "categories": [c for c in raw.get("categories") or [] if c][:8],
            }
        return cache.get_or_set(f"cg:profile:{coin_id}", CACHE_TTL["coingecko_profile"], fetch)
