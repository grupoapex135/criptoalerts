"""Telegram layer: short messages, authorization and the scan/tracking jobs (bot mocked)."""
import asyncio

import pytest
from unittest import mock

import telegram_app as T
import tracker
from tests.conftest import make_ai


def dossier(**over):
    d = {
        "asset": {"id": "lido-dao", "symbol": "LDO", "name": "Lido DAO"},
        "market_regime": {"status": "risk_on", "btc_7d": -2.0, "btc_30d": 7.8,
                          "fear_greed": 38, "fear_greed_label": "Medo"},
        "market": {"current_price_usd": 1.20, "market_cap_usd": 1e9, "daily_volume_usd": 9e7,
                   "ath_change_pct": -74.2, "price_btc": 0.0000144},
        "vs_btc": {"available": True, "days": 221, "from_high_pct": -38.0},
        "venue": {"exchange": "Binance", "pair": "LDOUSDT", "last_price": 1.20},
        "trend": {"state": "UPTREND", "pullback_in_uptrend": True, "dist_ma50_pct": 8.0,
                  "ma50_ma200": "golden_cross_recent", "rsi_daily": 44.0, "rsi_weekly": 38.0},
        "fundamentals": {"available": True, "tvl": 26.3e9, "tvl_change_30d": 11.3,
                         "revenue_30d": 3.1e6, "revenue_growth_30d": 27.1, "market_cap_to_annual_revenue": 10.0},
        "tokenomics": {"fdv_mcap_ratio": 1.205, "circulating_pct": 83.0, "dilution_risk": "low",
                       "dilution_basis": "trailing_supply_growth", "supply_growth_30d": -0.6,
                       "value_capture": {"available": True, "holders_revenue_30d": 1.2e6}},
        "derivatives": {"available": True, "positioning": "deleveraged", "funding_rate_pct": 0.004,
                        "oi_change_24h": -18.0},
        "scores": {"composite": 84.0, "data_coverage": 1.0, "risk": 20.0},
        "vetoes": [],
    }
    d.update(over)
    return d


def ai(**over):
    base = dict(entry_min_usd=1.18, entry_max_usd=1.22, target_usd=1.42, invalidation_usd=1.09,
                thesis="Maior protocolo de staking líquido de Ethereum: transforma ETH em stETH, "
                       "que rende e segue usável em DeFi.",
                plan="Entradas fracionadas dentro da zona, respeitando a invalidação.")
    base.update(over)
    return make_ai(**base)


def result(**over):
    r = {"dossier": dossier(), "ai": ai(), "decision": "alert", "confidence": 74,
         "risk": "medio", "problems": [], "position_limit_brl": 600.0}
    r.update(over)
    return r


