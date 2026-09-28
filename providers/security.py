"""
Token contract security via GoPlus (free, no key). Only for tokens that live on
a smart-contract chain; native coins (BTC, SOL, ...) have no contract to check.
EVM chains and Solana use different endpoints and field names; both are
normalized to the same shape. Percent fields from GoPlus are fractions (0.05 = 5%).
"""
from __future__ import annotations
from typing import Any
from analysis.params import CACHE_TTL, MIN_INTERVAL_S
from providers.base import RateLimiter, cache, http_get_json, to_float

BASE = "https://api.gopluslabs.io/api/v1"
# CoinGecko platform id -> GoPlus chain id, in order of preference.
EVM_CHAINS = {
    "ethereum": "1", "binance-smart-chain": "56", "base": "8453", "arbitrum-one": "42161",
    "polygon-pos": "137", "optimistic-ethereum": "10", "avalanche": "43114",
}
_limiter = RateLimiter(MIN_INTERVAL_S["goplus"])


def pick_contract(platforms: dict[str, str]) -> tuple[str, str] | None:
    for chain in EVM_CHAINS:
        if platforms.get(chain):
            return chain, platforms[chain]
    if platforms.get("solana"):
        return "solana", platforms["solana"]
    return None


def token_security(platforms: dict[str, str]) -> dict[str, Any] | None:
    picked = pick_contract(platforms or {})
    if not picked:
        return None
    chain, address = picked

    def fetch():
        _limiter.wait()
        if chain == "solana":
            url, params = f"{BASE}/solana/token_security", {"contract_addresses": address}
        else:
            url, params = f"{BASE}/token_security/{EVM_CHAINS[chain]}", {"contract_addresses": address}
        payload = http_get_json(url, params=params)
        result = payload.get("result") or {}
        raw = result.get(address) or result.get(address.lower()) or next(iter(result.values()), None)
        if not raw:
            return None
        return normalize_solana(raw) if chain == "solana" else normalize_evm(raw)
    data = cache.get_or_set(f"goplus:{chain}:{address}", CACHE_TTL["contract_security"], fetch)
    return {**data, "chain": chain} if data else None


def _flag(v) -> bool:
    return str(v) == "1"


def _top_holders_pct(holders: list[dict[str, Any]]) -> float | None:
    # Real wallets only: contracts (pools, bridges) and locked holdings are excluded.
    wallets = [h for h in holders or [] if not h.get("is_contract") and not h.get("is_locked")]
    if not wallets:
        return None
    return round(sum(to_float(h.get("percent")) or 0 for h in wallets[:10]) * 100, 2)


def normalize_evm(raw: dict[str, Any]) -> dict[str, Any]:
    sell_tax = to_float(raw.get("sell_tax"))
    buy_tax = to_float(raw.get("buy_tax"))
    severe = [name for name, bad in (
        ("honeypot", _flag(raw.get("is_honeypot"))),
        ("cannot_sell_all", _flag(raw.get("cannot_sell_all"))),
        ("owner_can_change_balances", _flag(raw.get("owner_change_balance"))),
        ("hidden_owner", _flag(raw.get("hidden_owner"))),
        ("can_take_back_ownership", _flag(raw.get("can_take_back_ownership"))),
        ("tax_can_be_changed", _flag(raw.get("slippage_modifiable"))),
        ("high_sell_tax", sell_tax is not None and sell_tax > 0.10),
    ) if bad]
    warnings = [name for name, bad in (
        ("mintable", _flag(raw.get("is_mintable"))),
        ("transfers_can_be_paused", _flag(raw.get("transfer_pausable"))),
        ("blacklist_function", _flag(raw.get("is_blacklisted"))),
        ("upgradeable_proxy", _flag(raw.get("is_proxy"))),
        ("closed_source", str(raw.get("is_open_source")) == "0"),
    ) if bad]
    return {
        "severe": severe,
        "warnings": warnings,
        "buy_tax_pct": round(buy_tax * 100, 2) if buy_tax is not None else None,
        "sell_tax_pct": round(sell_tax * 100, 2) if sell_tax is not None else None,
        "holder_count": to_float(raw.get("holder_count")),
        "top10_holders_pct": _top_holders_pct(raw.get("holders")),
    }


def normalize_solana(raw: dict[str, Any]) -> dict[str, Any]:
    def status(key):
        return _flag((raw.get(key) or {}).get("status")) if isinstance(raw.get(key), dict) else _flag(raw.get(key))
    severe = [name for name, bad in (
        ("freeze_authority", status("freezable")),
        ("owner_can_change_balances", status("balance_mutable_authority")),
        ("non_transferable", _flag(raw.get("non_transferable"))),
        ("closable", status("closable")),
    ) if bad]
    warnings = [name for name, bad in (
        ("mintable", status("mintable")),
        ("metadata_mutable", status("metadata_mutable")),
        ("transfer_hook", bool(raw.get("transfer_hook"))),
    ) if bad]
    return {
        "severe": severe,
        "warnings": warnings,
        "buy_tax_pct": None,
        "sell_tax_pct": None,
        "holder_count": to_float(raw.get("holder_count")),
        "top10_holders_pct": _top_holders_pct(raw.get("holders")),
    }
