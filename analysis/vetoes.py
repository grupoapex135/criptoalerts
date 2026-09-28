"""
Hard vetoes: conditions that block an alert no matter how good the average
looks. Each returns a stable code; LABELS turns it into plain Portuguese.
"""
from __future__ import annotations
from typing import Any
from config import settings
from analysis.derivatives import is_overheated
from analysis.params import CRITICAL_SUPPLY_GROWTH_30D
from analysis.tokenomics import dilution_level

LABELS = {
    "critical_unlock": "Desbloqueio grande de tokens nos próximos 30 dias",
    "critical_supply_inflation": "Oferta do token crescendo rápido demais",
    "low_liquidity": "Liquidez muito baixa",
    "critical_event": "Hack ou exploit recente no protocolo",
    "risk_off_downtrend": "Mercado em risco e ativo em tendência de baixa",
    "extreme_dilution": "Diluição (FDV ÷ market cap) alta demais",
    "overheated_leverage": "Alta alavancada: preço, open interest e funding esticados",
}


def compute_vetoes(d: dict[str, Any]) -> list[str]:
    market = d.get("market") or {}
    trend = d.get("trend") or {}
    tok = d.get("tokenomics") or {}
    der = d.get("derivatives") or {}
    cat = d.get("catalysts") or {}
    regime = d.get("market_regime") or {}
    vetoes = []

    unlock_30d = tok.get("unlock_30d_pct")
    if unlock_30d is not None and dilution_level(unlock_30d) == "critical":
        vetoes.append("critical_unlock")
    growth = tok.get("supply_growth_30d")
    if growth is not None and growth >= CRITICAL_SUPPLY_GROWTH_30D:
        vetoes.append("critical_supply_inflation")

    volume = market.get("daily_volume_usd") or 0
    mcap = market.get("market_cap_usd") or 0
    if volume < settings.min_daily_volume_usd or (mcap and volume / mcap < 0.005):
        vetoes.append("low_liquidity")

    if cat.get("critical_risk"):
        vetoes.append("critical_event")

    if regime.get("status") == "risk_off" and trend.get("state") in ("DOWNTREND", "CAPITULATION"):
        vetoes.append("risk_off_downtrend")

    ratio = tok.get("fdv_mcap_ratio")
    if ratio is not None and ratio > settings.max_fdv_to_mcap_ratio:
        vetoes.append("extreme_dilution")

    if der.get("available") and is_overheated(der, market.get("price_change_24h_pct")):
        vetoes.append("overheated_leverage")

    return vetoes
