"""Analysis layers: regime, trend, fundamentals, tokenomics, derivatives, social, catalysts, scoring, vetoes."""
from analysis.catalysts import build_catalysts, classify
from analysis.derivatives import build_derivatives, derivatives_score
from analysis.filters import prefilter
from analysis.fundamentals import build_fundamentals, fundamentals_score
from analysis.market_regime import build_market_regime
from analysis.scoring import compute_scores, coverage_penalty
from analysis.social import build_social
from analysis.tokenomics import build_tokenomics, dilution_level, supply_growth_30d, tokenomics_score
from analysis.trend import btc_relative, compute_trend, ma_cross, rsi, trend_score
from analysis.vetoes import compute_vetoes
from tests.conftest import make_coin, uptrend_closes


def btc(p24=1.0, p7=2.0, p30=8.0, price=100_000):
    return {"id": "bitcoin", "current_price": price, "price_change_percentage_24h_in_currency": p24,
            "price_change_percentage_7d_in_currency": p7, "price_change_percentage_30d_in_currency": p30}


def eth(p7=3.0, p30=10.0, price=4_000):
    return {"id": "ethereum", "current_price": price, "price_change_percentage_7d_in_currency": p7,
            "price_change_percentage_30d_in_currency": p30}


# ------------------------------------------------------------------ market regime
class TestMarketRegime:
    def test_risk_on_when_btc_rising_above_ma50(self):
        r = build_market_regime(btc(p7=4, p30=10), eth(), {}, uptrend_closes(90, 80_000, 100_000))
        assert r["status"] == "risk_on"
        assert r["adjustments"]["min_confidence"] == 65

    def test_risk_off_on_btc_crash_raises_the_bar(self):
        r = build_market_regime(btc(p24=-4, p7=-12, p30=-15), eth(p7=-15, p30=-20), {}, None)
        assert r["status"] == "risk_off"
        assert r["adjustments"]["min_confidence"] == 80
        assert r["adjustments"]["min_score"] == 65
        assert r["adjustments"]["position_factor"] == 0.5
        assert "BTC" in r["reason"]

    def test_risk_off_on_total_market_crash_day(self):
        r = build_market_regime(btc(p7=0, p30=2), eth(), {"market_cap_change_percentage_24h_usd": -8}, None)
        assert r["status"] == "risk_off"

    def test_neutral_without_clear_trend(self):
        r = build_market_regime(btc(p7=-1, p30=1), eth(), {}, None)
        assert r["status"] == "neutral"

    def test_eth_btc_ratio_and_change(self):
        r = build_market_regime(btc(p30=10), eth(p30=21), {}, None)
        assert r["eth_btc_ratio"] == 0.04
        assert r["eth_btc_30d_pct"] == 10.0

    def test_missing_btc_is_neutral_not_crash(self):
        r = build_market_regime(None, None, None, None)
        assert r["status"] == "neutral" and r["available"] is False


# ------------------------------------------------------------------ trend
class TestTrend:
    def test_pullback_in_uptrend_scores_high(self):
        closes = uptrend_closes()
        price = closes[-1] * 0.97  # dipped below MA20? still above MA50
        t = compute_trend(closes, price, p24=-2, p7=-5, p30=10)
        assert t["state"] == "UPTREND"
        assert t["pullback_in_uptrend"] is True
        assert trend_score(t) == 85

    def test_structural_decline_is_downtrend(self):
        closes = list(reversed(uptrend_closes()))
        t = compute_trend(closes, closes[-1], p24=-1, p7=-5, p30=-15)
        assert t["state"] == "DOWNTREND"
        assert trend_score(t) == 25

    def test_capitulation(self):
        closes = [3.0] * 60 + [2.8 - i * 0.05 for i in range(30)]
        price = 1.2
        t = compute_trend(closes, price, p7=-25, p30=-55)
        assert t["state"] == "CAPITULATION"

    def test_extended_uptrend_is_penalized(self):
        closes = uptrend_closes()
        t = compute_trend(closes, closes[-1] * 1.25, p24=5, p7=20, p30=40)
        assert t["extended"] is True
        assert trend_score(t) == 45

    def test_fallback_without_history_is_capped(self):
        t = compute_trend(None, 2.6, p24=-2, p7=-5, p30=15)
        assert t["source"] == "fallback_7d_30d"
        assert trend_score(t) <= 70

    def test_no_data_at_all(self):
        t = compute_trend(None, 2.6)
        assert trend_score(t) is None


