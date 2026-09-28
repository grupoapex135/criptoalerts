"""On-chain state from whatever provider is plugged in (see providers/onchain.py)."""
from __future__ import annotations
from typing import Any


def build_onchain(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {"available": False, "state": None}
    netflow = raw.get("exchange_netflow_usd")
    holders = raw.get("large_holder_accumulation_pct")
    if netflow is None and holders is None:
        state = None
    elif (netflow is None or netflow < 0) and (holders is None or holders > 0):
        # Coins leaving exchanges and/or big wallets growing.
        state = "accumulation"
    elif (netflow is None or netflow > 0) and (holders is None or holders < 0):
        state = "distribution"
    else:
        state = "neutral"
    return {
        "available": state is not None,
        "source": raw.get("source"),
        "exchange_netflow": netflow,
        "exchange_reserves_change": raw.get("exchange_reserves_change_pct"),
        "whale_activity": raw.get("whale_activity"),
        "large_holder_accumulation": holders,
        "stablecoin_flow": raw.get("stablecoin_flow_usd"),
        "state": state,
    }


def onchain_score(o: dict[str, Any]) -> float | None:
    state = o.get("state")
    if not state:
        return None
    return {"accumulation": 75.0, "neutral": 50.0, "distribution": 25.0}[state]
