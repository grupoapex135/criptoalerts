"""
News headlines via NewsData.io crypto endpoint (free tier: 200 credits/day,
articles ~12h delayed). CryptoPanic was the first choice but no longer has a
free API plan. Headlines are only context; time-critical risk comes from
DefiLlama hacks, which need no key.
"""
from __future__ import annotations
from typing import Any
from config import settings
from analysis.params import CACHE_TTL
from providers.base import MISS, cache, http_get_json

BASE = "https://newsdata.io/api/1/crypto"
BATCH = 5  # coins per request: one credit covers several finalists


class NewsClient:
    source = "newsdata"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else settings.newsdata_api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def headlines(self, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Recent headlines per symbol (uppercase keys). Uncached symbols are fetched in batches."""
        symbols = sorted({s.upper() for s in symbols})
        out: dict[str, list[dict[str, Any]]] = {}
        missing = []
        for s in symbols:
            hit = cache.get(f"news:{s}", CACHE_TTL["news"])
            if hit is MISS:
                missing.append(s)
            else:
                out[s] = hit
        for i in range(0, len(missing), BATCH):
            batch = missing[i:i + BATCH]
            # Key in a header, not the URL: request errors quote the URL in logs/Telegram.
            payload = http_get_json(BASE, params={"coin": ",".join(b.lower() for b in batch), "language": "en"},
                                    headers={"X-ACCESS-KEY": self.api_key})
            grouped = self.normalize(payload, batch)
            for s in batch:
                out[s] = grouped.get(s, [])
                cache.set(f"news:{s}", out[s])
        return out

    @staticmethod
    def normalize(payload: dict[str, Any], symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {s.upper(): [] for s in symbols}
        for item in payload.get("results") or []:
            coins = {str(c).upper() for c in item.get("coin") or []}
            article = {
                "title": item.get("title"),
                "published_at": item.get("pubDate"),
                "source": item.get("source_name") or item.get("source_id"),
                "url": item.get("link"),
            }
            for s in coins & grouped.keys():
                grouped[s].append(article)
        return grouped
