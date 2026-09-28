"""Signal tracking: target, invalidation, expiry, persistence and the /status numbers."""
from datetime import datetime, timedelta, timezone
from unittest import mock

import tracker
from database import Database

NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def opp(**over):
    base = {"id": "o1", "symbol": "PENDLE", "price_usd": 2.60, "entry_min_usd": 2.55, "entry_max_usd": 2.65,
            "target_usd": 3.10, "invalidation_usd": 2.35,
            "detected_at": NOW - timedelta(days=2), "expires_at": NOW + timedelta(days=12)}
    base.update(over)
    return base


def candle(low, high, close=None, at=None):
    at = at or NOW - timedelta(days=1)
    return {"open_time": at.timestamp() * 1000, "low": low, "high": high,
            "close": close if close is not None else (low + high) / 2}


class TestEvaluate:
    def test_target_hit_after_entry(self):
        status, price, entered = tracker.evaluate(opp(), [candle(2.5, 2.8), candle(2.9, 3.15)], NOW)
        assert (status, price, entered) == (tracker.TARGET_HIT, 3.10, True)

    def test_invalidation(self):
        status, price, _ = tracker.evaluate(opp(), [candle(2.5, 2.8), candle(2.30, 2.5)], NOW)
        assert (status, price) == (tracker.INVALIDATED, 2.35)

    def test_first_level_touched_wins(self):
        assert tracker.evaluate(opp(), [candle(2.5, 2.6), candle(2.3, 2.6), candle(2.9, 3.2)], NOW)[0] == tracker.INVALIDATED

    def test_both_levels_in_one_candle_counts_as_loss(self):
        assert tracker.evaluate(opp(), [candle(2.30, 3.20)], NOW)[0] == tracker.INVALIDATED

    def test_entry_candle_cannot_confirm_a_win(self):
        assert tracker.evaluate(opp(), [candle(2.6, 3.2)], NOW)[0] == tracker.OPEN

    def test_straight_to_target_without_entry_is_not_a_win(self):
        # Review finding: a pullback entry that never filled was counted as a win.
        o = opp(entry_min_usd=2.40, entry_max_usd=2.50)
        status, _, entered = tracker.evaluate(o, [candle(2.6, 2.9), candle(2.9, 3.3)], NOW)
        assert (status, entered) == (tracker.OPEN, False)

    def test_candles_after_expiry_are_ignored(self):
        # Review finding: a signal expired on day 14 became TARGET_HIT on day 30.
        o = opp(detected_at=NOW - timedelta(days=30), expires_at=NOW - timedelta(days=16))
        candles = [candle(2.5, 2.7, 2.62, at=NOW - timedelta(days=20)), candle(3.0, 3.3, at=NOW - timedelta(days=1))]
        status, price, entered = tracker.evaluate(o, candles, NOW)
        assert (status, price, entered) == (tracker.EXPIRED, 2.62, True)

    def test_still_open(self):
        assert tracker.evaluate(opp(), [candle(2.5, 2.9)], NOW) == (tracker.OPEN, None, True)


def test_result_measured_from_entry_zone():
    assert tracker.result_pct(tracker.entry_price(opp()), 3.10) == round((3.10 / 2.60 - 1) * 100, 2)


def test_check_open_updates_db_and_reports_changes():
    db = Database()
    opp_id = db.save_opportunity({"symbol": "PENDLE", "price_usd": 2.60, "entry_min_usd": 2.55,
                                  "entry_max_usd": 2.65, "target_usd": 3.10, "invalidation_usd": 2.35})
    binance = mock.MagicMock()
    binance.klines.return_value = [candle(2.5, 2.7), candle(2.9, 3.2)]
    changed = tracker.check_open(db, binance)
    assert [(c["id"], c["status"], c["result_pct"]) for c in changed] == [(opp_id, tracker.TARGET_HIT, 19.23)]
    assert db.open_opportunities() == []            # closed: not re-checked or re-notified
    assert tracker.check_open(db, binance) == []


