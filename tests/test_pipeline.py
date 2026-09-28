"""End-to-end funnel with every provider mocked: valid, watched, vetoed and failing candidates."""
import time
from contextlib import ExitStack
from unittest import mock

import pytest

import radar
from analysis import params as P
from providers.base import ProviderError
from tests.conftest import make_ai, make_coin, uptrend_closes

BTC = make_coin(id="bitcoin", symbol="btc", name="Bitcoin", current_price=100_000, market_cap=2e12,
                total_volume=50e9, fully_diluted_valuation=2.1e12,
                price_change_percentage_7d_in_currency=-1.0, price_change_percentage_30d_in_currency=1.0)
ETH = make_coin(id="ethereum", symbol="eth", name="Ethereum", current_price=4_000, market_cap=5e11,
                total_volume=20e9, fully_diluted_valuation=5e11)
PENDLE = make_coin()
UNI = make_coin(id="uniswap", symbol="uni", name="Uniswap", current_price=8.0, market_cap=5e9,
                total_volume=300e6, fully_diluted_valuation=6e9)
HACKED = make_coin(id="hacked", symbol="hkd", name="Hacked", current_price=1.0, market_cap=300e6,
                   total_volume=30e6, fully_diluted_valuation=300e6)
PRICES = {c["symbol"].upper(): c["current_price"] for c in (BTC, ETH, PENDLE, UNI, HACKED)}
HEALTHY_DERIVATIVES = {"source": "binance_futures", "funding_rate_pct": 0.01, "oi_change_24h": 2.0,
                       "long_short_ratio": 1.1}


def ai_for(price, **over):
    """AI answer with levels scaled to the asset price."""
    levels = {"entry_min_usd": price * 0.98, "entry_max_usd": price * 1.01,
              "target_usd": price * 1.2, "invalidation_usd": price * 0.9}
    return make_ai(**{**levels, **over})


def history(coin_id, days=90):
    price = next(c["current_price"] for c in (BTC, ETH, PENDLE, UNI, HACKED) if c["id"] == coin_id)
    closes = uptrend_closes(90, price * 0.7, price * 1.03)
    return {"prices": closes, "market_caps": [p * 1e8 for p in closes]}


def tickers(pairs):
    return {p: {"symbol": p, "last_price": PRICES[p[:-4]], "quote_volume": 1e7, "price_change_pct_24h": 0}
            for p in pairs}


@pytest.fixture
def mocked(request):
    """Patches every provider used by the radar. Returns the analyze mock."""
    coins = getattr(request, "param", None) or [BTC, ETH, PENDLE, UNI, HACKED]
    hacks = [{"name": "Hacked", "date": time.time() - 86400, "amount": 4e7, "defillamaId": None}]
    with ExitStack() as st:
        p = lambda obj, name, **kw: st.enter_context(mock.patch.object(obj, name, **kw))  # noqa: E731
        p(radar.cg, "markets", return_value=coins)
        p(radar.cg, "global_data", return_value={})
        p(radar.cg, "daily_history", side_effect=history)
        p(radar.cg, "coin_profile", return_value={"description": "Yield trading protocol.", "categories": ["DeFi"]})
        p(radar, "fear_greed", return_value={"value": 40, "label": "Medo"})
        p(radar.llama, "protocols", return_value=[])
        p(radar.llama, "chains", return_value=[])
        p(radar.llama, "overview", return_value=[])
        p(radar.llama, "hacks", return_value=hacks)
        p(radar.binance, "spot_pair", side_effect=lambda s: None if s.upper() in ("BTC", "ETH") else f"{s.upper()}USDT")
        p(radar.binance, "tickers", side_effect=tickers)
        p(radar.binance, "ticker", side_effect=lambda pair: tickers([pair])[pair])
        p(radar.futures, "derivatives", return_value=HEALTHY_DERIVATIVES)
        yield p(radar, "analyze", side_effect=lambda d: ai_for(radar.reference_price(d)))


def test_valid_opportunity_becomes_alert(mocked):
    report = radar.evaluate_candidates()
    alerts = {r["dossier"]["asset"]["symbol"]: r for r in report["results"]}
    assert set(alerts) == {"PENDLE", "UNI"}
    r = alerts["PENDLE"]
    assert r["decision"] == "alert"
    assert r["dossier"]["trend"]["state"] == "UPTREND"
    assert r["dossier"]["derivatives"]["positioning"] == "healthy"
    assert r["dossier"]["market_regime"]["status"] == "neutral"
    assert r["position_limit_brl"] == 600.0
    # AI confidence minus the data-coverage penalty
    assert r["confidence"] == 80 - radar.coverage_penalty(r["dossier"]["scores"])


def test_vetoed_asset_never_reaches_the_ai(mocked):
    report = radar.evaluate_candidates()
    assert any(v.startswith("HKD: critical_event") for v in report["vetoed"])
    seen = {c.args[0]["asset"]["symbol"] for c in mocked.call_args_list}
    assert "HKD" not in seen


def test_ai_failure_is_reported_not_silent(mocked):
    mocked.side_effect = RuntimeError("401 invalid key")
    report = radar.evaluate_candidates()
    assert report["results"] == []
    assert report["candidates"] == 2
    assert all("401" in e for e in report["errors"])


def test_low_confidence_becomes_watch(mocked):
    mocked.side_effect = lambda d: ai_for(radar.reference_price(d), confidence=50)
    report = radar.evaluate_candidates()
    assert report["results"] == []
    assert set(report["watch"]) == {"PENDLE", "UNI"}


