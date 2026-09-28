"""Pre-Binance mode: providers, funnel and tracking outside Binance. All HTTP mocked."""
import time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import radar
import tracker
from database import Database
from providers.binance_futures import BinanceFuturesClient
from providers.coingecko import CoinGeckoClient
from providers.security import normalize_evm, normalize_solana, pick_contract
from tests.conftest import make_ai, make_coin, uptrend_closes
from tests.test_pipeline import BTC, ETH, PENDLE, history, tickers

# Not on Binance spot, inside the pre-listing range.
FLUID = make_coin(id="instadapp", symbol="fluid", name="Fluid", current_price=4.0, market_cap=300e6,
                  total_volume=12e6, fully_diluted_valuation=400e6)
TINY = make_coin(id="tiny", symbol="tiny", name="Tiny", current_price=0.5, market_cap=15e6,
                 total_volume=900e3, fully_diluted_valuation=18e6)
FLUID_PROFILE = {
    "description": "Fluid is a DeFi protocol combining lending and a DEX.",
    "categories": ["Decentralized Finance (DeFi)", "Binance Alpha Spotlight"],
    "cex": ["Upbit", "Bybit", "Gate"], "cex_ids": ["upbit", "bybit_spot", "gdax", "gate"], "dex": ["Uniswap V3"],
    "platforms": {"ethereum": "0xabc"},
}


