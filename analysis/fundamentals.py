"""
Fundamentals: protocol or chain usage from DefiLlama (TVL, fees, revenue,
holders revenue, DEX volume, borrowed) and price-vs-usage divergences.
Missing metrics stay None; a coin with no DefiLlama footprint gets None
for the whole layer, never a low score.
"""
from __future__ import annotations
from typing import Any


def build_fundamentals(ctx: dict[str, Any] | None, flows: dict[str, dict | None],
                       tvl_change_30d: float | None, market_cap: float | None,
                       p30: float | None) -> dict[str, Any]:
    if not ctx:
        return {"available": False}

    fees = flows.get("fees") or {}
    revenue = flows.get("revenue") or {}
    holders = flows.get("holders_revenue") or {}
    dex = flows.get("dex_volume") or {}

    revenue_30d = revenue.get("total_30d")
    annual_revenue = revenue_30d * 365 / 30 if revenue_30d else None
    mcap_to_revenue = round(market_cap / annual_revenue, 1) if market_cap and annual_revenue else None

    f = {
        "available": True,
        "kind": ctx.get("kind"),
        "name": ctx.get("name"),
        "category": ctx.get("category"),
        "tvl": ctx.get("tvl"),
        "tvl_change_7d": ctx.get("change_7d"),
        "tvl_change_30d": tvl_change_30d,
        "borrowed": ctx.get("borrowed"),
        "fees_7d": fees.get("total_7d"),
        "fees_30d": fees.get("total_30d"),
        "fees_growth_30d": fees.get("growth_30d"),
        # DefiLlama "revenue" = what the protocol keeps (protocol revenue).
        "revenue_7d": revenue.get("total_7d"),
        "revenue_30d": revenue_30d,
        "revenue_growth_30d": revenue.get("growth_30d"),
        "holders_revenue_30d": holders.get("total_30d"),
        "dex_volume_30d": dex.get("total_30d"),
        "dex_volume_growth_30d": dex.get("growth_30d"),
        "perps_volume_30d": None,  # DefiLlama perps overview requires the paid plan
        "market_cap_to_annual_revenue": mcap_to_revenue,
    }
    f["divergences"] = divergences(f, p30)
    return f


def divergences(f: dict[str, Any], p30: float | None) -> list[str]:
    if p30 is None:
        return []
    out = []
    rev_g = f.get("revenue_growth_30d")
    usage = [g for g in (f.get("fees_growth_30d"), f.get("dex_volume_growth_30d")) if g is not None]
    tvl_g = f.get("tvl_change_30d") if f.get("tvl_change_30d") is not None else f.get("tvl_change_7d")
    if p30 <= -10 and rev_g is not None and rev_g >= 10:
        out.append("price_down_revenue_up")
    if p30 <= -10 and usage and max(usage) >= 10:
        out.append("price_down_usage_up")
    if abs(p30) <= 8 and tvl_g is not None and tvl_g >= 10:
        out.append("price_flat_tvl_up")
    if p30 >= 20 and rev_g is not None and rev_g <= -20:
        out.append("price_up_revenue_down")
    return out


POSITIVE_DIVERGENCES = {"price_down_revenue_up", "price_down_usage_up", "price_flat_tvl_up"}


def fundamentals_score(f: dict[str, Any]) -> float | None:
    if not f.get("available"):
        return None
    s = 50.0
    tvl = f.get("tvl") or 0
    if tvl >= 100_000_000:
        s += 5
    c7, c30 = f.get("tvl_change_7d"), f.get("tvl_change_30d")
    if c7 is not None:
        s += 5 if c7 >= 8 else (-10 if c7 <= -10 else 0)
    if c30 is not None:
        s += 8 if c30 >= 15 else (-12 if c30 <= -20 else 0)
    fg = f.get("fees_growth_30d")
    if fg is not None:
        s += 8 if fg >= 20 else (-10 if fg <= -30 else 0)
    if f.get("revenue_30d"):
        s += 4
    rg = f.get("revenue_growth_30d")
    if rg is not None:
        s += 8 if rg >= 20 else (-8 if rg <= -30 else 0)
    if f.get("holders_revenue_30d"):
        s += 5
    ps = f.get("market_cap_to_annual_revenue")
    if ps is not None:
        s += 10 if ps <= 15 else (5 if ps <= 40 else (-5 if ps >= 300 else 0))
    divs = f.get("divergences") or []
    s += min(12, 6 * sum(1 for d in divs if d in POSITIVE_DIVERGENCES))
    if "price_up_revenue_down" in divs:
        s -= 8
    return float(max(0, min(100, s)))