# ------------------------------------------------------------------ fundamentals
class TestFundamentals:
    CTX = {"kind": "protocol", "name": "Pendle V2", "category": "Yield", "tvl": 1.2e9,
           "change_7d": 9.0, "borrowed": None}

    def test_price_down_revenue_up_divergence(self):
        flows = {"revenue": {"total_30d": 3e6, "growth_30d": 25.0},
                 "fees": {"total_30d": 9e6, "growth_30d": 30.0}}
        f = build_fundamentals(self.CTX, flows, 12.0, 500e6, p30=-15)
        assert "price_down_revenue_up" in f["divergences"]
        assert "price_down_usage_up" in f["divergences"]
        assert f["market_cap_to_annual_revenue"] == round(500e6 / (3e6 * 365 / 30), 1)
        assert fundamentals_score(f) > 70

    def test_no_defillama_footprint_is_unknown_not_bad(self):
        f = build_fundamentals(None, {}, None, 500e6, p30=-15)
        assert f == {"available": False}
        assert fundamentals_score(f) is None


# ------------------------------------------------------------------ tokenomics
class TestTokenomics:
    def test_supply_growth_from_caps_and_prices(self):
        prices = [1.0] * 31
        caps = [100.0] * 30 + [110.0]  # supply +10%
        assert supply_growth_30d(caps, prices) == 10.0

    def test_dilution_levels_follow_thresholds(self):
        assert dilution_level(1) == "low"
        assert dilution_level(3) == "medium"
        assert dilution_level(7) == "high"
        assert dilution_level(12) == "critical"

    def test_unlock_schedule_takes_precedence(self):
        unlocks = {"available": True, "source": "tokenomist", "unlock_30d_pct": 12.0, "unlock_7d_pct": 12.0}
        t = build_tokenomics(make_coin(), supply_growth=0.5, unlocks=unlocks, holders_revenue_30d=None)
        assert t["dilution_risk"] == "critical"
        assert t["dilution_basis"] == "unlock_schedule"
        assert t["tokenomics_available"] is True

    def test_without_tokenomist_uses_trailing_growth(self):
        t = build_tokenomics(make_coin(), supply_growth=1.0, unlocks=None, holders_revenue_30d=5e5)
        assert t["tokenomics_available"] is False
        assert t["dilution_basis"] == "trailing_supply_growth"
        assert t["value_capture"]["available"] is True
        assert tokenomics_score(t) > 60

    def test_value_capture_absent_is_unknown(self):
        t = build_tokenomics(make_coin(), None, None, None)
        assert t["value_capture"] == {"available": False, "type": None}


# ------------------------------------------------------------------ derivatives
class TestDerivatives:
    def test_crowded_long_pattern(self):
        d = build_derivatives({"funding_rate_pct": 0.08, "oi_change_24h": 60}, p24=15, p7=20)
        assert d["positioning"] == "crowded_long"
        assert derivatives_score(d) == 20

    def test_deleveraged_after_correction(self):
        d = build_derivatives({"funding_rate_pct": 0.005, "oi_change_24h": -20}, p24=-3, p7=-10)
        assert d["positioning"] == "deleveraged"
        assert derivatives_score(d) == 80

    def test_no_data(self):
        assert build_derivatives(None, 0, 0) == {"available": False, "positioning": None}


