from __future__ import annotations
import json
from typing import Any
from config import settings
from analysis.params import CACHE_TTL
from providers.base import cache, http_get_json, ProviderError


class BinanceClient:
    """Binance spot public market data (read-only, no account key)."""

    def __init__(self):
        self.base = settings.binance_base_url.rstrip("/")

    def _load_symbols(self) -> set[str]:
        # Refreshed periodically so new listings/delistings are picked up.
        def fetch():
            payload = http_get_json(
                f"{self.base}/api/v3/exchangeInfo",
                params={"symbolStatus": "TRADING", "showPermissionSets": "false"},
            )
            return {s["symbol"] for s in payload.get("symbols", []) if s.get("status") == "TRADING"}
        return cache.get_or_set("binance:symbols", CACHE_TTL["binance_symbols"], fetch)

    def spot_pair(self, base_symbol: str) -> str | None:
        pair = f"{base_symbol.upper()}USDT"
        return pair if pair in self._load_symbols() else None

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": data["symbol"],
            "last_price": float(data["lastPrice"]),
            "quote_volume": float(data["quoteVolume"]),
            "price_change_pct_24h": float(data["priceChangePercent"]),
        }

    def ticker(self, pair: str) -> dict[str, Any] | None:
        try:
            data = http_get_json(f"{self.base}/api/v3/ticker/24hr", params={"symbol": pair})
        except ProviderError as exc:
            if "HTTP 400" in str(exc):
                return None  # unknown/invalid symbol
            # Other failures (outage, 429, 451 geo-block) must surface, not look like "no pair".
            raise
        return self._normalize(data)

    def tickers(self, pairs: list[str]) -> dict[str, dict[str, Any]]:
        """24h tickers for many pairs in one request."""
        if not pairs:
            return {}
        try:
            data = http_get_json(f"{self.base}/api/v3/ticker/24hr",
                                 params={"symbols": json.dumps(pairs, separators=(",", ":"))})
        except ProviderError as exc:
            if "HTTP 400" not in str(exc):
                raise
            # One pair delisted since the cached symbol list: the whole batch is
            # rejected. Fall back to one request per pair instead of failing the scan.
            return {p: t for p in pairs if (t := self.ticker(p))}
        return {d["symbol"]: self._normalize(d) for d in data}

    def klines(self, pair: str, start_ms: int, interval: str = "1h", limit: int = 1000) -> list[dict[str, float]]:
        """Candles since start_ms, oldest first: open_time, high, low, close."""
        raw = http_get_json(f"{self.base}/api/v3/klines",
                            params={"symbol": pair, "interval": interval, "startTime": start_ms, "limit": limit})
        return [{"open_time": int(k[0]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4])} for k in raw]
