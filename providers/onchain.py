"""
On-chain layer interface. No provider ships enabled: CryptoQuant, Glassnode
and Nansen are paid and each has its own asset coverage.

To add one: subclass OnchainProvider, implement `metrics(symbol)` returning
the keys below (None when unknown), and return it from `get_onchain_provider()`.
"""
from __future__ import annotations
from typing import Any

ONCHAIN_KEYS = (
    "exchange_netflow_usd",        # + = coins flowing INTO exchanges (sell pressure)
    "exchange_reserves_change_pct",
    "whale_activity",              # e.g. count/volume of large transfers
    "large_holder_accumulation_pct",
    "stablecoin_flow_usd",
)


class OnchainProvider:
    source = "none"

    def metrics(self, symbol: str) -> dict[str, Any] | None:
        raise NotImplementedError


def get_onchain_provider() -> OnchainProvider | None:
    # Plug a concrete provider here (e.g. CryptoQuantProvider()) when a key exists.
    return None