# ------------------------------------------------------------------ providers
class TestProviders:
    def test_profile_splits_cex_and_dex_by_volume(self):
        raw = {
            "description": {"en": "<p>Fluid <b>is</b> DeFi.</p>"},
            "categories": ["DeFi", "Binance Alpha Spotlight"],
            "platforms": {"ethereum": "0xabc", "": ""},
            "tickers": [
                {"market": {"name": "Bybit", "identifier": "bybit_spot"}, "base": "FLUID", "target": "USDT",
                 "converted_volume": {"usd": 100}},
                {"market": {"name": "Upbit", "identifier": "upbit"}, "base": "FLUID", "target": "KRW",
                 "converted_volume": {"usd": 300}},
                {"market": {"name": "Uniswap V3 (Ethereum)", "identifier": "uniswap_v3"},
                 "base": "0X6F40D4A6237C257FFF2DB00FA0510DEEECD303EB", "target": "0XC02A", "converted_volume": {"usd": 50}},
                {"market": {"name": "Shady", "identifier": "shady"}, "base": "FLUID", "target": "USDT",
                 "converted_volume": {"usd": 9e9}, "is_anomaly": True},
            ],
        }
        p = CoinGeckoClient.normalize_profile(raw)
        assert p["description"] == "Fluid is DeFi."
        assert p["cex"] == ["Upbit", "Bybit"]          # by volume, anomaly dropped
        assert p["dex"] == ["Uniswap V3 (Ethereum)"]
        assert p["platforms"] == {"ethereum": "0xabc"}

    def test_markets_pages_until_limit(self):
        client = CoinGeckoClient()
        pages = {1: [{"id": f"a{i}"} for i in range(250)], 2: [{"id": f"b{i}"} for i in range(250)],
                 3: [{"id": "c0"}]}
        with mock.patch.object(client, "_get", side_effect=lambda path, params: pages[params["page"]]):
            assert len(client.markets(1000)) == 501   # stops at the short page
            assert len(client.markets(300)) == 300

    def test_perp_bases_strip_the_1000x_prefix(self):
        client = BinanceFuturesClient()
        payload = {"symbols": [
            {"baseAsset": "1000PEPE", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
            {"baseAsset": "FLUID", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "TRADING"},
            {"baseAsset": "OLD", "quoteAsset": "USDT", "contractType": "PERPETUAL", "status": "SETTLING"},
            {"baseAsset": "BTC", "quoteAsset": "USDT", "contractType": "CURRENT_QUARTER", "status": "TRADING"},
        ]}
        with mock.patch.object(client, "_get", return_value=payload):
            assert client.perp_bases() == {"PEPE", "FLUID"}

    def test_contract_pick_prefers_evm_then_solana(self):
        assert pick_contract({"solana": "So1", "base": "0xb", "ethereum": "0xe"}) == ("ethereum", "0xe")
        assert pick_contract({"solana": "So1"}) == ("solana", "So1")
        assert pick_contract({}) is None   # native coin: nothing to check

    def test_goplus_evm_severe_vs_warning(self):
        # Real PEPE flags: pausable + blacklist exist but it is not a scam -> warnings only.
        legit = normalize_evm({"is_honeypot": "0", "sell_tax": "0", "transfer_pausable": "1", "is_blacklisted": "1",
                               "is_open_source": "1", "holders": [{"percent": "0.08", "is_contract": 0, "is_locked": 0}]})
        assert legit["severe"] == [] and set(legit["warnings"]) == {"transfers_can_be_paused", "blacklist_function"}
        assert legit["top10_holders_pct"] == 8.0
        scam = normalize_evm({"is_honeypot": "1", "sell_tax": "0.25", "is_open_source": "0"})
        assert {"honeypot", "high_sell_tax"} <= set(scam["severe"]) and scam["sell_tax_pct"] == 25.0

    def test_goplus_solana(self):
        s = normalize_solana({"freezable": {"status": "1"}, "mintable": {"status": "1"},
                              "balance_mutable_authority": {"status": "0"}, "non_transferable": "0"})
        assert s["severe"] == ["freeze_authority"] and s["warnings"] == ["mintable"]


# ------------------------------------------------------------------ funnel
@pytest.fixture
def pre_mocked():
    hacks = [{"name": "Pendle", "date": time.time() - 86400 * 400}]  # too old to matter
    prices = {**{c["symbol"].upper(): c["current_price"] for c in (BTC, ETH, PENDLE)}}

    def hist(coin_id, days=None):
        if coin_id in ("instadapp", "tiny"):
            price = FLUID["current_price"] if coin_id == "instadapp" else TINY["current_price"]
            closes = uptrend_closes(90, price * 0.7, price * 1.03)
            return {"prices": closes, "market_caps": [p * 1e8 for p in closes]}
        return history(coin_id)

    with ExitStack() as st:
        p = lambda obj, name, **kw: st.enter_context(mock.patch.object(obj, name, **kw))  # noqa: E731
        p(radar.cg, "markets", return_value=[BTC, ETH, PENDLE, FLUID, TINY])
        p(radar.cg, "global_data", return_value={})
        p(radar.cg, "daily_history", side_effect=hist)
        p(radar.cg, "coin_profile", side_effect=lambda cid: FLUID_PROFILE if cid == "instadapp" else
          {"description": "x", "categories": [], "cex": ["MEXC"], "cex_ids": ["mxc"], "dex": [], "platforms": {}})
        p(radar, "fear_greed", return_value=None)
        p(radar.llama, "protocols", return_value=[])
        p(radar.llama, "chains", return_value=[])
        p(radar.llama, "overview", return_value=[])
        p(radar.llama, "hacks", return_value=hacks)
        p(radar.binance, "spot_pair", side_effect=lambda s: f"{s.upper()}USDT" if s.upper() == "PENDLE" else None)
        p(radar.binance, "tickers", side_effect=lambda pairs: tickers(pairs) if all(x[:-4] in prices for x in pairs) else {})
        p(radar.futures, "derivatives", return_value={"source": "binance_futures", "funding_rate_pct": 0.01,
                                                      "oi_change_24h": 2.0})
        p(radar.futures, "perp_bases", return_value={"FLUID"})
        security = p(radar, "token_security", return_value={"severe": [], "warnings": [], "top10_holders_pct": 20.0})
        analyze = p(radar, "analyze", side_effect=lambda d: make_ai(
            entry_min_usd=radar.reference_price(d) * 0.98, entry_max_usd=radar.reference_price(d) * 1.01,
            target_usd=radar.reference_price(d) * 1.3, invalidation_usd=radar.reference_price(d) * 0.85))
        yield {"analyze": analyze, "security": security}


def test_both_modes_are_scanned_with_labels(pre_mocked):
    report = radar.evaluate_candidates()
    modes = {r["dossier"]["asset"]["symbol"]: r["dossier"]["mode"] for r in report["results"]}
    assert modes.get("PENDLE") == "binance"
    assert modes.get("FLUID") == "pre_listing"
    assert report["universe_pre_listing"] == 2
    fluid = next(r for r in report["results"] if r["dossier"]["asset"]["symbol"] == "FLUID")
    d = fluid["dossier"]
    assert d["listing"]["binance_alpha"] and d["listing"]["binance_perp_without_spot"]
    assert d["venue"]["binance"] is False and d["venue"]["cex"] == ["Upbit", "Bybit", "Gate"]
    assert d["contract"]["status"] == "ok"
    assert d["scores"]["listing"] is not None


def test_small_cap_needs_more_evidence(pre_mocked):
    # TINY ($15M) has no fundamentals and no derivatives data -> coverage below the 0.7 small-cap bar.
    radar.futures.derivatives.side_effect = lambda s: None if s == "TINY" else {
        "source": "binance_futures", "funding_rate_pct": 0.01, "oi_change_24h": 2.0}
    report = radar.evaluate_candidates()
    seen = {c.args[0]["asset"]["symbol"] for c in pre_mocked["analyze"].call_args_list}
    assert "TINY" not in seen and "FLUID" in seen
    assert report["universe_pre_listing"] == 2


def test_dangerous_contract_is_vetoed_before_the_ai(pre_mocked):
    pre_mocked["security"].return_value = {"severe": ["honeypot"], "warnings": []}
    report = radar.evaluate_candidates()
    assert any(v.startswith("FLUID: contract_risk") for v in report["vetoed"])
    seen = {c.args[0]["asset"]["symbol"] for c in pre_mocked["analyze"].call_args_list}
    assert "FLUID" not in seen


def test_pre_listing_can_be_turned_off(pre_mocked):
    with mock.patch.object(radar, "settings", mock.MagicMock(wraps=radar.settings, enable_pre_listing=False,
                                                             top_coins_to_scan=200, pre_listing_top_n=1000,
                                                             max_ai_candidates=5)):
        report = radar.evaluate_candidates()
    assert report["universe_pre_listing"] == 0
    assert all(r["dossier"]["mode"] == "binance" for r in report["results"])


def test_analyze_symbol_off_binance_uses_pre_listing_mode(pre_mocked):
    result = radar.analyze_symbol("fluid")
    assert result["dossier"]["mode"] == "pre_listing"
    assert result["dossier"]["listing"]["binance_alpha"] is True


# ------------------------------------------------------------------ tracking outside Binance
NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def _pre_signal(db):
    return db.save_opportunity({"symbol": "FLUID", "coin_id": "instadapp", "mode": "pre_listing", "price_usd": 4.0,
                                "entry_min_usd": 3.9, "entry_max_usd": 4.05, "target_usd": 5.2,
                                "invalidation_usd": 3.4})


def test_pre_listing_signal_tracked_on_coingecko_and_listing_detected():
    db = Database()
    opp_id = _pre_signal(db)
    binance, cg = mock.MagicMock(), mock.MagicMock()
    binance.spot_pair.return_value = "FLUIDUSDT"          # it just got listed
    start = datetime.now(timezone.utc)
    cg.price_series.return_value = [
        {"open_time": (start + timedelta(hours=1)).timestamp() * 1000, "high": 4.0, "low": 4.0, "close": 4.0},
        {"open_time": (start + timedelta(hours=2)).timestamp() * 1000, "high": 5.3, "low": 5.3, "close": 5.3},
    ]
    events = tracker.check_open(db, binance, cg, now=start + timedelta(hours=3))
    kinds = [(e["event"], e.get("status")) for e in events]
    assert kinds == [("listed", tracker.OPEN), ("closed", tracker.TARGET_HIT)]
    binance.klines.assert_not_called()
    assert db._opps[opp_id]["binance_listed_at"] is not None


def test_listing_is_announced_once_and_coingecko_is_throttled():
    db = Database()
    _pre_signal(db)
    binance, cg = mock.MagicMock(), mock.MagicMock()
    binance.spot_pair.return_value = "FLUIDUSDT"
    cg.price_series.return_value = []
    t0 = datetime.now(timezone.utc) + timedelta(hours=1)
    first = tracker.check_open(db, binance, cg, now=t0)
    second = tracker.check_open(db, binance, cg, now=t0 + timedelta(hours=1))
    assert [e["event"] for e in first] == ["listed"] and second == []
    assert cg.price_series.call_count == 1                 # second run inside the 6h window: no call
    tracker.check_open(db, binance, cg, now=t0 + timedelta(hours=7))
    assert cg.price_series.call_count == 2


def test_stats_count_listings():
    rows = [{"status": "OPEN", "detected_at": NOW, "mode": "pre_listing", "binance_listed_at": NOW},
            {"status": "OPEN", "detected_at": NOW, "mode": "pre_listing", "binance_listed_at": None},
            {"status": "OPEN", "detected_at": NOW, "mode": "binance"}]
    assert tracker.stats(rows, NOW)["pre_listing"] == {"total": 2, "listed": 1}


# ------------------------------------------------------------------ fixes found on live data
@pytest.mark.parametrize("name,coin_id", [
    ("NVIDIA • Robinhood Token", "nvidia-robinhood-tokenized-stock"),
    ("MicroStrategy (Ondo Tokenized Stock)", "microstrategy-ondo-tokenized-stock"),
    ("Tesla xStock", "tesla-xstock"),
    ("Bitget Wrapped BTC", "bitget-wrapped-btc"),
    ("Frax Staked frxUSD", "staked-frax-usd"),
    ("Matrixdock Gold", "matrixdock-gold"),
    ("3Jane USD3", "3jane-usd3"),
    ("JPY Coin", "jpy-coin"),
    ("Pharaoh Liquid Staking Token", "pharaoh-liquid-staking-token"),
])
def test_not_crypto_native_assets_are_filtered(name, coin_id):
    from analysis.filters import prefilter
    coin = make_coin(name=name, id=coin_id, symbol="zzz", market_cap=200e6, total_volume=5e6,
                     fully_diluted_valuation=200e6)
    assert prefilter(coin, "pre_listing") == "tokenized, wrapped or pegged asset"


def test_real_crypto_names_are_not_caught():
    from analysis.filters import prefilter
    for name, cid in [("Velodrome Finance", "velodrome-finance"), ("Goldfinch", "goldfinch"),
                      ("Stake DAO", "stake-dao"), ("Akash Network", "akash-network"), ("Fluid", "instadapp")]:
        coin = make_coin(name=name, id=cid, symbol="zzz", market_cap=200e6, total_volume=5e6,
                         fully_diluted_valuation=200e6)
        assert prefilter(coin, "pre_listing") is None, name


def test_renamed_ticker_already_on_binance_is_vetoed(pre_mocked):
    # Real case: BTT is on Binance as BTTC, so "BTTUSDT not on spot" was a false pre-listing.
    profile = {**FLUID_PROFILE, "cex_ids": ["binance", "upbit"]}
    radar.cg.coin_profile.side_effect = lambda cid: profile
    report = radar.evaluate_candidates()
    assert any(v.startswith("FLUID: ") and "already_on_binance" in v for v in report["vetoed"])


def test_pre_listing_without_fundamentals_or_binance_signal_skips_the_ai(pre_mocked):
    # Real case: CASHCAT reached the AI with no listing signal and no fundamentals.
    radar.futures.perp_bases.return_value = set()
    radar.cg.coin_profile.side_effect = lambda cid: {**FLUID_PROFILE, "categories": ["Meme"]}
    radar.evaluate_candidates()
    seen = {c.args[0]["asset"]["symbol"] for c in pre_mocked["analyze"].call_args_list}
    assert "FLUID" not in seen and "PENDLE" in seen
