"""
Pre-Binance mode: signals that often precede a Binance spot listing, and the
contract checks a DEX/small-cap token needs. None of this predicts a listing —
Binance does not publish its pipeline. These are probabilistic hints.
"""
from __future__ import annotations
from typing import Any
from analysis.params import BINANCE_PROGRAM_CATEGORIES, MAJOR_CEX, TIER1_CEX


def build_listing(profile: dict[str, Any] | None, has_binance_perp: bool) -> dict[str, Any]:
    profile = profile or {}
    categories = set(profile.get("categories") or [])
    kinds = {BINANCE_PROGRAM_CATEGORIES[c] for c in categories if c in BINANCE_PROGRAM_CATEGORIES}
    programs = sorted(c for c in categories if BINANCE_PROGRAM_CATEGORIES.get(c) == "binance_program")
    cex_ids = set(profile.get("cex_ids") or [])
    tier1 = sorted(MAJOR_CEX[i] for i in cex_ids & TIER1_CEX)
    cex, dex = profile.get("cex") or [], profile.get("dex") or []
    return {
        "available": bool(profile) or has_binance_perp,
        "binance_alpha": "binance_alpha" in kinds,
        "yzi_labs": "yzi_labs" in kinds,
        "binance_programs": programs,
        "binance_perp_without_spot": has_binance_perp,
        "tier1_cex": tier1,
        "cex": cex[:5],
        "dex": dex[:3],
        "dex_only": bool(dex) and not cex,
        # CoinGecko lists a Binance market for it: it is already on Binance (renamed ticker, e.g. BTT/BTTC).
        "already_on_binance": "binance" in cex_ids,
    }


def listing_score(listing: dict[str, Any]) -> float | None:
    if not listing.get("available"):
        return None
    s = 30.0
    if listing.get("binance_alpha"):
        s += 30
    if listing.get("binance_perp_without_spot"):
        s += 25
    if listing.get("yzi_labs"):
        s += 15
    if listing.get("binance_programs"):
        s += 10
    tier1 = len(listing.get("tier1_cex") or [])
    s += 15 if tier1 >= 3 else (8 if tier1 >= 1 else 0)
    if listing.get("dex_only"):
        s -= 15
    return float(max(0, min(100, s)))


def build_contract(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {"available": False, "status": None}
    severe, warnings = raw.get("severe") or [], raw.get("warnings") or []
    concentration = raw.get("top10_holders_pct")
    if concentration is not None and concentration > 50:
        warnings = [*warnings, "concentrated_holders"]
    status = "danger" if severe else ("warning" if warnings else "ok")
    return {"available": True, **raw, "warnings": warnings, "status": status}
