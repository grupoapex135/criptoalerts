"""
Scanner pipeline. Expensive work only happens for survivors of cheaper stages:

  Two modes share the funnel, each with its own quotas:
    binance      — assets on Binance spot (top TOP_COINS_TO_SCAN)
    pre_listing  — assets NOT on Binance spot yet (top PRE_LISTING_TOP_N, $10M–$1B)

  top N coins ─ prefilter per mode ─────▶ stage 1 (bulk data, no per-coin calls)
             ─ top TREND_CANDIDATES ─────▶ stage 2 (90d history: trend, supply growth)
             ─ top DEEP_CANDIDATES ──────▶ stage 3 (unlocks, derivatives, social, news, TVL 30d)
             ─ no vetoes, score ok ──────▶ AI (top MAX_AI_CANDIDATES) ─▶ code checks ─▶ alert
"""
from __future__ import annotations
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Iterable

from config import settings
from analysis import params as P
from analysis.catalysts import build_catalysts
from analysis.derivatives import build_derivatives
from analysis.filters import prefilter
from analysis.fundamentals import build_fundamentals
from analysis.listing import build_contract, build_listing
from analysis.market_regime import build_market_regime
from analysis.onchain import build_onchain
from analysis.scoring import compute_scores, coverage_penalty, risk_label
from analysis.social import build_social
from analysis.tokenomics import build_tokenomics, supply_growth_30d
from analysis.trend import btc_relative, compute_trend
from analysis.vetoes import compute_vetoes
from ai_analyzer import analyze
from providers import BinanceClient, CoinGeckoClient, DefiLlamaClient
from providers.base import cache, pct_change, safe_call, to_float
from providers.binance_futures import BinanceFuturesClient
from providers.coinglass import CoinGlassClient
from providers.lunarcrush import LunarCrushClient
from providers.news import NewsClient
from providers.onchain import get_onchain_provider
from providers.security import token_security
from providers.sentiment import fear_greed
from providers.tokenomist import TokenomistClient

log = logging.getLogger(__name__)

cg = CoinGeckoClient()
llama = DefiLlamaClient()
binance = BinanceClient()
futures = BinanceFuturesClient()
coinglass = CoinGlassClient()
tokenomist = TokenomistClient()
lunarcrush = LunarCrushClient()
news = NewsClient()

RISK_ORDER = {"baixo": 0, "medio": 1, "alto": 2}


def _num(v, default=0.0):
    f = to_float(v)
    return default if f is None else f


def position_limit_brl(factor: float = 1.0) -> float:
    return round(settings.capital_brl * (settings.max_position_pct / 100.0) * factor, 2)


def enabled_layers(mode: str = "binance") -> set[str]:
    """Layers that have a configured source; the rest don't lower data coverage."""
    layers = {"market_quality", "trend_quality", "fundamentals", "tokenomics"}
    if mode == "pre_listing":
        layers.add("listing")
    if settings.enable_derivatives:
        layers.add("derivatives")
    if settings.enable_social and lunarcrush.enabled:
        layers.add("social")
    if settings.enable_onchain and get_onchain_provider():
        layers.add("onchain")
    return layers


# ------------------------------------------------------------------ market data
def _range_7d(coin: dict[str, Any]) -> tuple[float | None, float | None]:
    prices = [p for p in ((coin.get("sparkline_in_7d") or {}).get("price") or []) if p is not None]
    if not prices:
        return None, None
    return min(prices), max(prices)


def market_snapshot(coin: dict[str, Any]) -> dict[str, Any]:
    mcap = _num(coin.get("market_cap"))
    fdv = _num(coin.get("fully_diluted_valuation"))
    low_7d, high_7d = _range_7d(coin)
    return {
        "current_price_usd": _num(coin.get("current_price")),
        "market_cap_usd": mcap,
        "daily_volume_usd": _num(coin.get("total_volume")),
        "fdv_usd": fdv or None,
        "fdv_to_market_cap": round(fdv / mcap, 3) if fdv and mcap else None,
        "price_change_1h_pct": to_float(coin.get("price_change_percentage_1h_in_currency")),
        "price_change_24h_pct": to_float(coin.get("price_change_percentage_24h_in_currency")),
        "price_change_7d_pct": to_float(coin.get("price_change_percentage_7d_in_currency")),
        "price_change_30d_pct": to_float(coin.get("price_change_percentage_30d_in_currency")),
        "high_24h_usd": to_float(coin.get("high_24h")),
        "low_24h_usd": to_float(coin.get("low_24h")),
        "range_7d_low_usd": low_7d,
        "range_7d_high_usd": high_7d,
        "ath_change_pct": to_float(coin.get("ath_change_percentage")),
    }


