"""
CoinGlass API v4 — cross-exchange derivatives. Auth: header CG-API-KEY.
Uses per-coin endpoints available from the Hobbyist plan (30 req/min),
reading the aggregated "All" exchange row where the API provides one.
Funding comes in percent (0.01 = 0.01% per 8h) per the docs' examples.
"""
from __future__ import annotations
from typing import Any
from config import settings
from analysis.params import CACHE_TTL, MIN_INTERVAL_S
from providers.base import RateLimiter, ProviderError, cache, http_get_json, to_float

BASE = "https://open-api-v4.coinglass.com"
_limiter = RateLimiter(MIN_INTERVAL_S["coinglass"])


def _all_row(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    rows = rows or []
    return next((r for r in rows if str(r.get("exchange")).lower() == "all"), rows[0] if rows else {})


class CoinGlassClient:
    source = "coinglass"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else settings.coinglass_api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict) -> Any:
        _limiter.wait()
        payload = http_get_json(f"{BASE}{path}", params=params, headers={"CG-API-KEY": self.api_key}, retries=1)
        if str(payload.get("code")) != "0":
            raise ProviderError(f"coinglass {path}: {payload.get('msg')}")
        return payload.get("data")

    def derivatives(self, symbol: str) -> dict[str, Any] | None:
        symbol = symbol.upper()
        return cache.get_or_set(f"coinglass:{symbol}", CACHE_TTL["derivatives"], lambda: self._fetch(symbol))

    def _fetch(self, symbol: str) -> dict[str, Any] | None:
        oi = self._get("/api/futures/open-interest/exchange-list", {"symbol": symbol})
        if not oi:
            return None
        funding = self._get("/api/futures/funding-rate/oi-weight-history",
                            {"symbol": symbol, "interval": "4h", "limit": 1})
        liq = self._get("/api/futures/liquidation/exchange-list", {"symbol": symbol, "range": "24h"})
        ratio = self._get("/api/futures/global-long-short-account-ratio/history",
                          {"exchange": "Binance", "symbol": f"{symbol}USDT", "interval": "4h", "limit": 1})
        return self.normalize(oi, funding, liq, ratio)

    @staticmethod
    def normalize(oi: list[dict], funding: list[dict] | None, liq: list[dict] | None,
                  ratio: list[dict] | None) -> dict[str, Any]:
        o = _all_row(oi)
        last_funding = (funding or [{}])[-1] if funding else {}
        last_ratio = (ratio or [{}])[-1] if ratio else {}
        return {
            "source": "coinglass",
            "open_interest_usd": to_float(o.get("open_interest_usd")),
            "oi_change_1h": to_float(o.get("open_interest_change_percent_1h")),
            "oi_change_4h": to_float(o.get("open_interest_change_percent_4h")),
            "oi_change_24h": to_float(o.get("open_interest_change_percent_24h")),
            "funding_rate_pct": to_float(last_funding.get("close")),
            "liquidations_24h_usd": to_float(_all_row(liq).get("liquidation_usd")) if liq else None,
            "long_short_ratio": to_float(last_ratio.get("global_account_long_short_ratio")),
            "futures_volume_24h_usd": None,
        }
