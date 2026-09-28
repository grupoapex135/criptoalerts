from __future__ import annotations
import re
from typing import Any
from config import settings
from analysis.params import CACHE_TTL, HISTORY_DAYS, MAJOR_CEX
from providers.base import cache, http_get_json


DEX_KEYWORDS = ("uniswap", "pancakeswap", "raydium", "orca", "meteora", "aerodrome", "velodrome",
                "sushiswap", "curve", "balancer", "camelot", "quickswap", "trader_joe", "traderjoe",
                "jupiter", "pumpswap", "dex", "swap")


def _is_dex(identifier: str, name: str, ticker: dict[str, Any]) -> bool:
    # DEX tickers quote contract addresses as base/target, and DEX names are recognizable.
    base, target = str(ticker.get("base") or ""), str(ticker.get("target") or "")
    looks_like_address = any(x.startswith("0X") or len(x) > 30 for x in (base, target))
    return looks_like_address or any(k in f"{identifier} {name}".lower() for k in DEX_KEYWORDS)


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
        """Top `limit` coins by market cap, paged 250 at a time (each page cached)."""
        coins: list[dict[str, Any]] = []
        page = 1
        while len(coins) < limit:
            batch = self._markets_page(page)
            coins.extend(batch)
            if len(batch) < 250:
                break
            page += 1
        return coins[:limit]

    def _markets_page(self, page: int) -> list[dict[str, Any]]:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "page": page,
            # 7d hourly prices, used to give the AI a real 7d range.
            "sparkline": "true",
            "price_change_percentage": "1h,24h,7d,30d",
        }
        return cache.get_or_set(f"cg:markets:p{page}", CACHE_TTL["coingecko_markets"],
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
        """
        Official description and categories (raw material for the thesis), where the
        coin trades (tickers) and its contract addresses. One call, cached 24h.
        """
        def fetch():
            raw = self._get(f"/coins/{coin_id}", {
                "localization": "false", "tickers": "true", "market_data": "false",
                "community_data": "false", "developer_data": "false", "sparkline": "false",
            })
            return self.normalize_profile(raw)
        return cache.get_or_set(f"cg:profile:{coin_id}", CACHE_TTL["coingecko_profile"], fetch)

    @staticmethod
    def normalize_profile(raw: dict[str, Any]) -> dict[str, Any]:
        text = re.sub(r"<[^>]+>", "", (raw.get("description") or {}).get("en") or "")
        cex: dict[str, float] = {}
        dex: dict[str, float] = {}
        for t in raw.get("tickers") or []:
            if t.get("is_anomaly") or t.get("is_stale"):
                continue
            market = t.get("market") or {}
            name, ident = market.get("name") or "", market.get("identifier") or ""
            volume = float((t.get("converted_volume") or {}).get("usd") or 0)
            bucket = dex if _is_dex(ident, name, t) else cex
            label = MAJOR_CEX.get(ident, name) if bucket is cex else name
            bucket[label] = bucket.get(label, 0) + volume
        by_volume = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: kv[1], reverse=True)]  # noqa: E731
        return {
            "description": " ".join(text.split())[:700] or None,
            "categories": [c for c in raw.get("categories") or [] if c],
            "cex": by_volume(cex),
            "cex_ids": sorted({(t.get("market") or {}).get("identifier") for t in raw.get("tickers") or []} - {None}),
            "dex": by_volume(dex),
            "platforms": {k: v for k, v in (raw.get("platforms") or {}).items() if k and v},
        }

    def price_series(self, coin_id: str, days: int) -> list[dict[str, float]]:
        """Price points since `days` ago (hourly up to 90 days), as candles with high=low=close."""
        raw = self._get(f"/coins/{coin_id}/market_chart", {"vs_currency": "usd", "days": max(1, days)})
        return [{"open_time": int(ts), "high": p, "low": p, "close": p}
                for ts, p in raw.get("prices") or [] if p is not None]