def get_market_regime(coins: list[dict[str, Any]]) -> dict[str, Any]:
    def build():
        btc = next((c for c in coins if c.get("id") == "bitcoin"), None)
        eth = next((c for c in coins if c.get("id") == "ethereum"), None)
        global_data = safe_call("CoinGecko global", cg.global_data)
        history = safe_call("Histórico BTC", lambda: cg.daily_history("bitcoin")) or {}
        sentiment = safe_call("Fear & Greed", fear_greed)
        return build_market_regime(btc, eth, global_data, history.get("prices"), sentiment)
    return cache.get_or_set("regime", P.CACHE_TTL["market_regime"], build)


def load_bulk() -> dict[str, Any]:
    """DefiLlama datasets covering every protocol at once: one call each, cached."""
    return {
        "protocols": safe_call("DefiLlama protocols", llama.protocols, []),
        "chains": safe_call("DefiLlama chains", llama.chains, []),
        "overviews": {k: safe_call(f"DefiLlama {k}", lambda k=k: llama.overview(k), [])
                      for k in llama.OVERVIEWS},
    }


def binance_venue(symbol: str, coingecko_price: float, ticker: dict[str, Any] | None = None) -> dict[str, Any] | None:
    pair = binance.spot_pair(symbol)
    if not pair:
        return None
    venue = ticker if ticker is not None else binance.ticker(pair)
    if not venue:
        return None
    if coingecko_price > 0:
        gap = abs(venue["last_price"] - coingecko_price) / coingecko_price * 100
        if gap > P.MAX_VENUE_PRICE_GAP_PCT:
            # Same ticker on CoinGecko and Binance can be different assets.
            log.info("%s: Binance %s diverge %.1f%% do CoinGecko; ativo diferente, ignorado.", symbol, pair, gap)
            return None
    return venue


