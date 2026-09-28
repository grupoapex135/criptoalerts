from __future__ import annotations
from typing import Any
from config import settings

STABLES = {
    "usdt","usdc","dai","usde","fdusd","tusd","usds","pyusd","usdd","frax","gusd","lusd",
    "usd1","usdg","rlusd","usdgo","usdf","bfusd","gho","usd0","usdtb","crvusd","ausd",
    "usdai","usx","u","eurc","eurcv","eursafo","a7a5","reusd","apxusd","sofid",
}
# Tokenized gold/silver and treasury/RWA funds: pegged to an off-chain asset.
PEGGED = {"xaut","paxg","kau","kag","buidl","usyc","usdy","ustb","eutbl","ousg","jtrsy","jaaa","ylds"}
WRAPPED = {
    "wbtc","weth","steth","wsteth","weeth","cbeth","reth","wbeth","cbbtc","lbtc","solvbtc",
    "jitosol","msol","bnsol","wbnb","rseth","ezeth","meth","susde","susds","tbtc",
}

def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _opt(v) -> float | None:
    # Missing data stays None: a missing 7d change must not score as "consolidation".
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _looks_pegged(price: float | None, p7: float | None, p30: float | None) -> bool:
    # Catches USD stablecoins that are not in the list yet.
    if price is None or p7 is None or p30 is None:
        return False
    return 0.97 <= price <= 1.03 and abs(p7) < 2 and abs(p30) < 3

def score_market_candidate(coin: dict[str, Any]) -> tuple[float, list[str]]:
    symbol = str(coin.get("symbol") or "").lower()
    if symbol in STABLES or symbol in WRAPPED or symbol in PEGGED:
        return 0, ["stable/wrapped/pegged asset"]

    mcap = _num(coin.get("market_cap"))
    volume = _num(coin.get("total_volume"))
    fdv = _num(coin.get("fully_diluted_valuation"))
    p24 = _opt(coin.get("price_change_percentage_24h_in_currency"))
    p7 = _opt(coin.get("price_change_percentage_7d_in_currency"))
    p30 = _opt(coin.get("price_change_percentage_30d_in_currency"))

    if _looks_pegged(_opt(coin.get("current_price")), p7, p30):
        return 0, ["looks like a pegged asset"]
    if mcap < settings.min_market_cap_usd:
        return 0, ["market cap below threshold"]
    if volume < settings.min_daily_volume_usd:
        return 0, ["volume below threshold"]

    if fdv and mcap and (fdv / mcap) > settings.max_fdv_to_mcap_ratio:
        return 0, ["FDV dilution too high"]

    score = 35.0
    reasons: list[str] = []

    # Prefer liquid assets.
    volume_ratio = volume / mcap if mcap else 0
    if volume_ratio >= 0.10:
        score += 15
        reasons.append("strong liquidity")
    elif volume_ratio >= 0.04:
        score += 8
        reasons.append("healthy liquidity")

    # Look for pullbacks rather than vertical pumps.
    if p7 is not None:
        if -25 <= p7 <= -3:
            score += 16
            reasons.append("7d pullback")
        elif -3 < p7 <= 5:
            score += 8
            reasons.append("7d consolidation")

    if p30 is not None:
        if -35 <= p30 <= 8:
            score += 8
            reasons.append("30d not extended")
        elif p30 > 35:
            score -= 18
            reasons.append("30d price extended")

    if p24 is not None:
        if p24 < -5:
            score += 6
            reasons.append("short-term reset")
        elif p24 > 15:
            score -= 12
            reasons.append("24h overheated")

    if fdv and mcap:
        ratio = fdv / mcap
        if ratio <= 1.25:
            score += 12
            reasons.append("low dilution gap")
        elif ratio <= 1.75:
            score += 5
            reasons.append("moderate dilution gap")

    return max(0.0, min(100.0, score)), reasons

def enrich_with_defi(score: float, defi: dict[str, Any] | None) -> tuple[float, list[str]]:
    reasons = []
    if not defi:
        return score, reasons

    tvl = _num(defi.get("tvl"))
    # DefiLlama /protocols has no 1-month change, so trend signals use 7d.
    c7 = _opt(defi.get("change_7d"))

    if tvl >= 100_000_000:
        score += 7
        reasons.append("meaningful protocol TVL")
    if c7 is not None and c7 >= 8:
        score += 8
        reasons.append("TVL growing 7d")
    if c7 is not None and c7 <= -10:
        score -= 12
        reasons.append("TVL contracting 7d")

    return max(0.0, min(100.0, score)), reasons