def test_check_open_survives_missing_candles():
    db = Database()
    db.save_opportunity({"symbol": "PENDLE", "price_usd": 2.60, "target_usd": 3.10, "invalidation_usd": 2.35})
    binance = mock.MagicMock()
    binance.klines.side_effect = RuntimeError("binance down")
    assert tracker.check_open(db, binance) == []
    assert len(db.open_opportunities()) == 1


def test_stats_shows_bad_results_and_recent_window():
    rows = (
        [{"status": tracker.TARGET_HIT, "detected_at": NOW - timedelta(days=5)}] * 6
        + [{"status": tracker.INVALIDATED, "detected_at": NOW - timedelta(days=5)}] * 2
        + [{"status": tracker.OPEN, "detected_at": NOW - timedelta(days=1)}]
        + [{"status": tracker.TARGET_HIT, "detected_at": NOW - timedelta(days=60)}] * 7
        + [{"status": tracker.INVALIDATED, "detected_at": NOW - timedelta(days=60)}] * 4
        + [{"status": tracker.EXPIRED, "detected_at": NOW - timedelta(days=60)}]
    )
    st = tracker.stats(rows, NOW)
    assert st["all"]["total"] == 21
    assert st["all"]["TARGET_HIT"] == 13 and st["all"]["INVALIDATED"] == 6 and st["all"]["EXPIRED"] == 1
    assert st["all"]["win_rate"] == 68  # 13 / 19; expired is neither win nor loss
    assert st["recent"]["total"] == 9 and st["recent"]["OPEN"] == 1


def test_stats_without_closed_signals():
    assert tracker.stats([], NOW)["all"]["win_rate"] is None


def test_watchlist_in_memory():
    db = Database()
    db.watch("pendle")
    db.watch("uni")
    db.watch("pendle", active=False)
    assert db.watchlist() == ["UNI"]


def test_cooldown_without_supabase():
    db = Database()
    assert not db.recent_alert_exists("PENDLE")
    db.save_alert(None, "pendle", "12345", "msg")
    assert db.recent_alert_exists("PENDLE")


class FakeSupabase:
    """Minimal PostgREST chain: select/eq/gte/limit/execute; update can be made to fail."""
    def __init__(self, rows, fail_update=False):
        self.rows, self.fail_update, self.selects = rows, fail_update, 0

    def table(self, name):
        return self

    def select(self, *a):
        self.selects += 1
        self._mode = "select"
        return self

    def update(self, *a):
        self._mode = "update"
        return self

    def eq(self, *a):
        return self

    def gte(self, *a):
        return self

    def limit(self, *a):
        return self

    def execute(self):
        if self._mode == "update" and self.fail_update:
            raise TimeoutError("supabase timeout")
        return mock.MagicMock(data=self.rows if self._mode == "select" else [])


def test_close_is_not_repeated_when_the_db_update_fails():
    # Review finding: after a restart, a failed UPDATE made the same signal close (and notify) every run.
    db = Database()
    db.enabled = True
    row = {"id": "uuid-1", "symbol": "PENDLE", "detected_at": (NOW - timedelta(days=1)).isoformat(),
           "price_usd": 2.6, "entry_min_usd": 2.55, "entry_max_usd": 2.65, "target_usd": 3.1,
           "invalidation_usd": 2.35, "expires_at": None, "status": "OPEN"}
    db.client = FakeSupabase([row], fail_update=True)
    binance = mock.MagicMock()
    binance.klines.return_value = [candle(2.5, 2.7), candle(2.9, 3.2)]
    assert len(tracker.check_open(db, binance)) == 1
    assert tracker.check_open(db, binance) == []


def test_cooldown_symbols_come_from_one_query():
    # Review finding: one SELECT per coin (100+) stalled scans when Supabase hung.
    db = Database()
    db.enabled = True
    db.client = FakeSupabase([{"symbol": "PENDLE"}, {"symbol": "UNI"}])
    db.save_alert(None, "ldo", "1", "m")
    assert db.recent_alert_symbols() == {"PENDLE", "UNI", "LDO"}
    assert db.client.selects == 1