# ------------------------------------------------------------------ dossier
class Candidate:
    """One asset moving through the funnel: provider context + the dossier the AI will read."""

    def __init__(self, coin: dict[str, Any], bulk: dict[str, Any], regime: dict[str, Any],
                 mode: str = "binance", perps: set[str] | frozenset = frozenset(),
                 binance_categories: dict[str, set[str]] | None = None, meme_ids: set[str] | None = None):
        self.coin = coin
        self.mode = mode
        self.id = str(coin.get("id") or "")
        self.symbol = str(coin.get("symbol") or "").upper()
        self.market = market_snapshot(coin)
        btc_price, btc_30d = regime.get("btc_price"), regime.get("btc_30d")
        p30 = self.market["price_change_30d_pct"]
        self.market["price_btc"] = round(self.market["current_price_usd"] / btc_price, 10) if btc_price else None
        self.market["vs_btc_30d_pct"] = (round(((1 + p30 / 100) / (1 + btc_30d / 100) - 1) * 100, 2)
                                         if p30 is not None and btc_30d is not None else None)
        protocols, chains = bulk["protocols"], bulk["chains"]
        self.ctx = llama.protocol_context(self.id, self.symbol, protocols) if protocols else None
        if self.ctx is None and chains:
            self.ctx = llama.chain_context(self.id, chains)
        self.flows = ({k: llama.flow_metrics(self.ctx, entries) for k, entries in bulk["overviews"].items()}
                      if self.ctx else {})
        self.history: dict[str, list[float]] = {}
        self.btc_history: dict[str, list[float]] = {}
        self.tvl_change_30d: float | None = None
        self.unlocks: dict[str, Any] | None = None
        self.venue: dict[str, Any] | None = None
        self.profile: dict[str, Any] | None = None
        self.contract_raw: dict[str, Any] | None = None
        self.has_perp = self.symbol in perps
        self.early_categories = (binance_categories or {}).get(self.id, set())
        self.is_meme = self.id in (meme_ids or set())
        self.dossier: dict[str, Any] = {
            "mode": mode,
            "asset": {"id": self.id, "symbol": self.symbol, "name": self.coin.get("name")},
            "market_regime": {k: v for k, v in regime.items() if k != "adjustments"},
            "market": self.market,
            "venue": None,
            "derivatives": {"available": False, "positioning": None},
            "onchain": {"available": False, "state": None},
            "social": {"available": False, "state": None},
            "catalysts": {"available": False, "positive": [], "negative": [], "critical_risk": False},
        }
        self.rebuild()

    @property
    def price(self) -> float:
        return self.market["current_price_usd"]

    def rebuild(self):
        m = self.market
        p24, p7, p30 = m["price_change_24h_pct"], m["price_change_7d_pct"], m["price_change_30d_pct"]
        prices, caps = self.history.get("prices"), self.history.get("market_caps")
        holders = (self.flows.get("holders_revenue") or {}).get("total_30d")
        self.dossier["trend"] = compute_trend(prices, self.price, p24, p7, p30)
        self.dossier["vs_btc"] = btc_relative(prices, self.btc_history.get("prices"))
        self.dossier["fundamentals"] = build_fundamentals(self.ctx, self.flows, self.tvl_change_30d,
                                                          m["market_cap_usd"], p30)
        self.dossier["tokenomics"] = build_tokenomics(self.coin, supply_growth_30d(caps, prices),
                                                      self.unlocks, holders)
        if self.mode == "pre_listing":
            self.dossier["listing"] = build_listing(self.profile, self.has_perp, self.early_categories, self.is_meme)
            self.dossier["contract"] = build_contract(self.contract_raw)
        else:
            self.dossier["listing"] = {"available": False}
            self.dossier["contract"] = {"available": False, "status": None}
        self.dossier["venue"] = self._venue()  # after listing: venue order uses the tier-1 list
        self.dossier["scores"] = compute_scores(self.dossier, enabled_layers(self.mode))
        self.dossier["vetoes"] = compute_vetoes(self.dossier)

    def _venue(self) -> dict[str, Any] | None:
        if self.mode == "binance":
            return ({"exchange": "Binance", "binance": True, "pair": self.venue["symbol"],
                     "last_price": self.venue["last_price"]} if self.venue else None)
        profile = self.profile or {}
        # Tier-1 exchanges first (where a reader would actually buy), then the rest by volume.
        tier1 = (self.dossier.get("listing") or {}).get("tier1_cex") or []
        cex = [x for x in (profile.get("cex") or []) if x in tier1] + [x for x in (profile.get("cex") or []) if x not in tier1]
        dex = profile.get("dex") or []
        # Off Binance there is no single venue price: CoinGecko's aggregate is the reference.
        return {"exchange": (cex or dex or [None])[0], "binance": False, "cex": cex[:3], "dex": dex[:2],
                "last_price": self.price}

    @property
    def composite(self) -> float:
        return self.dossier["scores"]["composite"]

    def could_pass_the_rule(self) -> bool:
        """
        Pre-listing rule, checked BEFORE spending deep slots: DefiLlama fundamentals, or a
        Binance signal on something that is not a memecoin (the goal is fundamentals).
        """
        return bool(self.ctx) or ((self.has_perp or bool(self.early_categories)) and not self.is_meme)

    def eligible_for_ai(self, min_score: float) -> bool:
        scores = self.dossier["scores"]
        min_coverage = P.MIN_DATA_COVERAGE_FOR_AI
        if self.mode == "pre_listing" and self.market["market_cap_usd"] < P.PRE_LISTING_SMALL_CAP_USD:
            # Small caps need more evidence ("it depends on the research and the fundamentals").
            min_score += P.PRE_LISTING_SMALL_CAP_EXTRA_SCORE
            min_coverage = P.PRE_LISTING_SMALL_CAP_MIN_COVERAGE
        if self.mode == "pre_listing":
            listing, contract = self.dossier.get("listing") or {}, self.dossier.get("contract") or {}
            has_fundamentals = (self.dossier.get("fundamentals") or {}).get("available")
            has_binance_signal = any(listing.get(k) for k in (
                "binance_alpha", "binance_perp_without_spot", "yzi_labs", "binance_programs"))
            # The goal is assets WITH fundamentals before a Binance listing. Without DefiLlama
            # data, a Binance signal only counts for a real project: official description, not a meme.
            has_project = bool(self.dossier["asset"].get("description")) and not listing.get("meme")
            if not (has_fundamentals or (has_binance_signal and has_project)):
                return False
            # A DEX-only token whose contract could not be checked is not worth the risk.
            if listing.get("dex_only") and not contract.get("available"):
                return False
        return not self.dossier["vetoes"] and scores["composite"] >= min_score and scores["data_coverage"] >= min_coverage


def reference_price(dossier: dict[str, Any]) -> float:
    venue = dossier.get("venue") or {}
    return _num(venue.get("last_price")) or _num((dossier.get("market") or {}).get("current_price_usd"))


