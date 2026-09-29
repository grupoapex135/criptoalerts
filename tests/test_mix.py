"""Group mix (≥90% pre-Binance), no repeated assets and the daily "OBSERVAR" cap."""
import asyncio
from unittest import mock

import pytest

import radar
import telegram_app as T
from database import Database
from tests.conftest import make_ai
from tests.test_telegram import dossier, result


# ------------------------------------------------------------------ 90/10 quota
@pytest.mark.parametrize("pre,binance,expected", [
    (0, 0, False),   # nothing sent yet: the first alerts must be pre-Binance
    (8, 0, False),   # 1 of 9 would be 11% Binance
    (9, 0, True),    # 1 of 10 = 10% Binance: allowed
    (9, 1, False),   # a second one needs 18 pre-Binance
    (18, 1, True),
])
def test_binance_quota_keeps_90_percent_pre_listing(pre, binance, expected):
    assert radar.binance_quota_open(pre, binance, 90) is expected


def test_quota_extremes():
    assert radar.binance_quota_open(0, 5, 0) is True
    assert radar.binance_quota_open(100, 0, 100) is False


def test_binance_mode_is_paused_while_the_quota_is_closed():
    # Real case: the group only got blue chips (ETH, LDO).
    with mock.patch.object(T.db, "mode_counts", return_value={"binance": 2}):
        modes, _ = T._modes_for_this_scan()
    assert modes == ["pre_listing"]
    with mock.patch.object(T.db, "mode_counts", return_value={"pre_listing": 27, "binance": 2}):
        modes, _ = T._modes_for_this_scan()
    assert modes == ["binance", "pre_listing"]


# ------------------------------------------------------------------ memory store
def test_mode_counts_and_open_signals_in_memory():
    db = Database()
    db.save_opportunity({"symbol": "ETH", "mode": "binance", "target_usd": 1, "invalidation_usd": 0.5})
    db.save_opportunity({"symbol": "FLUID", "mode": "pre_listing", "target_usd": 1, "invalidation_usd": 0.5})
    assert db.mode_counts(30) == {"binance": 1, "pre_listing": 1}
    assert db.open_signal_symbols() == {"ETH", "FLUID"}


def test_watch_cap_and_window_in_memory():
    db = Database()
    db.save_watch("arc", "1", "m")
    db.save_watch("carv", "1", "m")
    assert db.watch_sent_count(24) == 2
    assert db.recent_watch_symbols(7) == {"ARC", "CARV"}


# ------------------------------------------------------------------ delivery
def _ctx():
    ctx = mock.MagicMock()
    ctx.bot.send_message = mock.AsyncMock()
    return ctx


def _watch(symbol, conf, mode="pre_listing"):
    d = dossier(mode=mode, asset={"id": symbol.lower(), "symbol": symbol, "name": symbol})
    return result(decision="watch", confidence=conf, dossier=d, ai=make_ai(decision="watch"))


def test_watch_messages_respect_cap_window_and_mode():
    T.db._watch_log.clear()
    T.db.save_watch("OLD", "1", "m")  # sent yesterday-ish: inside the 7-day window
    report = {"watch_results": [_watch("OLD", 90), _watch("BEST", 80), _watch("MID", 60),
                                _watch("LOW", 50), _watch("ETH", 99, mode="binance")]}
    ctx = _ctx()
    asyncio.run(T._deliver_watch(ctx, report))
    sent = [c.kwargs["text"].splitlines()[1] for c in ctx.bot.send_message.await_args_list]
    # cap 3/day with one already sent -> 2 more, best confidence first; no repeat, no Binance mode
    assert sent == ["$BEST — BEST", "$MID — MID"]
    assert all(c.kwargs["text"].startswith("👀 OBSERVAR · 🚀 PRÉ-BINANCE") for c in ctx.bot.send_message.await_args_list)
    ctx2 = _ctx()
    asyncio.run(T._deliver_watch(ctx2, report))  # daily cap reached
    ctx2.bot.send_message.assert_not_awaited()
    T.db._watch_log.clear()


def test_an_asset_with_an_open_signal_is_never_alerted_again():
    # Real case: LDO reached the group several times.
    ctx = _ctx()
    r = result()
    with mock.patch.object(T.db, "recent_alert_exists", return_value=False), \
         mock.patch.object(T.db, "open_signal_symbols", return_value={"LDO"}):
        asyncio.run(T.send_opportunity(ctx, r))
    ctx.bot.send_message.assert_not_awaited()
