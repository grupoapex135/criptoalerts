from __future__ import annotations
import time
import requests
from typing import Any
from config import settings

TIMEOUT = 20

class CoinGeckoClient:
    def __init__(self):
        self.base = settings.coingecko_base_url.rstrip("/")
        self.headers = {}
        if settings.coingecko_api_key:
            # CoinGecko demo key header. Pro users can change this to x-cg-pro-api-key.
            self.headers["x-cg-demo-api-key"] = settings.coingecko_api_key

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
        r = requests.get(
            f"{self.base}/coins/markets",
            params=params,
            headers=self.headers,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()


class DefiLlamaClient:
    """
    Uses DefiLlama's public protocol dataset for TVL/category context.

    Matching rules (checked against the live dataset):
    - CEX entries are excluded: their "TVL" is exchange reserves
      (e.g. "Binance CEX" uses symbol BNB).
    - A protocol whose gecko_id points to another coin is a ticker collision.
    - Versions of the same protocol (Aave V2/V3/V4...) share a parentProtocol
      and are summed; the family with the largest TVL wins.
    - Families below MIN_TVL_USD are treated as no data, which drops tiny
      collisions such as "Solana Farm" (symbol SOL).
    """
    BASE = "https://api.llama.fi"
    CACHE_TTL_SECONDS = 3600
    EXCLUDED_CATEGORIES = {"CEX"}
    MIN_TVL_USD = 5_000_000

    def __init__(self):
        self._protocols: list[dict[str, Any]] | None = None
        self._fetched_at = 0.0

    def protocols(self) -> list[dict[str, Any]]:
        if self._protocols is None or time.time() - self._fetched_at > self.CACHE_TTL_SECONDS:
            r = requests.get(f"{self.BASE}/protocols", timeout=TIMEOUT)
            r.raise_for_status()
            self._protocols = r.json()
            self._fetched_at = time.time()
        return self._protocols

    @staticmethod
    def _weighted_change(members: list[dict[str, Any]], field: str) -> float | None:
        # Aggregate % change of the family: current TVL vs implied previous TVL.
        now = prev = 0.0
        for p in members:
            tvl = float(p.get("tvl") or 0)
            change = p.get(field)
            if tvl <= 0 or change is None or float(change) <= -100:
                continue
            now += tvl
            prev += tvl / (1 + float(change) / 100)
        if prev <= 0:
            return None
        return round((now / prev - 1) * 100, 2)

    def protocol_context(self, coin_id: str, symbol: str,
                         protocols: list[dict[str, Any]]) -> dict[str, Any] | None:
        symbol = symbol.upper()
        families: dict[str, list[dict[str, Any]]] = {}
        for p in protocols:
            if p.get("category") in self.EXCLUDED_CATEGORIES:
                continue
            gecko_id = p.get("gecko_id")
            same_coin = gecko_id == coin_id
            same_symbol = str(p.get("symbol") or "").upper() == symbol and not gecko_id
            if not (same_coin or same_symbol):
                continue
            key = p.get("parentProtocol") or p.get("id") or p.get("name")
            families.setdefault(str(key), []).append(p)

        if not families:
            return None

        members = max(families.values(), key=lambda ms: sum(float(m.get("tvl") or 0) for m in ms))
        tvl = sum(float(m.get("tvl") or 0) for m in members)
        if tvl < self.MIN_TVL_USD:
            return None

        top = max(members, key=lambda m: float(m.get("tvl") or 0))
        chains = sorted({c for m in members for c in (m.get("chains") or [])})
        return {
            "name": top.get("name"),
            "symbol": top.get("symbol"),
            "category": top.get("category"),
            "protocols_in_family": len(members),
            "tvl": round(tvl, 2),
            "change_1d": self._weighted_change(members, "change_1d"),
            "change_7d": self._weighted_change(members, "change_7d"),
            "chains": chains[:15],
            "slug": top.get("slug"),
        }


class BinanceClient:
    SYMBOLS_TTL_SECONDS = 6 * 3600

    def __init__(self):
        self.base = settings.binance_base_url.rstrip("/")
        self._symbols: set[str] | None = None
        self._fetched_at = 0.0

    def _load_symbols(self) -> set[str]:
        # Refreshed periodically so new listings/delistings are picked up.
        if self._symbols is None or time.time() - self._fetched_at > self.SYMBOLS_TTL_SECONDS:
            r = requests.get(
                f"{self.base}/api/v3/exchangeInfo",
                params={"symbolStatus": "TRADING", "showPermissionSets": "false"},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            payload = r.json()
            self._symbols = {
                s["symbol"]
                for s in payload.get("symbols", [])
                if s.get("status") == "TRADING"
            }
            self._fetched_at = time.time()
        return self._symbols

    def spot_pair(self, base_symbol: str) -> str | None:
        pair = f"{base_symbol.upper()}USDT"
        return pair if pair in self._load_symbols() else None

    def ticker(self, pair: str) -> dict[str, Any] | None:
        r = requests.get(
            f"{self.base}/api/v3/ticker/24hr",
            params={"symbol": pair},
            timeout=TIMEOUT,
        )
        if r.status_code == 400:
            # Unknown/invalid symbol.
            return None
        # Other failures (outage, 429, 451 geo-block) must surface, not look like "no pair".
        r.raise_for_status()
        data = r.json()
        return {
            "symbol": pair,
            "last_price": float(data["lastPrice"]),
            "quote_volume": float(data["quoteVolume"]),
            "price_change_pct_24h": float(data["priceChangePercent"]),
        }