# ------------------------------------------------------------------ stages
def add_profile(c: Candidate):
    """Description, categories, venues and contracts. Binance mode: only before the AI."""
    if c.profile is None:
        c.profile = safe_call(f"Perfil {c.symbol}", lambda: cg.coin_profile(c.id)) or {}
    c.dossier["asset"]["description"] = c.profile.get("description") or (c.ctx or {}).get("description")
    c.dossier["asset"]["categories"] = (c.profile.get("categories") or [])[:8]


def add_history(c: Candidate):
    c.history = safe_call(f"Histórico {c.symbol}", lambda: cg.daily_history(c.id)) or {}
    # Same cached series the market regime already fetched: no extra call.
    c.btc_history = safe_call("Histórico BTC", lambda: cg.daily_history("bitcoin")) or {}
    c.rebuild()


def add_deep_data(c: Candidate, hacks: list[dict[str, Any]], headlines: dict[str, list] | None):
    m = c.market
    if c.mode == "pre_listing":
        # Listing signals, where it trades and the contract check all come from the profile.
        add_profile(c)
        c.contract_raw = safe_call(f"Contrato {c.symbol}", lambda: token_security(c.profile.get("platforms") or {}))
    if settings.enable_tokenomics and tokenomist.enabled:
        c.unlocks = safe_call(f"Tokenomist {c.symbol}",
                              lambda: tokenomist.unlocks(c.id, c.symbol, to_float(c.coin.get("circulating_supply"))))

    if c.ctx and c.ctx.get("slug"):
        series = safe_call(f"TVL histórico {c.symbol}", lambda: llama.tvl_series(c.ctx["kind"], c.ctx["slug"])) or []
        c.tvl_change_30d = pct_change(series[-1], series[-31]) if len(series) > 30 else None

    if settings.enable_derivatives:
        raw = safe_call(f"CoinGlass {c.symbol}", lambda: coinglass.derivatives(c.symbol)) if coinglass.enabled else None
        if raw is None:
            raw = safe_call(f"Binance Futures {c.symbol}", lambda: futures.derivatives(c.symbol))
        c.dossier["derivatives"] = build_derivatives(raw, m["price_change_24h_pct"], m["price_change_7d_pct"])

    if settings.enable_social and lunarcrush.enabled:
        raw = safe_call(f"LunarCrush {c.symbol}", lambda: lunarcrush.social(c.symbol, m["market_cap_usd"]))
        c.dossier["social"] = build_social(raw)

    provider = get_onchain_provider() if settings.enable_onchain else None
    if provider:
        c.dossier["onchain"] = build_onchain(safe_call(f"On-chain {c.symbol}", lambda: provider.metrics(c.symbol)))

    if settings.enable_news:
        since = (datetime.now(timezone.utc) - timedelta(days=P.CATALYST_LOOKBACK_DAYS)).timestamp()
        sources = ["defillama_hacks"] + (["newsdata"] if headlines is not None else [])
        c.dossier["catalysts"] = build_catalysts(
            llama.recent_hacks(c.ctx, str(c.coin.get("name") or c.symbol), since, hacks),
            (headlines or {}).get(c.symbol), sources)

    c.rebuild()


def run_deep_stage(cands: list[Candidate]):
    if not cands:
        return
    hacks = safe_call("DefiLlama hacks", llama.hacks, []) if settings.enable_news else []
    headlines = None
    if settings.enable_news and news.enabled:
        headlines = safe_call("Notícias", lambda: news.headlines([c.symbol for c in cands]))
    with ThreadPoolExecutor(max_workers=P.DEEP_WORKERS) as pool:
        list(pool.map(lambda c: add_deep_data(c, hacks, headlines), cands))


def _pick(ranked: list[Candidate], n: int, watchlist: set[str]) -> list[Candidate]:
    top = ranked[:n]
    extra = [c for c in ranked[n:] if c.symbol in watchlist]
    return top + extra


# ------------------------------------------------------------------ AI + guard
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
    if price > 0 and not (inval < price < target):
        # A target below the current price would be "hit" on the first candle.
        problems.append("alvo precisa estar acima e invalidação abaixo do preço atual")
    if price > 0:
        low = price * (1 - P.MAX_ENTRY_DISTANCE_PCT / 100)
        high = price * (1 + P.MAX_ENTRY_DISTANCE_PCT / 100)
        if emin < low or emax > high:
            problems.append(f"zona de entrada a mais de {P.MAX_ENTRY_DISTANCE_PCT:.0f}% do preço atual")
    return problems