class TestMessages:
    def test_alert_is_a_sectioned_research_note(self):
        msg = T.opportunity_message(result())
        assert msg.startswith("💎 OPORTUNIDADE · 🟢 BINANCE — $LDO (Lido DAO)")
        assert "🔎 SINAIS DE LISTAGEM" not in msg  # listed asset: no listing section
        for section in ("📌 TESE E UTILIDADE", "📊 VALUATION & SAÚDE", "📉 TÉCNICO & CICLO",
                        "🌍 CONTEXTO MACRO", "✅ POR QUE AGORA", "🎯 PLANO DE REFERÊNCIA"):
            assert section in msg
        for line in ("Maior protocolo de staking líquido", "• Preço: $1.2 (-74% do topo histórico)",
                     "• Par BTC: 0.00001440 BTC · -38% da máxima de 221d contra o BTC",
                     "• FDV / Market cap: 1,21 · 83% em circulação",
                     "• Diluição: 🟢 baixa — oferta -0.6% em 30d",
                     "• Captura de valor: receita repassada a holders ($1.2M/30d)",
                     "• Receita 30d: $3.1M (+27%) · P/receita 10x", "• TVL: $26.3B (+11% em 30d)",
                     "• Tendência: alta, recuo saudável · +8% da MA50",
                     "• Médias 50/200d: cruz de ouro recente 🟡", "• RSI: 44 diário · 38 semanal",
                     "• Alavancagem: 🟢 desalavancado (funding 0.004%, OI -18% em 24h)",
                     "• Mercado: 🟢 favorável — BTC -2.0% em 7d, +7.8% em 30d",
                     "• Sentimento: Medo (Fear & Greed 38)", "Entrada: $1.18 – $1.22 · Binance",
                     "🎯 Alvo: $1.42 (+18.3%)", "🛑 Invalidação: $1.09 (-9.2%)",
                     "Risco: Médio · Limite: R$ 600,00", "💡 Entradas fracionadas",
                     "⚠️ Pesquisa, não recomendação."):
            assert line in msg, line
        assert len(msg) < 4096  # Telegram limit

    def test_missing_data_lines_are_omitted_not_shown_as_na(self):
        d = dossier(fundamentals={"available": False}, derivatives={"available": False, "positioning": None},
                    vs_btc={"available": False}, market_regime={"status": "neutral"},
                    trend={"state": "NEUTRAL"})
        msg = T.opportunity_message(result(dossier=d))
        for absent in ("• Receita", "• TVL", "• Alavancagem", "• Sentimento", "• RSI", "• Médias", "máxima de"):
            assert absent not in msg, absent
        assert "N/A" not in msg and "None" not in msg

    def test_risk_off_alert_shows_reduced_limit(self):
        d = dossier(market_regime={"status": "risk_off", "btc_7d": -12.0, "btc_30d": -21.0})
        msg = T.opportunity_message(result(position_limit_brl=300.0, dossier=d))
        assert "Limite: R$ 300,00 (reduzido: mercado em risco)" in msg
        assert "• Mercado: 🔴 em risco" in msg

    def test_manual_analysis_shows_score_and_coverage(self):
        msg = T.manual_analysis_message(result(decision="watch"))
        assert msg.startswith("👀 OBSERVAR · 🟢 BINANCE — $LDO (Lido DAO)")
        assert "Nota 84/100 · cobertura de dados 100% · confiança 74%" in msg
        assert "⚠️ Principal risco: Mercado volátil." in msg

    def test_manual_analysis_of_vetoed_asset(self):
        r = result(decision="reject", ai=None, confidence=None,
                   dossier=dossier(vetoes=["critical_unlock", "critical_event"]))
        msg = T.manual_analysis_message(r)
        assert msg.startswith("🔴 BLOQUEADO · 🟢 BINANCE — $LDO")
        assert "📌 TESE" not in msg and "🎯 PLANO" not in msg  # no AI call, no thesis or plan
        assert "🚫 Desbloqueio grande de tokens nos próximos 30 dias" in msg
        assert "🚫 Hack ou exploit recente no protocolo" in msg

    def test_tracking_messages(self):
        base = {"symbol": "LDO", "price_usd": 1.20, "close_price": 1.42, "result_pct": 18.33, "entered": True}
        hit = T.tracking_message({**base, "status": tracker.TARGET_HIT})
        assert hit == "🎯 LDO bateu o alvo\n$1.2 → $1.42 (+18.3% desde a entrada)"
        assert T.tracking_message({**base, "status": tracker.INVALIDATED}).startswith("🛑 LDO foi invalidado")
        expired = T.tracking_message({**base, "status": tracker.EXPIRED, "entered": False})
        assert "sem o preço entrar na zona" in expired

    def test_status_block_matches_the_requested_format(self):
        st = {"all": {"total": 24, "OPEN": 5, "TARGET_HIT": 13, "INVALIDATED": 6, "EXPIRED": 0, "win_rate": 68},
              "recent": {"total": 9, "OPEN": 1, "TARGET_HIT": 6, "INVALIDATED": 2, "EXPIRED": 0}, "days": 30}
        text = "\n".join(T._stats_block(st))
        assert "Sinais: 24\nAlvo atingido: 13\nInvalidado: 6" in text
        assert "Win rate encerrados: 68%" in text
        assert "9 sinais · 6 alvo · 2 invalidados · 1 abertos" in text

    def test_scan_summary(self):
        report = {"regime": {"status": "risk_off"}, "universe": 120, "deep": 8, "candidates": 3,
                  "results": [result()], "watch": ["UNI"], "vetoed": ["HKD: critical_event"],
                  "rejected": [], "errors": []}
        text = T.scan_summary(report)
        assert "Mercado: 🔴 em risco" in text
        assert "120 ativos (0 pré-Binance) → 8 a fundo → 3 na IA" in text
        assert "👀 Em observação: UNI" in text


