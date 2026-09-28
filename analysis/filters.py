"""Cheap eliminatory filters: run on every coin, no extra API calls."""
from __future__ import annotations
import re
from typing import Any
from config import settings
from providers.base import to_float

STABLES = {
    "usdt","usdc","dai","usde","fdusd","tusd","usds","pyusd","usdd","frax","gusd","lusd",
    "usd1","usdg","rlusd","usdgo","usdf","bfusd","gho","usd0","usdtb","crvusd","ausd",
    "usdai","usx","u","eurc","eurcv","eursafo","a7a5","reusd","apxusd","sofid",
    "jpyc","zchf","eure",
}
# Not crypto-native assets that the wider top-1000 universe brings in: tokenized stocks
# and ETFs (Ondo, xStock, Robinhood), wrapped/bridged/staked versions, commodity and
# fiat-pegged tokens. Checked against the real top-1000 list.
NOT_CRYPTO_NATIVE = re.compile(
    r"tokeni[sz]ed|xstock|robinhood token|\bwrapped\b|\bbridged\b|\bstaked\b|restaked|liquid staking|"
    r"\bgold\b|\bsilver\b|\bsavings?\b|\btreasury\b|reinsurance|\bsynth usd|\busd\d*\b|"
    r"\b(jpy|eur|chf|gbp|brl|cny|krw)\b|emoney|stablecoin", re.I)
# Tokenized gold/silver and treasury/RWA funds: pegged to an off-chain asset.
PEGGED = {"xaut","paxg","kau","kag","buidl","usyc","usdy","ustb","eutbl","ousg","jtrsy","jaaa","ylds"}
WRAPPED = {
    "wbtc","weth","steth","wsteth","weeth","cbeth","reth","wbeth","cbbtc","lbtc","solvbtc",
    "jitosol","msol","bnsol","wbnb","rseth","ezeth","meth","susde","susds","tbtc",
}


def min_market_cap(mode: str) -> float:
    return settings.pre_listing_min_mcap_usd if mode == "pre_listing" else settings.min_market_cap_usd


def min_volume_for(mode: str) -> float:
    return settings.pre_listing_min_volume_usd if mode == "pre_listing" else settings.min_daily_volume_usd


def _looks_pegged(price: float | None, p7: float | None, p30: float | None) -> bool:
    # Catches USD stablecoins that are not in the list yet.
    if price is None or p7 is None or p30 is None:
        return False
    return 0.97 <= price <= 1.03 and abs(p7) < 2 and abs(p30) < 3


def prefilter(coin: dict[str, Any], mode: str = "binance") -> str | None:
    """Returns the rejection reason, or None when the coin stays in the funnel."""
    symbol = str(coin.get("symbol") or "").lower()
    if symbol in STABLES or symbol in WRAPPED or symbol in PEGGED:
        return "stable/wrapped/pegged asset"
    if NOT_CRYPTO_NATIVE.search(f"{coin.get('name') or ''} {coin.get('id') or ''}"):
        return "tokenized, wrapped or pegged asset"
    p7 = to_float(coin.get("price_change_percentage_7d_in_currency"))
    p30 = to_float(coin.get("price_change_percentage_30d_in_currency"))
    if _looks_pegged(to_float(coin.get("current_price")), p7, p30):
        return "looks like a pegged asset"

    mcap = to_float(coin.get("market_cap")) or 0
    volume = to_float(coin.get("total_volume")) or 0
    fdv = to_float(coin.get("fully_diluted_valuation")) or 0
    min_mcap, min_volume = min_market_cap(mode), min_volume_for(mode)
    if mcap < min_mcap:
        return "market cap below threshold"
    if mode == "pre_listing" and mcap > settings.pre_listing_max_mcap_usd:
        return "market cap above pre-listing range"
    if volume < min_volume:
        return "volume below threshold"
    if fdv and mcap and fdv / mcap > settings.max_fdv_to_mcap_ratio:
        return "FDV dilution too high"
    return None