# ------------------------------------------------------------------ social + catalysts
class TestSocialAndCatalysts:
    def test_social_states(self):
        assert build_social({"mentions_change_24h": 30})["state"] == "emerging"
        assert build_social({"mentions_change_24h": 250, "sentiment": 90})["state"] == "euphoric"
        assert build_social({"mentions_change_24h": 5})["state"] == "quiet"
        assert build_social(None)["state"] is None

    def test_headline_classification(self):
        assert classify("Protocol X drained in $40M exploit") == "critical"
        assert classify("Binance will delist ABC") == "critical"
        assert classify("SEC sues foundation") == "negative"
        assert classify("Coinbase listing for XYZ") == "positive"
        assert classify("Weekly market wrap") is None

    def test_news_headline_informs_but_never_vetoes(self):
        # Review finding: "Binance will delist ABC/BTC" tagged BTC would veto Bitcoin.
        c = build_catalysts([], [{"title": "Binance Will Delist ABC/BTC Spot Trading Pairs"}], ["newsdata"])
        assert c["critical_risk"] is False
        assert c["negative"][0]["severity"] == "critical"

    def test_recent_hack_is_critical(self):
        c = build_catalysts([{"name": "Pendle", "date": 1790000000, "amount_usd": 5e6}], [], ["defillama_hacks"])
        assert c["critical_risk"] is True
        assert c["negative"][0]["title"].startswith("Hack/exploit")


# ------------------------------------------------------------------ scoring + vetoes
def dossier(**over):
    d = {
        "market": {"market_cap_usd": 500e6, "daily_volume_usd": 60e6, "price_change_24h_pct": -2.0},
        "market_regime": {"status": "neutral"},
        "trend": {"state": "UPTREND", "pullback_in_uptrend": True, "source": "daily_history"},
        "fundamentals": {"available": False},
        "tokenomics": {"circulating_pct": 60.0, "fdv_mcap_ratio": 1.2, "dilution_risk": "low",
                       "dilution_basis": "trailing_supply_growth", "supply_growth_30d": 1.0},
        "derivatives": {"available": False, "positioning": None},
        "onchain": {"available": False, "state": None},
        "social": {"available": False, "state": None},
        "catalysts": {"critical_risk": False, "negative": []},
    }
    d.update(over)
    return d


class TestScoring:
    def test_missing_layers_are_reweighted_not_zeroed(self):
        s = compute_scores(dossier(), {"market_quality", "trend_quality", "fundamentals", "tokenomics", "derivatives"})
        assert s["fundamentals"] is None and s["derivatives"] is None
        # only market (15), trend (15) and tokenomics (20) count
        expected = round((15 * s["market_quality"] + 15 * s["trend_quality"] + 20 * s["tokenomics"]) / 50, 1)
        assert s["composite"] == expected
        assert s["data_coverage"] == round(50 / 85, 2)
        assert coverage_penalty(s) == round((1 - s["data_coverage"]) * 20)

    def test_disabled_layers_do_not_lower_coverage(self):
        s = compute_scores(dossier(), {"market_quality", "trend_quality", "tokenomics"})
        assert s["data_coverage"] == 1.0

    def test_risk_rises_with_bad_context(self):
        calm = compute_scores(dossier())["risk"]
        bad = compute_scores(dossier(market_regime={"status": "risk_off"},
                                     trend={"state": "DOWNTREND", "source": "daily_history"},
                                     derivatives={"available": True, "positioning": "crowded_long"}))["risk"]
        assert bad > calm + 40


