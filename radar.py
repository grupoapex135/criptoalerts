from __future__ import annotations
import logging
from typing import Any, Callable
from config import settings
from providers import CoinGeckoClient, DefiLlamaClient, BinanceClient
from scoring import score_market_candidate, enrich_with_defi
from ai_analyzer import analyze

log = logging.getLogger(__name__)

cg = CoinGeckoClient()
llama = DefiLlamaClient()
binance = BinanceClient()

# Same ticker on CoinGecko and Binance can be different assets
# (e.g. CoinGecko "AI" vs Binance AIUSDT). Above this gap we treat it as a mismatch.
MAX_VENUE_PRICE_GAP_PCT = 5.0
# Code-level guard for "keep entry zone near current price".
MAX_ENTRY_DISTANCE_PCT = 10.0

def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _opt(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def position_limit_brl() -> float:
    return round(settings.capital_brl * (settings.max_position_pct / 100.0), 2)

def _range_7d(coin: dict[str, Any]) -> tuple[float | None, float | None]:
    prices = [p for p in ((coin.get("sparkline_in_7d") or {}).get("price") or []) if p is not None]
    if not prices:
        return None, None
    return min(prices), max(prices)

def build_snapshot(coin: dict[str, Any], score: float, reasons: list[str],
                   defi: dict[str, Any] | None, venue: dict[str, Any] | None) -> dict[str, Any]:
    mcap = _num(coin.get("market_cap"))
    fdv = _num(coin.get("fully_diluted_valuation"))
    low_7d, high_7d = _range_7d(coin)
    return {
        "id": coin.get("id"),
        "symbol": str(coin.get("symbol") or "").upper(),
        "name": coin.get("name"),
        "current_price_usd": _num(coin.get("current_price")),
        "market_cap_usd": mcap,
        "daily_volume_usd": _num(coin.get("total_volume")),
        "fdv_usd": fdv or None,
        "fdv_to_market_cap": round(fdv / mcap, 3) if fdv and mcap else None,
        "price_change_1h_pct": _opt(coin.get("price_change_percentage_1h_in_currency")),
        "price_change_24h_pct": _opt(coin.get("price_change_percentage_24h_in_currency")),
        "price_change_7d_pct": _opt(coin.get("price_change_percentage_7d_in_currency")),
        "price_change_30d_pct": _opt(coin.get("price_change_percentage_30d_in_currency")),
        "high_24h_usd": _opt(coin.get("high_24h")),
        "low_24h_usd": _opt(coin.get("low_24h")),
        "range_7d_low_usd": low_7d,
        "range_7d_high_usd": high_7d,
        "ath_change_pct": _opt(coin.get("ath_change_percentage")),
        "market_score": round(score, 1),
        "quant_reasons": reasons,
        "defillama_protocol_context": defi,
        "binance_spot": venue,
        "position_limit_brl": position_limit_brl(),
    }

def reference_price(snapshot: dict[str, Any]) -> float:
    venue = snapshot.get("binance_spot") or {}
    return _num(venue.get("last_price")) or _num(snapshot.get("current_price_usd"))

def binance_venue(symbol: str, coingecko_price: float) -> dict[str, Any] | None:
    pair = binance.spot_pair(symbol)
    if not pair:
        return None
    venue = binance.ticker(pair)
    if not venue:
        return None
    if coingecko_price > 0:
        gap = abs(venue["last_price"] - coingecko_price) / coingecko_price * 100
        if gap > MAX_VENUE_PRICE_GAP_PCT:
            log.info("%s: Binance %s diverge %.1f%% do CoinGecko; ativo diferente, ignorado.",
                     symbol, pair, gap)
            return None
    return venue

def check_levels(ai: dict[str, Any], price: float) -> list[str]:
    """Returns the problems found in the AI price levels (empty = coherent)."""
    try:
        emin, emax, target, inval = (
            float(ai[k]) for k in ("entry_min_usd", "entry_max_usd", "target_usd", "invalidation_usd")
        )
    except (KeyError, TypeError, ValueError):
        return ["níveis de preço ausentes ou inválidos"]

    problems = []
    if not (0 < inval < emin <= emax < target):
        problems.append("níveis incoerentes (exige invalidação < entrada mín ≤ entrada máx < alvo)")
    if price > 0:
        low = price * (1 - MAX_ENTRY_DISTANCE_PCT / 100)
        high = price * (1 + MAX_ENTRY_DISTANCE_PCT / 100)
        if emin < low or emax > high:
            problems.append(f"zona de entrada a mais de {MAX_ENTRY_DISTANCE_PCT:.0f}% do preço atual")
    return problems

def scan_candidates(skip_symbol: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    coins = cg.markets(settings.top_coins_to_scan)

    try:
        protocols = llama.protocols()
    except Exception as exc:
        # TVL is optional enrichment; the radar keeps working without it.
        log.warning("DefiLlama indisponível: %s", exc)
        protocols = []

    ranked = []
    for coin in coins:
        base_score, reasons = score_market_candidate(coin)
        if base_score <= 0:
            continue

        symbol = str(coin.get("symbol") or "").upper()
        defi = llama.protocol_context(str(coin.get("id") or ""), symbol, protocols) if protocols else None
        score, defi_reasons = enrich_with_defi(base_score, defi)
        if score >= settings.min_score_to_ai:
            ranked.append((score, coin, reasons + defi_reasons, defi))

    # Best scores first; network checks only until the AI slots are filled.
    ranked.sort(key=lambda r: r[0], reverse=True)
    candidates = []
    for score, coin, reasons, defi in ranked:
        if len(candidates) >= settings.max_ai_candidates:
            break
        symbol = str(coin.get("symbol") or "").upper()
        # Checked before the AI call so cooldown symbols don't burn tokens
        # or take the slots of fresh candidates.
        if skip_symbol and skip_symbol(symbol):
            continue

        venue = binance_venue(symbol, _num(coin.get("current_price")))
        if not venue:
            # MVP only alerts assets actually available on Binance spot.
            continue

        candidates.append(build_snapshot(coin, score, reasons, defi, venue))

    return candidates

def evaluate_candidates(skip_symbol: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """
    Runs one radar pass. Returns a report so callers can tell apart
    "nothing passed" from "the AI failed":
    {"candidates": int, "results": [...], "errors": [str], "rejected": [str]}
    """
    candidates = scan_candidates(skip_symbol)
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    rejected: list[str] = []

    for snapshot in candidates:
        symbol = snapshot["symbol"]
        try:
            ai = analyze(snapshot)
        except Exception as exc:
            log.exception("IA falhou para %s", symbol)
            errors.append(f"{symbol}: {exc}")
            continue

        if not ai.get("alert"):
            continue
        if int(ai.get("confidence", 0)) < settings.min_confidence_to_alert:
            continue
        problems = check_levels(ai, reference_price(snapshot))
        if problems:
            log.warning("%s descartado: %s | %s", symbol, "; ".join(problems), ai)
            rejected.append(f"{symbol}: {'; '.join(problems)}")
            continue

        results.append({"snapshot": snapshot, "ai": ai})

    return {"candidates": len(candidates), "results": results, "errors": errors, "rejected": rejected}

def analyze_symbol(query_symbol: str) -> dict[str, Any] | None:
    symbol = query_symbol.strip().upper()
    coins = cg.markets(settings.top_coins_to_scan)
    coin = next((c for c in coins if str(c.get("symbol") or "").upper() == symbol), None)
    if not coin:
        return None

    try:
        defi = llama.protocol_context(str(coin.get("id") or ""), symbol, llama.protocols())
    except Exception as exc:
        log.warning("DefiLlama indisponível: %s", exc)
        defi = None

    score, reasons = score_market_candidate(coin)
    score, extra = enrich_with_defi(score, defi)
    reasons += extra

    venue = binance_venue(symbol, _num(coin.get("current_price")))
    snapshot = build_snapshot(coin, score, reasons, defi, venue)
    ai = analyze(snapshot)
    problems = check_levels(ai, reference_price(snapshot))
    return {"snapshot": snapshot, "ai": ai, "problems": problems}
