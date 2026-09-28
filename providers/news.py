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
from providers.base import MISS, KeyedProvider, ProviderError, cache, http_get_json

BASE = "https://newsdata.io/api/1/crypto"
BATCH = 5  # coins per request (API maximum): one credit covers several finalists
UNSUPPORTED_TTL = 24 * 3600


class NewsClient(KeyedProvider):
    source = name = "newsdata"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else settings.newsdata_api_key
        self.blocked = None

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
        # Coins NewsData does not know are remembered, so they never cost a credit again.
        missing = [s for s in missing if cache.get(f"news:unsupported:{s}", UNSUPPORTED_TTL) is MISS]
        for i in range(0, len(missing), BATCH):
            batch = missing[i:i + BATCH]
            payload = self._fetch_batch(batch)
            grouped = self.normalize(payload, batch)
            for s in batch:
                out[s] = grouped.get(s, [])
                cache.set(f"news:{s}", out[s])
        return out

    def _fetch_batch(self, batch: list[str]) -> dict[str, Any]:
        """One unknown coin makes NewsData reject the whole batch (HTTP 422, invalid_coin):
        drop it and retry with the rest."""
        batch = list(batch)
        while batch:
            # Key in a header, not the URL: request errors quote the URL in logs/Telegram.
            params = {"coin": ",".join(b.lower() for b in batch), "language": "en"}
            try:
                return self.guard(lambda: http_get_json(BASE, params=params, headers={"X-ACCESS-KEY": self.api_key}))
            except ProviderError as exc:
                # Several unknown coins come comma-separated: "fluid,hkd".
                raw = str(((exc.body or {}).get("results") or {}).get("invalid_coin") or "")
                invalid = {c.strip().upper() for c in raw.split(",") if c.strip()} & set(batch)
                if "HTTP 422" not in str(exc) or not invalid:
                    raise
                for coin in invalid:
                    cache.set(f"news:unsupported:{coin}", True)
                batch = [b for b in batch if b not in invalid]
        return {"results": []}

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