class TestPreBinanceMessages:
    def pre(self, **over):
        d = dossier(
            mode="pre_listing",
            asset={"id": "instadapp", "symbol": "FLUID", "name": "Fluid"},
            venue={"exchange": "Upbit", "binance": False, "cex": ["Upbit", "Bybit", "Gate"], "dex": ["Uniswap V3"],
                   "last_price": 1.20},
            listing={"available": True, "binance_alpha": True, "binance_perp_without_spot": True, "yzi_labs": False,
                     "binance_programs": ["Binance HODLer Airdrops"], "tier1_cex": ["Bybit", "Coinbase", "Upbit"],
                     "dex_only": False},
            contract={"available": True, "status": "warning", "chain": "ethereum", "severe": [],
                      "warnings": ["mintable", "concentrated_holders"], "top10_holders_pct": 62.0},
        )
        d.update(over)
        return result(dossier=d)

    def test_pre_listing_alert_has_label_signals_and_venues(self):
        msg = T.opportunity_message(self.pre())
        assert msg.startswith("💎 OPORTUNIDADE · 🚀 PRÉ-BINANCE — $FLUID (Fluid)")
        for line in ("🔎 SINAIS DE LISTAGEM", "• Binance Alpha Spotlight ✅",
                     "• Perpétuo na Binance Futures, ainda sem spot ✅", "• Programas Binance: HODLer Airdrops",
                     "• Corretoras tier-1: Bybit, Coinbase, Upbit",
                     "• Contrato (ethereum): 🟡 emissão de novos tokens liberada, top 10 carteiras com 62%",
                     "Entrada: $1.18 – $1.22 · Upbit, Bybit, Gate · DEX: Uniswap V3"):
            assert line in msg, line

    def test_no_binance_signal_is_said_plainly(self):
        d = self.pre()["dossier"]
        d["listing"] = {"available": True, "binance_alpha": False, "binance_perp_without_spot": False,
                        "yzi_labs": False, "binance_programs": [], "tier1_cex": [], "dex_only": True}
        msg = T.opportunity_message(result(dossier=d))
        assert "• Nenhum sinal oficial da Binance ainda" in msg
        assert "• Só negocia em DEX 🟡" in msg

    def test_dangerous_contract_is_spelled_out(self):
        d = self.pre()["dossier"]
        d["contract"] = {"available": True, "status": "danger", "chain": "base", "severe": ["honeypot"], "warnings": []}
        msg = T.manual_analysis_message(result(decision="reject", ai=None, confidence=None,
                                               dossier={**d, "vetoes": ["contract_risk"]}))
        assert "• Contrato (base): 🔴 honeypot (não deixa vender)" in msg
        assert "🚫 Contrato com risco grave" in msg

    def test_listed_event_message(self):
        from datetime import datetime, timezone
        text = T.tracking_message({"event": "listed", "symbol": "FLUID", "price_usd": 3.1,
                                   "detected_at": datetime(2026, 9, 1, 15, tzinfo=timezone.utc)})
        assert text == "🚀 $FLUID foi listada no spot da Binance! (sinal de 01/09)\nPreço no alerta: $3.1"

    def test_status_counts_listings_after_the_alert(self):
        st = {"all": {"total": 3, "OPEN": 3, "TARGET_HIT": 0, "INVALIDATED": 0, "EXPIRED": 0, "win_rate": None},
              "recent": {"total": 3, "OPEN": 3, "TARGET_HIT": 0, "INVALIDATED": 0, "EXPIRED": 0}, "days": 30,
              "pre_listing": {"total": 2, "listed": 1}}
        assert "🚀 Pré-Binance: 2 sinais · 1 listados na Binance depois do alerta" in "\n".join(T._stats_block(st))


