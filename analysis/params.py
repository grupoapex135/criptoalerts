"""
Single place for the radar's tuning knobs: weights, thresholds, funnel sizes
and cache TTLs. The few values users are expected to change live in .env
(see config.py); everything else is tuned here.
"""
from config import settings

# ---------------------------------------------------------------- funnel
# Cheap stage covers every coin that passes the eliminatory filters.
TREND_CANDIDATES = 15   # get 90d daily history (trend + supply growth)
DEEP_CANDIDATES = 8     # get paid/slow data (unlocks, derivatives, social, news, TVL 30d)
DEEP_WORKERS = 4        # parallel deep enrichment
# Daily history length: 200d moving average + a 10-day window to spot a fresh 50/200 cross.
HISTORY_DAYS = 220

# ---------------------------------------------------------------- weights
# Composite = weighted average of the AVAILABLE subscores (missing data is
# re-weighted away, never scored as negative). `risk` is reported separately.
WEIGHTS = {
    "listing": 15,          # Pre-Binance mode only: listing signals (None for listed assets)
    "market_quality": 15,
    "trend_quality": 15,
    "fundamentals": 25,
    "tokenomics": 20,
    "derivatives": 10,
    "onchain": 10,
    "social": 5,
}
# Low data coverage lowers the AI confidence by up to this many points.
MAX_COVERAGE_PENALTY = 20
# An alert needs converging evidence: below this share of layers with data, no AI call.
MIN_DATA_COVERAGE_FOR_AI = 0.5

# ---------------------------------------------------------------- pre-Binance mode
# Below this market cap an asset needs more evidence (higher score and coverage).
PRE_LISTING_SMALL_CAP_USD = 30_000_000
PRE_LISTING_SMALL_CAP_EXTRA_SCORE = 5
PRE_LISTING_SMALL_CAP_MIN_COVERAGE = 0.7
# Assets off Binance are tracked on CoinGecko prices; check them less often (quota).
PRE_LISTING_TRACK_EVERY_HOURS = 6
# Exchanges whose listing counts as a quality signal.
MAJOR_CEX = {
    "gdax": "Coinbase", "okex": "OKX", "bybit_spot": "Bybit", "upbit": "Upbit", "kraken": "Kraken",
    "bithumb": "Bithumb", "bitget": "Bitget", "kucoin": "KuCoin", "gate": "Gate", "mxc": "MEXC",
}
TIER1_CEX = {"gdax", "okex", "bybit_spot", "upbit", "kraken", "bithumb"}
# CoinGecko category id -> name, fetched in bulk so Binance signals are known before
# the deep stage (and the deep slots go to assets that can pass the "with fundamentals" rule).
BINANCE_CATEGORY_IDS = {
    "binance-alpha-spotlight": "Binance Alpha Spotlight",
    "yzi-labs-portfolio": "YZi Labs (Prev. Binance Labs) Portfolio",
    "binance-hodler-airdrops": "Binance HODLer Airdrops",
    "binance-launchpool": "Binance Launchpool",
    "binance-launchpad": "Binance Launchpad",
    "binance-megadrop": "Binance Megadrop",
    "binance-wallet-ido": "Binance Wallet IDO",
    "binance-buildkey-tge": "Binance Buildkey TGE",
}
# Binance programs that usually precede or accompany a spot listing (CoinGecko categories).
BINANCE_PROGRAM_CATEGORIES = {
    "Binance Alpha Spotlight": "binance_alpha",
    "YZi Labs (Prev. Binance Labs) Portfolio": "yzi_labs",
    "Binance Wallet IDO": "binance_program",
    "Binance HODLer Airdrops": "binance_program",
    "Binance Launchpool": "binance_program",
    "Binance Launchpad": "binance_program",
    "Binance Megadrop": "binance_program",
    "Binance Buildkey TGE": "binance_program",
}

# ---------------------------------------------------------------- market regime
REGIME = {
    "risk_off_btc_7d": -10.0,       # BTC 7d at or below -> risk_off
    "risk_off_btc_30d": -20.0,
    "risk_off_total_mcap_24h": -7.0,
    "short_up_btc_7d": 3.0,
    "short_down_btc_7d": -5.0,
    "mid_up_btc_30d": 5.0,
    "mid_down_btc_30d": -10.0,
    "below_ma50_pct": -5.0,         # BTC this far under MA50 counts as mid-term down
}
REGIME_ADJUSTMENTS = {
    "risk_on": {"min_confidence": settings.min_confidence_to_alert, "min_score": settings.min_score_to_ai, "position_factor": 1.0},
    "neutral": {"min_confidence": settings.min_confidence_to_alert, "min_score": settings.min_score_to_ai, "position_factor": 1.0},
    "risk_off": {
        "min_confidence": max(settings.market_risk_off_min_confidence, settings.min_confidence_to_alert),
        "min_score": settings.min_score_to_ai + 10,
        "position_factor": 0.5,
    },
}

