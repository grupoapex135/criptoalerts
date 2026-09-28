from __future__ import annotations
from typing import Any
from config import settings
from analysis.params import CACHE_TTL
from providers.base import cache, http_get_json, pct_change, to_float, ProviderError


class BinanceFuturesClient:
    """
    Free derivatives source: Binance USDⓈ-M public endpoints (no key).
    Used when COINGLASS_API_KEY is not set. Binance-only (not cross-exchange)
    and without liquidations, which the public API no longer exposes.
    """
    source = "binance_futures"

    def __init__(self):
        self.base = settings.binance_futures_base_url.rstrip("/")

    def _get(self, path: str, params: dict) -> Any:
        return http_get_json(f"{self.base}{path}", params=params, retries=1)

    def derivatives(self, symbol: str) -> dict[str, Any] | None:
        symbol = symbol.upper()

        def fetch():
            # Low-priced coins trade as 1000x contracts (1000PEPEUSDT). OI in USD,
            # funding and long/short ratio are the same regardless of the multiplier.
            return self._fetch(f"{symbol}USDT") or self._fetch(f"1000{symbol}USDT")
        return cache.get_or_set(f"bnf:{symbol}", CACHE_TTL["derivatives"], fetch)

    def _fetch(self, pair: str) -> dict[str, Any] | None:
        try:
            premium = self._get("/fapi/v1/premiumIndex", {"symbol": pair})
        except ProviderError as exc:
            if "HTTP 400" in str(exc):
                return None  # no perpetual for this coin
            raise
        oi_hist = self._get("/futures/data/openInterestHist", {"symbol": pair, "period": "1h", "limit": 25})
        ratio = self._get("/futures/data/globalLongShortAccountRatio", {"symbol": pair, "period": "1h", "limit": 1})
        ticker = self._get("/fapi/v1/ticker/24hr", {"symbol": pair})
        return self.normalize(premium, oi_hist, ratio, ticker)

    @staticmethod
    def normalize(premium: dict, oi_hist: list[dict], ratio: list[dict], ticker: dict) -> dict[str, Any]:
        oi = [to_float(p.get("sumOpenInterestValue")) for p in oi_hist or []]
        oi = [v for v in oi if v is not None]  # oldest first, hourly

        def change(hours: int) -> float | None:
            return pct_change(oi[-1], oi[-1 - hours]) if len(oi) > hours else None

        funding = to_float(premium.get("lastFundingRate"))
        return {
            "source": "binance_futures",
            "open_interest_usd": round(oi[-1], 2) if oi else None,
            "oi_change_1h": change(1),
            "oi_change_4h": change(4),
            "oi_change_24h": change(24),
            # lastFundingRate is a fraction per 8h (0.0001 = 0.01%)
            "funding_rate_pct": round(funding * 100, 4) if funding is not None else None,
            "liquidations_24h_usd": None,
            "long_short_ratio": to_float((ratio or [{}])[0].get("longShortRatio")) if ratio else None,
            "futures_volume_24h_usd": to_float(ticker.get("quoteVolume")),
        }