class TestVetoes:
    def test_critical_unlock(self):
        d = dossier(tokenomics={"unlock_30d_pct": 12.0, "dilution_risk": "critical", "dilution_basis": "unlock_schedule"})
        assert compute_vetoes(d) == ["critical_unlock"]

    def test_trailing_inflation_counts_even_with_tokenomist(self):
        # Review finding: with Tokenomist on, a +25% supply (linear emission, no cliffs) scored "low".
        unlocks = {"available": True, "source": "tokenomist", "unlock_30d_pct": 0.0}
        t = build_tokenomics(make_coin(), supply_growth=25.0, unlocks=unlocks, holders_revenue_30d=None)
        assert t["dilution_risk"] == "critical" and t["dilution_basis"] == "trailing_supply_growth"
        assert "critical_supply_inflation" in compute_vetoes(dossier(tokenomics=t))

    def test_critical_event(self):
        d = dossier(catalysts={"critical_risk": True, "negative": [{"title": "hack"}]})
        assert "critical_event" in compute_vetoes(d)

    def test_risk_off_plus_downtrend(self):
        d = dossier(market_regime={"status": "risk_off"}, trend={"state": "DOWNTREND"})
        assert "risk_off_downtrend" in compute_vetoes(d)
        # the same downtrend in a neutral market is a low score, not a veto
        assert compute_vetoes(dossier(trend={"state": "DOWNTREND"})) == []

    def test_low_liquidity_and_extreme_dilution(self):
        d = dossier(market={"market_cap_usd": 500e6, "daily_volume_usd": 1e6},
                    tokenomics={"fdv_mcap_ratio": 6.0})
        assert set(compute_vetoes(d)) == {"low_liquidity", "extreme_dilution"}

    def test_overheated_leverage(self):
        d = dossier(market={"market_cap_usd": 500e6, "daily_volume_usd": 60e6, "price_change_24h_pct": 16},
                    derivatives={"available": True, "funding_rate_pct": 0.06, "oi_change_24h": 55})
        assert "overheated_leverage" in compute_vetoes(d)

    def test_supply_inflation_from_trailing_growth(self):
        d = dossier(tokenomics={"dilution_basis": "trailing_supply_growth", "supply_growth_30d": 12.0})
        assert "critical_supply_inflation" in compute_vetoes(d)


# ------------------------------------------------------------------ cheap filters
class TestFilters:
    def test_stablecoins_listed_and_detected(self):
        assert prefilter(make_coin(symbol="usd1"))
        assert prefilter(make_coin(symbol="newusd", current_price=0.9995,
                                   price_change_percentage_7d_in_currency=0.02,
                                   price_change_percentage_30d_in_currency=0.1))
        assert prefilter(make_coin(symbol="paxg"))

    def test_liquid_coin_passes(self):
        assert prefilter(make_coin()) is None

    def test_size_liquidity_dilution(self):
        assert prefilter(make_coin(market_cap=50e6)) == "market cap below threshold"
        assert prefilter(make_coin(total_volume=1e6)) == "volume below threshold"
        assert prefilter(make_coin(fully_diluted_valuation=2e9)) == "FDV dilution too high"


def test_parabolic_move_scores_like_a_late_entry():
    # Real case: NEAR was 101% above its MA50 and still scored 45 on trend.
    closes = uptrend_closes(90, 1.0, 1.5)
    t = compute_trend(closes, closes[-1] * 1.8, p24=3, p7=25, p30=60)
    assert t["parabolic"] is True
    assert trend_score(t) == 20


class TestTechnicalAndRelative:
    def test_rsi_extremes_and_midpoint(self):
        assert rsi(list(range(1, 30))) == 100.0
        assert rsi(list(range(30, 1, -1))) == 0.0
        assert rsi([1.0] * 5) is None

    def test_golden_cross_is_detected_when_recent(self):
        closes = [10.0] * 212 + [10 + i * 0.5 for i in range(1, 9)]  # MA50 overtakes MA200 in the last 8 days
        assert ma_cross(closes) == "golden_cross_recent"
        assert ma_cross(closes + [closes[-1]] * 60) == "above"
        assert ma_cross([10.0] * 100) is None  # not enough history

    def test_btc_relative_discount(self):
        closes = [1.0] * 100
        btc = [100.0] * 50 + [200.0] * 50   # asset halved against BTC
        r = btc_relative(closes, btc)
        assert r["from_high_pct"] == -50.0 and r["range_position"] == 0.0

    def test_trend_exposes_ma200_and_rsi_with_long_history(self):
        closes = uptrend_closes(220, 1.0, 2.0)
        t = compute_trend(closes, closes[-1], p7=2, p30=8)
        assert t["ma50_ma200"] == "above" and t["dist_ma200_pct"] > 0
        assert t["rsi_daily"] == 100.0 and t["rsi_weekly"] == 100.0