def test_incoherent_levels_are_rejected(mocked):
    mocked.side_effect = lambda d: ai_for(radar.reference_price(d), invalidation_usd=radar.reference_price(d) * 2)
    report = radar.evaluate_candidates()
    assert report["results"] == []
    assert len(report["rejected"]) == 2


def test_ai_reject_is_respected(mocked):
    mocked.side_effect = lambda d: make_ai(decision="reject", entry_min_usd=None, entry_max_usd=None,
                                           target_usd=None, invalidation_usd=None)
    assert radar.evaluate_candidates()["results"] == []


def test_cooldown_skips_before_any_per_coin_call(mocked):
    report = radar.evaluate_candidates(skip_symbol=lambda s: s == "PENDLE")
    seen = {c.args[0]["asset"]["symbol"] for c in mocked.call_args_list}
    assert "PENDLE" not in seen
    assert [r["dossier"]["asset"]["symbol"] for r in report["results"]] == ["UNI"]
    # no history, derivatives or profile request either (review finding: only the AI call was checked)
    assert "pendle" not in [c.args[0] for c in radar.cg.daily_history.call_args_list]
    assert "PENDLE" not in [c.args[0] for c in radar.futures.derivatives.call_args_list]
    assert "pendle" not in [c.args[0] for c in radar.cg.coin_profile.call_args_list]


def test_risk_off_raises_the_bar_and_halves_the_limit(mocked):
    crash_btc = {**BTC, "price_change_percentage_7d_in_currency": -14.0, "price_change_percentage_30d_in_currency": -22.0}
    radar.cg.markets.return_value = [crash_btc, ETH, PENDLE, UNI]
    mocked.side_effect = lambda d: ai_for(radar.reference_price(d), confidence=75)
    report = radar.evaluate_candidates()
    assert report["regime"]["status"] == "risk_off"
    assert report["results"] == []          # 75 would pass in neutral, not in risk_off
    mocked.side_effect = lambda d: ai_for(radar.reference_price(d), confidence=100)
    radar.cache.clear()
    report = radar.evaluate_candidates()
    assert report["results"] and all(r["position_limit_brl"] == 300.0 for r in report["results"])


def test_runs_without_optional_apis_even_if_free_ones_fail(mocked):
    radar.futures.derivatives.side_effect = ProviderError("fapi down")
    radar.llama.hacks.side_effect = ProviderError("llama down")
    report = radar.evaluate_candidates()
    assert report["results"]
    d = report["results"][0]["dossier"]
    assert d["derivatives"] == {"available": False, "positioning": None}
    assert d["tokenomics"]["tokenomics_available"] is False
    assert d["social"]["state"] is None


def test_watchlist_symbol_gets_deep_analysis_past_the_cut(mocked):
    with mock.patch.object(P, "TREND_CANDIDATES", 1), mock.patch.object(P, "DEEP_CANDIDATES", 1):
        report = radar.evaluate_candidates(watchlist=["HKD"])
    assert any(v.startswith("HKD") for v in report["vetoed"])  # only the deep stage can find the hack


def test_analyze_symbol_with_veto_skips_the_ai(mocked):
    result = radar.analyze_symbol("hkd")
    assert result["decision"] == "reject"
    assert result["ai"] is None
    assert "critical_event" in result["dossier"]["vetoes"]
    mocked.assert_not_called()


def test_analyze_symbol_unknown_returns_none(mocked):
    assert radar.analyze_symbol("NOPE") is None


def test_same_ticker_different_asset_is_not_a_venue():
    # Real case: CoinGecko "AI" at $0.226 vs Binance AIUSDT at $0.0199.
    ticker = {"symbol": "AIUSDT", "last_price": 0.0199, "quote_volume": 1e6, "price_change_pct_24h": 1}
    with mock.patch.object(radar.binance, "spot_pair", return_value="AIUSDT"):
        assert radar.binance_venue("AI", 0.226, ticker) is None
        assert radar.binance_venue("AI", 0.0200, ticker) is not None


class TestLevels:
    def test_coherent(self):
        assert radar.check_levels(make_ai(), 2.60) == []

    def test_inverted_or_missing(self):
        assert radar.check_levels(make_ai(invalidation_usd=2.70), 2.60)
        assert radar.check_levels(make_ai(target_usd=None), 2.60)

    def test_target_below_current_price(self):
        # Review finding: a target under the price would be "hit" on the first candle.
        problems = radar.check_levels(make_ai(entry_min_usd=2.40, entry_max_usd=2.50, target_usd=2.55,
                                              invalidation_usd=2.30), 2.60)
        assert any("acima e invalidação abaixo" in p for p in problems)

    def test_far_entry(self):
        problems = radar.check_levels(make_ai(entry_min_usd=1.9, entry_max_usd=2.0, invalidation_usd=1.8), 2.60)
        assert any("zona de entrada" in p for p in problems)


def test_thin_evidence_does_not_reach_the_ai(mocked):
    # Review finding: the old test never crossed the real 0.5 boundary.
    bare = {k: v for k, v in UNI.items() if k not in ("circulating_supply", "total_supply", "max_supply",
                                                         "fully_diluted_valuation")}
    radar.cg.markets.return_value = [BTC, ETH, bare]
    radar.futures.derivatives.return_value = None
    radar.cg.daily_history.side_effect = lambda coin_id, days=None: {**history(coin_id), "market_caps": []}
    report = radar.evaluate_candidates()
    # only market (15) + trend (15) of 85 enabled weight: coverage 0.35 < 0.5
    assert report["candidates"] == 0
    mocked.assert_not_called()