# ---------------------------------------------------------------- trend
TREND = {
    "capitulation_dist_ma50": -25.0,
    "capitulation_p7": -15.0,
    "capitulation_drawdown_30d": -35.0,
    "downtrend_dist_ma50": -12.0,
    "extended_dist_ma20": 15.0,
    "parabolic_dist_ma20": 30.0,     # far above the averages: late to the move
    "parabolic_dist_ma50": 50.0,
    "extended_p30": 35.0,
    "overheated_p24": 15.0,
    "cross_lookback_days": 10,       # a 50/200 cross younger than this is "recent"
}

# ---------------------------------------------------------------- tokenomics
# Share of circulating supply entering the market in 30 days.
# <2% low · 2-5% medium · 5%-critical high · >=critical (CRITICAL_UNLOCK_30D_PCT) critical
DILUTION = {"low_below": 2.0, "medium_below": 5.0, "critical_from": settings.critical_unlock_30d_pct}
CRITICAL_SUPPLY_GROWTH_30D = settings.critical_unlock_30d_pct

# ---------------------------------------------------------------- derivatives
DERIVATIVES = {
    "crowded_long_funding_pct": 0.05,    # per 8h funding, in percent
    "extreme_funding_pct": 0.10,
    "crowded_short_funding_pct": -0.03,
    "oi_surge_24h_pct": 25.0,
    "price_pump_24h_pct": 8.0,
    "deleveraged_oi_24h_pct": -15.0,
    "neutral_funding_abs_pct": 0.02,
    "crowded_long_ratio": 3.0,
    "crowded_short_ratio": 0.5,
    # veto: the "price +15%, OI +40%, funding hot" pattern
    "veto_price_24h_pct": 15.0,
    "veto_oi_24h_pct": 40.0,
}

# ---------------------------------------------------------------- social
SOCIAL = {
    "emerging_mentions_change": 20.0,
    "trending_mentions_change": 60.0,
    "euphoric_mentions_change": 200.0,
    "euphoric_sentiment": 80.0,
}

# ---------------------------------------------------------------- catalysts
CATALYST_LOOKBACK_DAYS = 30   # hacks/news older than this are ignored

# ---------------------------------------------------------------- paid API pacing
# Minimum seconds between calls, to stay under each plan's per-minute limit.
MIN_INTERVAL_S = {"coinglass": 2.1, "lunarcrush": 6.5, "tokenomist": 0.6, "goplus": 2.0}

# ---------------------------------------------------------------- AI output guard
MAX_VENUE_PRICE_GAP_PCT = 5.0   # CoinGecko vs Binance price: above this, different asset
MAX_ENTRY_DISTANCE_PCT = 10.0

# ---------------------------------------------------------------- cache TTLs (seconds)
CACHE_TTL = {
    "coingecko_markets": 5 * 60,
    "coingecko_global": 5 * 60,
    # Daily candles: 12h keeps the reading and the CoinGecko Demo quota (10k/month) safe.
    "coingecko_history": 12 * 3600,
    "coingecko_profile": 24 * 3600,
    "coingecko_category": 12 * 3600,
    "binance_perps": 6 * 3600,
    "contract_security": 24 * 3600,
    "fear_greed": 3600,
    "defillama_protocols": 15 * 60,
    "defillama_overviews": 15 * 60,
    "defillama_chains": 15 * 60,
    "defillama_tvl_history": 6 * 3600,
    "defillama_hacks": 3600,
    "binance_symbols": 6 * 3600,
    "derivatives": 5 * 60,
    # Tokenomist Pro allows ~1,000 requests/month: unlock schedules change rarely.
    "tokenomist_tokens": 24 * 3600,
    "tokenomics": 12 * 3600,
    "social": 15 * 60,
    # NewsData.io free tier: 200 credits/day and articles arrive ~12h late anyway.
    "news": 2 * 3600,
    "onchain": 15 * 60,
    "market_regime": 5 * 60,
}
