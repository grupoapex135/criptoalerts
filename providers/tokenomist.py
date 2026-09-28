"""
Tokenomist API v5 (formerly TokenUnlocks) — upcoming cliff unlocks.
Auth: header x-api-key. Plans with API access start at Pro (~1,000 req/month),
hence the long cache TTLs. Only cliff unlocks are summed; linear emissions
(/v5/daily-emission) are not, and show up instead as trailing supply growth.
"""
from __future__ import annotations
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from config import settings
from analysis.params import CACHE_TTL, MIN_INTERVAL_S
from providers.base import RateLimiter, ProviderError, cache, http_get_json, to_float

BASE = "https://api.tokenomist.ai"
_limiter = RateLimiter(MIN_INTERVAL_S["tokenomist"])


class TokenomistClient:
    source = "tokenomist"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else settings.tokenomist_api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict | None = None) -> Any:
        _limiter.wait()
        payload = http_get_json(f"{BASE}{path}", params=params, headers={"x-api-key": self.api_key})
        if not payload.get("status", True):
            raise ProviderError(f"tokenomist {path}: {payload.get('errorMessage')}")
        return payload.get("data")

    def token_list(self) -> list[dict[str, Any]]:
        return cache.get_or_set("tokenomist:list", CACHE_TTL["tokenomist_tokens"],
                                lambda: self._get("/v5/token/list") or [])

    @staticmethod
    def resolve(tokens: list[dict[str, Any]], coin_id: str, symbol: str,
                circulating: float | None) -> dict[str, Any] | None:
        """Tokenomist has no CoinGecko id field: match by slug, then symbol + supply sanity."""
        exact = next((t for t in tokens if t.get("id") == coin_id), None)
        if exact:
            return exact
        same = [t for t in tokens if str(t.get("symbol") or "").upper() == symbol.upper()]
        if len(same) == 1 and not circulating:
            return same[0]
        if circulating:
            def gap(t):
                c = to_float(t.get("circulatingSupply"))
                return abs(math.log(c / circulating)) if c and c > 0 else float("inf")
            best = min(same, key=gap, default=None)
            if best is not None and gap(best) <= math.log(2):  # between 0.5x and 2x of CoinGecko's supply
                return best
        return None

    def unlocks(self, coin_id: str, symbol: str, circulating: float | None) -> dict[str, Any] | None:
        token = self.resolve(self.token_list(), coin_id, symbol, circulating)
        if not token:
            return None
        token_id = token["id"]

        def fetch():
            today = datetime.now(timezone.utc).date()
            events = self._get(f"/v5/unlock/events/{token_id}", {
                "start": today.isoformat(), "end": (today + timedelta(days=30)).isoformat(),
            }) or []
            circ = circulating or to_float(token.get("circulatingSupply"))
            return self.normalize(events, circ)
        return cache.get_or_set(f"tokenomist:unlocks:{token_id}", CACHE_TTL["tokenomics"], fetch)

    @staticmethod
    def normalize(events: list[dict[str, Any]], circulating: float | None,
                  now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        rows = []
        for e in events:
            cliff = e.get("cliffUnlocks") or {}
            amount = to_float(cliff.get("cliffAmount"))
            try:
                when = datetime.fromisoformat(str(e.get("unlockDate")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if amount is None or when < now:
                continue
            recipients = sorted({
                str(a.get("standardAllocationName") or a.get("allocationName"))
                for a in cliff.get("allocationBreakdown") or []
                if a.get("standardAllocationName") or a.get("allocationName")
            })
            rows.append({
                "date": when,
                "amount": amount,
                "value_usd": to_float(cliff.get("cliffValue")),
                "pct_circulating": round(amount / circulating * 100, 3) if circulating else None,
                "recipients": recipients,
            })
        rows.sort(key=lambda r: r["date"])

        def window(days: int) -> float | None:
            if not circulating:
                return None
            limit = now + timedelta(days=days)
            return round(sum(r["amount"] for r in rows if r["date"] <= limit) / circulating * 100, 3)

        nxt = rows[0] if rows else None
        return {
            "available": True,
            "source": "tokenomist",
            "unlock_7d_pct": window(7),
            "unlock_30d_pct": window(30),
            "next_unlock_date": nxt["date"].date().isoformat() if nxt else None,
            "next_unlock_pct": nxt["pct_circulating"] if nxt else None,
            "unlock_recipients": sorted({x for r in rows for x in r["recipients"]}),
            "vesting_schedule": [
                {"date": r["date"].date().isoformat(), "pct_circulating": r["pct_circulating"],
                 "value_usd": r["value_usd"], "recipients": r["recipients"]}
                for r in rows[:5]
            ],
        }
