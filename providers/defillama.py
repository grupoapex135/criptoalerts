from __future__ import annotations
from typing import Any
from analysis.params import CACHE_TTL
from providers.base import cache, http_get_json, pct_change, to_float

OVERVIEW_PARAMS = {"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true"}


class DefiLlamaClient:
    """
    DefiLlama public API: protocol TVL, chain TVL, fees/revenue/holders revenue,
    DEX volume and hacks. No key needed.

    Protocol matching rules (checked against the live dataset):
    - CEX entries are excluded: their "TVL" is exchange reserves
      (e.g. "Binance CEX" uses symbol BNB).
    - A protocol whose gecko_id points to another coin is a ticker collision.
    - Versions of the same protocol (Aave V2/V3/V4...) share a parentProtocol
      and are summed; the family with the largest TVL wins.
    - Families below MIN_TVL_USD are treated as no data, which drops tiny
      collisions such as "Solana Farm" (symbol SOL).
    """
    BASE = "https://api.llama.fi"
    EXCLUDED_CATEGORIES = {"CEX"}
    MIN_TVL_USD = 5_000_000
    MAX_GROWTH_RATIO = 10.0
    OVERVIEWS = {
        "fees": ("/overview/fees", {}),
        "revenue": ("/overview/fees", {"dataType": "dailyRevenue"}),
        "holders_revenue": ("/overview/fees", {"dataType": "dailyHoldersRevenue"}),
        "dex_volume": ("/overview/dexs", {}),
    }

    # ------------------------------------------------------------ raw datasets
    def protocols(self) -> list[dict[str, Any]]:
        return cache.get_or_set("llama:protocols", CACHE_TTL["defillama_protocols"],
                                lambda: http_get_json(f"{self.BASE}/protocols"))

    def chains(self) -> list[dict[str, Any]]:
        return cache.get_or_set("llama:chains", CACHE_TTL["defillama_chains"],
                                lambda: http_get_json(f"{self.BASE}/v2/chains"))

    def overview(self, kind: str) -> list[dict[str, Any]]:
        path, extra = self.OVERVIEWS[kind]
        return cache.get_or_set(
            f"llama:overview:{kind}", CACHE_TTL["defillama_overviews"],
            lambda: http_get_json(f"{self.BASE}{path}", params={**OVERVIEW_PARAMS, **extra}).get("protocols") or [],
        )

    def hacks(self) -> list[dict[str, Any]]:
        return cache.get_or_set("llama:hacks", CACHE_TTL["defillama_hacks"],
                                lambda: http_get_json(f"{self.BASE}/hacks"))

    def tvl_series(self, kind: str, slug: str) -> list[float]:
        """Daily TVL values, oldest first. Protocol payloads are large, so deep stage only."""
        def fetch():
            if kind == "chain":
                raw = http_get_json(f"{self.BASE}/v2/historicalChainTvl/{slug}", timeout=20, retries=1)
                return [float(p["tvl"]) for p in raw if p.get("tvl") is not None]
            raw = http_get_json(f"{self.BASE}/protocol/{slug}", timeout=25, retries=0)
            return [float(p["totalLiquidityUSD"]) for p in raw.get("tvl") or []
                    if p.get("totalLiquidityUSD") is not None]
        return cache.get_or_set(f"llama:tvl:{kind}:{slug}", CACHE_TTL["defillama_tvl_history"], fetch)

    # ------------------------------------------------------------ matching
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

        family_key, members = max(
            families.items(), key=lambda kv: sum(float(m.get("tvl") or 0) for m in kv[1]))
        tvl = sum(float(m.get("tvl") or 0) for m in members)
        if tvl < self.MIN_TVL_USD:
            return None

        top = max(members, key=lambda m: float(m.get("tvl") or 0))
        chains = sorted({c for m in members for c in (m.get("chains") or [])})
        borrowed = sum(float((m.get("chainTvls") or {}).get("borrowed") or 0) for m in members)
        parent = family_key if family_key.startswith("parent#") else None
        return {
            "kind": "protocol",
            "name": top.get("name"),
            "symbol": top.get("symbol"),
            "category": top.get("category"),
            "protocols_in_family": len(members),
            "tvl": round(tvl, 2),
            "borrowed": round(borrowed, 2) or None,
            "change_1d": self._weighted_change(members, "change_1d"),
            "change_7d": self._weighted_change(members, "change_7d"),
            "chains": chains[:15],
            "slug": parent.split("#", 1)[1] if parent else top.get("slug"),
            "description": (top.get("description") or "")[:500] or None,
            # join keys for fees/revenue/hacks
            "member_ids": sorted(str(m.get("id")) for m in members if m.get("id") is not None),
            "parent_key": parent,
        }

    def chain_context(self, coin_id: str, chains: list[dict[str, Any]]) -> dict[str, Any] | None:
        chain = next((c for c in chains if c.get("gecko_id") == coin_id), None)
        if not chain or float(chain.get("tvl") or 0) < self.MIN_TVL_USD:
            return None
        return {
            "kind": "chain",
            "name": chain.get("name"),
            "symbol": chain.get("tokenSymbol"),
            "category": "Chain",
            "tvl": round(float(chain["tvl"]), 2),
            "slug": chain.get("name"),
            "member_ids": [],
            "parent_key": None,
        }

    @staticmethod
    def _matches(entry: dict[str, Any], ctx: dict[str, Any]) -> bool:
        if ctx["kind"] == "chain":
            return str(entry.get("defillamaId") or "").startswith("chain#") and entry.get("name") == ctx["name"]
        return (str(entry.get("defillamaId")) in ctx["member_ids"]
                or (ctx["parent_key"] is not None and entry.get("parentProtocol") == ctx["parent_key"]))

    def flow_metrics(self, ctx: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Sums 30d and previous-30d totals of an overview (fees, revenue...) for the family."""
        matched = [e for e in entries if self._matches(e, ctx)]
        if not matched:
            return None
        total_30d = sum(to_float(e.get("total30d")) or 0 for e in matched)
        prev_30d = sum(to_float(e.get("total60dto30d")) or 0 for e in matched)
        total_7d = sum(to_float(e.get("total7d")) or 0 for e in matched)
        # A protocol that just started being tracked shows +2000% "growth"; that is
        # missing history, not usage. Above MAX_GROWTH_RATIO the growth is unknown.
        reliable = prev_30d > 0 and total_30d / prev_30d <= self.MAX_GROWTH_RATIO
        return {
            "total_30d": round(total_30d, 2),
            "total_7d": round(total_7d, 2),
            "growth_30d": pct_change(total_30d, prev_30d) if reliable else None,
        }

    def recent_hacks(self, ctx: dict[str, Any] | None, name: str, since_ts: float,
                     hacks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = {name.lower()}
        if ctx:
            names.add(str(ctx.get("name") or "").lower())
        ids = set(ctx["member_ids"]) if ctx else set()
        out = []
        for h in hacks:
            if float(h.get("date") or 0) < since_ts:
                continue
            same_id = h.get("defillamaId") is not None and str(h["defillamaId"]) in ids
            hack_name = str(h.get("name") or "").lower()
            same_name = any(n and (hack_name == n or hack_name.startswith(n + " ")) for n in names)
            if same_id or same_name:
                out.append({
                    "name": h.get("name"),
                    "date": h.get("date"),
                    "amount_usd": h.get("amount"),
                    "technique": h.get("technique"),
                    "returned_funds": h.get("returnedFunds"),
                })
        return out