def ai_dossier(dossier: dict[str, Any]) -> dict[str, Any]:
    """What the AI reads: every layer, without bulky or internal fields."""
    d = dict(dossier)
    d["trend"] = {k: v for k, v in (d.get("trend") or {}).items() if k not in ("ma20", "ma50", "ma200")}
    return d


def decide(dossier: dict[str, Any], ai: dict[str, Any], regime: dict[str, Any]) -> dict[str, Any]:
    """Combines the AI's call with the code's own gates into the final decision."""
    scores = dossier["scores"]
    adj = regime.get("adjustments") or P.REGIME_ADJUSTMENTS["neutral"]
    confidence = max(0, int(ai.get("confidence") or 0) - coverage_penalty(scores))
    code_risk = risk_label(scores["risk"])
    ai_risk = ai.get("risk") if ai.get("risk") in RISK_ORDER else "alto"
    risk = max(ai_risk, code_risk, key=RISK_ORDER.get)
    problems: list[str] = []

    decision = ai.get("decision")
    if dossier.get("vetoes"):
        decision = "reject"
    elif decision == "alert":
        problems = check_levels(ai, reference_price(dossier))
        if problems:
            decision = "reject"
        elif confidence < adj["min_confidence"] or scores["composite"] < adj["min_score"]:
            decision = "watch"
    elif decision not in ("watch", "reject"):
        decision = "reject"

    return {
        "decision": decision,
        "confidence": confidence,
        "risk": risk,
        "problems": problems,
        "position_limit_brl": position_limit_brl(adj["position_factor"]),
    }


# ------------------------------------------------------------------ entry points
def _universe_limit() -> int:
    return max(settings.top_coins_to_scan, settings.pre_listing_top_n if settings.enable_pre_listing else 0)


def _mode_of(coin: dict[str, Any]) -> str | None:
    return "binance" if binance.spot_pair(str(coin.get("symbol") or "")) else (
        "pre_listing" if settings.enable_pre_listing else None)


def _perps() -> set[str]:
    return safe_call("Binance Futures (perpétuos)", futures.perp_bases, set()) if settings.enable_pre_listing else set()


def _meme_ids() -> set[str]:
    if not settings.enable_pre_listing:
        return set()
    return safe_call("Categoria meme", lambda: cg.category_ids(P.MEME_CATEGORY_ID), set())


def _binance_categories() -> dict[str, set[str]]:
    """coin id -> Binance program categories it belongs to (bulk, one call per category)."""
    if not settings.enable_pre_listing:
        return {}
    out: dict[str, set[str]] = {}
    for cat_id, name in P.BINANCE_CATEGORY_IDS.items():
        for coin_id in safe_call(f"Categoria {cat_id}", lambda c=cat_id: cg.category_ids(c), set()):
            out.setdefault(coin_id, set()).add(name)
    return out


def _run_mode(mode: str, universe: list[Candidate], skip_symbol, watch: set[str],
              min_score: float) -> tuple[list[Candidate], list[Candidate], list[Candidate]]:
    """Stages 2 and 3 for one mode. Returns (deep, vetoed, finalists)."""
    universe.sort(key=lambda c: c.composite, reverse=True)
    # Cooldown before any per-coin call: repeated symbols cost nothing.
    fresh = [c for c in universe if not (skip_symbol and skip_symbol(c.symbol))]
    if mode == "pre_listing":
        # Real case: all 8 deep slots went to coins with neither fundamentals nor a Binance
        # signal, which the final rule then discarded. Spend slots only on possible passes.
        fresh = [c for c in fresh if c.could_pass_the_rule() or c.symbol in watch]
    stage2 = _pick(fresh, P.TREND_CANDIDATES, watch)

    if mode == "binance":
        tickers = binance.tickers([f"{c.symbol}USDT" for c in stage2])
        confirmed = []
        for c in stage2:
            c.venue = binance_venue(c.symbol, c.price, tickers.get(f"{c.symbol}USDT"))
            if c.venue:
                add_history(c)
                confirmed.append(c)
    else:
        for c in stage2:
            add_history(c)
        confirmed = list(stage2)
    confirmed.sort(key=lambda c: c.composite, reverse=True)

    stage3 = _pick([c for c in confirmed if not c.dossier["vetoes"]], P.DEEP_CANDIDATES, watch)
    run_deep_stage(stage3)

    limit = settings.max_ai_candidates if mode == "binance" else settings.pre_listing_max_ai_candidates
    finalists = sorted((c for c in stage3 if c.eligible_for_ai(min_score)),
                       key=lambda c: c.composite, reverse=True)[:limit]
    return stage3, [c for c in confirmed if c.dossier["vetoes"]], finalists