class TestAuth:
    def test_only_configured_chat_is_authorized(self):
        assert T._authorized_filter().chat_ids == frozenset({12345})

    def test_brl_format(self):
        assert T.brl(1234567.891) == "R$ 1.234.567,89"


class TestJobs:
    def _context(self):
        ctx = mock.MagicMock()
        ctx.bot.send_message = mock.AsyncMock()
        return ctx

    def test_scanner_job_sends_persists_and_respects_cooldown(self):
        ctx = self._context()
        report = {"regime": {"status": "neutral"}, "universe": 1, "deep": 1, "candidates": 1,
                  "results": [result()], "watch": [], "vetoed": [], "rejected": [], "errors": []}
        with mock.patch.object(T, "evaluate_candidates", return_value=report):
            asyncio.run(T.scanner_job(ctx))
            asyncio.run(T.scanner_job(ctx))  # same symbol again: cooldown
        assert ctx.bot.send_message.await_count == 1
        opens = T.db.open_opportunities()
        assert [o["symbol"] for o in opens] == ["LDO"] and opens[0]["target_usd"] == 1.42

    def test_tracker_job_notifies_status_change(self):
        ctx = self._context()
        changed = [{"symbol": "PENDLE", "status": tracker.TARGET_HIT, "price_usd": 2.6, "close_price": 3.1,
                    "result_pct": 19.23, "entered": True}]
        with mock.patch.object(T.tracker, "check_open", return_value=changed):
            asyncio.run(T.tracker_job(ctx))
        text = ctx.bot.send_message.await_args.kwargs["text"]
        assert text.startswith("🎯 PENDLE bateu o alvo")

    def test_scan_failure_is_notified_once_per_window(self):
        ctx = self._context()
        T.STATE["last_error_notice_at"] = None
        with mock.patch.object(T, "evaluate_candidates", side_effect=RuntimeError("429 CoinGecko")):
            asyncio.run(T.scanner_job(ctx))
            asyncio.run(T.scanner_job(ctx))
        assert ctx.bot.send_message.await_count == 1
        assert T.STATE["last_scan_error"] == "429 CoinGecko"


class TestSendRetry:
    def _bot(self, *effects):
        bot = mock.MagicMock()
        bot.send_message = mock.AsyncMock(side_effect=list(effects))
        return bot

    def test_network_blip_is_retried(self):
        # Real case: a TLS ConnectError during send lost the LDO alert.
        from telegram.error import NetworkError
        bot = self._bot(NetworkError("ConnectError"), NetworkError("ConnectError"), "ok")
        with mock.patch.object(T.asyncio, "sleep", new=mock.AsyncMock()):
            assert asyncio.run(T.send_text(bot, "oi")) == "ok"
        assert bot.send_message.await_count == 3

    def test_bad_request_is_not_retried(self):
        from telegram.error import BadRequest
        bot = self._bot(BadRequest("chat not found"))
        with mock.patch.object(T.asyncio, "sleep", new=mock.AsyncMock()):
            with pytest.raises(BadRequest):
                asyncio.run(T.send_text(bot, "oi"))
        assert bot.send_message.await_count == 1

    def test_flood_control_waits_and_retries(self):
        from telegram.error import RetryAfter
        bot = self._bot(RetryAfter(5), "ok")
        sleep = mock.AsyncMock()
        with mock.patch.object(T.asyncio, "sleep", new=sleep):
            assert asyncio.run(T.send_text(bot, "oi")) == "ok"
        sleep.assert_awaited_once_with(6.0)

    def test_gives_up_after_three_attempts(self):
        from telegram.error import NetworkError
        bot = self._bot(*[NetworkError("down")] * 3)
        with mock.patch.object(T.asyncio, "sleep", new=mock.AsyncMock()):
            with pytest.raises(NetworkError):
                asyncio.run(T.send_text(bot, "oi"))
        assert bot.send_message.await_count == 3