def scan_candidates(skip_symbol: Callable[[str], bool] | None = None,
                    watchlist: Iterable[str] = ()) -> tuple[dict[str, Any], list[Candidate], dict[str, Any]]:
    """Runs the funnel up to (not including) the AI. Returns regime, finalists and funnel stats."""
    watch = {s.upper() for s in watchlist}
    coins = cg.markets(_universe_limit())
    regime = get_market_regime(coins)
    bulk = load_bulk()
    perps = _perps()
    categories = _binance_categories()
    memes = _meme_ids()

    by_mode: dict[str, list[Candidate]] = {"binance": [], "pre_listing": []}
    for rank, coin in enumerate(coins):
        mode = _mode_of(coin)
        if mode == "binance" and rank >= settings.top_coins_to_scan:
            continue
        if mode and not prefilter(coin, mode):
            by_mode[mode].append(Candidate(coin, bulk, regime, mode, perps, categories, memes))

    deep, vetoed, finalists = [], [], []
    for mode, universe in by_mode.items():
        d, v, f = _run_mode(mode, universe, skip_symbol, watch, regime["adjustments"]["min_score"])
        deep += d
        vetoed += v
        finalists += f
    stats = {
        "universe": sum(len(u) for u in by_mode.values()),
        "universe_pre_listing": len(by_mode["pre_listing"]),
        "deep": len(deep),
        "vetoed": [f"{c.symbol}: {', '.join(c.dossier['vetoes'])}" for c in vetoed][:8],
    }
    return regime, finalists, stats


def evaluate_candidates(skip_symbol: Callable[[str], bool] | None = None,
                        watchlist: Iterable[str] = ()) -> dict[str, Any]:
    """
    One radar pass. The report tells apart "nothing passed" from "the AI failed":
    {"regime", "universe", "deep", "candidates", "results", "watch", "vetoed", "rejected", "errors"}
    """
    regime, finalists, stats = scan_candidates(skip_symbol, watchlist)
    results, watch, rejected, errors = [], [], [], []

    for c in finalists:
        add_profile(c)
        try:
            ai = analyze(ai_dossier(c.dossier))
        except Exception as exc:
            log.exception("IA falhou para %s", c.symbol)
            errors.append(f"{c.symbol}: {exc}")
            continue
        final = decide(c.dossier, ai, regime)
        if final["decision"] == "alert":
            results.append({"dossier": c.dossier, "ai": ai, **final})
        elif final["decision"] == "watch":
            watch.append(c.symbol)
        elif final["problems"]:
            log.warning("%s descartado: %s | %s", c.symbol, "; ".join(final["problems"]), ai)
            rejected.append(f"{c.symbol}: {'; '.join(final['problems'])}")

    return {"regime": regime, **stats, "candidates": len(finalists), "results": results,
            "watch": watch, "rejected": rejected, "errors": errors}


def analyze_symbol(query_symbol: str) -> dict[str, Any] | None:
    """Full dossier for one asset, even if it would not pass the scanner's filters."""
    symbol = query_symbol.strip().upper()
    coins = cg.markets(_universe_limit())
    coin = next((c for c in coins if str(c.get("symbol") or "").upper() == symbol), None)
    if not coin:
        return None

    regime = get_market_regime(coins)
    mode = _mode_of(coin) or "binance"
    pre = mode == "pre_listing"
    c = Candidate(coin, load_bulk(), regime, mode, _perps() if pre else set(), _binance_categories() if pre else {},
                  _meme_ids() if pre else set())
    if mode == "binance":
        c.venue = binance_venue(symbol, c.price)
    add_history(c)
    run_deep_stage([c])

    if c.dossier["vetoes"]:
        # A hard veto is final: no need to pay for an AI opinion.
        return {"dossier": c.dossier, "ai": None, "decision": "reject", "confidence": None,
                "risk": risk_label(c.dossier["scores"]["risk"]), "problems": [],
                "position_limit_brl": position_limit_brl(regime["adjustments"]["position_factor"])}

    add_profile(c)
    ai = analyze(ai_dossier(c.dossier))
    return {"dossier": c.dossier, "ai": ai, **decide(c.dossier, ai, regime)}
