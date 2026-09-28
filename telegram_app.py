from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo
from telegram import BotCommand, Update
from telegram.error import BadRequest, NetworkError, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from config import settings
from database import Database
from analysis.vetoes import LABELS as VETO_LABELS
import radar
import tracker
from radar import evaluate_candidates, analyze_symbol, reference_price

log = logging.getLogger(__name__)

db = Database()
TZ = ZoneInfo("America/Sao_Paulo")
# At most one error message per window, so an outage doesn't spam the chat.
ERROR_NOTICE_INTERVAL = timedelta(hours=3)
RISK_LABEL = {"baixo": "Baixo", "medio": "Médio", "alto": "Alto"}
REGIME = {"risk_on": ("🟢", "favorável"), "neutral": ("🟡", "neutro"), "risk_off": ("🔴", "em risco")}
TREND = {"UPTREND": "alta", "NEUTRAL": "lateral", "DOWNTREND": "baixa", "CAPITULATION": "capitulação"}
POSITIONING = {
    "healthy": ("🟢", "saudável"), "deleveraged": ("🟢", "desalavancado"),
    "crowded_short": ("🟡", "vendidos demais"), "crowded_long": ("🔴", "comprados demais"),
}
SYMBOL_RE = re.compile(r"^\$?([A-Za-z0-9]{2,12})$")
MODE_LABEL = {"binance": "🟢 BINANCE", "pre_listing": "🚀 PRÉ-BINANCE"}
CONTRACT_LABELS = {
    "honeypot": "honeypot (não deixa vender)", "cannot_sell_all": "não permite vender tudo",
    "owner_can_change_balances": "dono pode alterar saldos", "hidden_owner": "dono oculto",
    "can_take_back_ownership": "dono pode retomar o controle", "tax_can_be_changed": "taxa pode ser alterada",
    "high_sell_tax": "taxa de venda acima de 10%", "freeze_authority": "pode congelar carteiras",
    "non_transferable": "token intransferível", "closable": "contas podem ser fechadas",
    "mintable": "emissão de novos tokens liberada", "transfers_can_be_paused": "transferências podem ser pausadas",
    "blacklist_function": "tem blacklist", "upgradeable_proxy": "contrato atualizável",
    "closed_source": "código fechado", "metadata_mutable": "metadados alteráveis",
    "transfer_hook": "hook de transferência", "concentrated_holders": "concentração alta em poucas carteiras",
}

# No dashboard: this in-memory state is what /status shows.
STATE: dict[str, Any] = {
    "started_at": datetime.now(timezone.utc),
    "last_scan_at": None,
    "last_report": None,
    "last_scan_error": None,
    "alerts_sent": 0,
    "last_error_notice_at": None,
}
_scan_lock: asyncio.Lock | None = None

def _get_scan_lock() -> asyncio.Lock:
    # Created lazily so it binds to the running event loop.
    global _scan_lock
    if _scan_lock is None:
        _scan_lock = asyncio.Lock()
    return _scan_lock

# ------------------------------------------------------------------ formatting
def money(v) -> str:
    try:
        v = float(v)
    except Exception:
        return "-"
    if v >= 1000:
        return f"${v:,.2f}"
    if v >= 1:
        return f"${v:.4f}".rstrip("0").rstrip(".")
    return f"${v:.8f}".rstrip("0").rstrip(".")

def brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def pct_from(price: float, level) -> str:
    try:
        return f"{(float(level) / price - 1) * 100:+.1f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "-"

def local_time(dt: datetime | None) -> str:
    return dt.astimezone(TZ).strftime("%d/%m %H:%M") if dt else "-"

def usd_short(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "-"
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= size:
            return f"${v / size:.1f}{unit}".replace(".0" + unit, unit)
    return f"${v:.0f}"

def signed(v, decimals: int = 0) -> str:
    return f"{v:+.{decimals}f}%"

def decimal_br(v: float, decimals: int = 2) -> str:
    return f"{v:.{decimals}f}".replace(".", ",")

DILUTION = {"low": ("🟢", "baixa"), "medium": ("🟡", "média"), "high": ("🔴", "alta"), "critical": ("🔴", "crítica")}
CROSS = {
    "golden_cross_recent": ("🟡", "cruz de ouro recente"),
    "death_cross_recent": ("🔴", "cruz da morte recente"),
    "above": ("🟢", "MA50 acima da MA200"),
    "below": ("🔴", "MA50 abaixo da MA200"),
}

def _valuation(d: dict[str, Any]) -> list[str]:
    m, tk = d.get("market") or {}, d.get("tokenomics") or {}
    f, vb = d.get("fundamentals") or {}, d.get("vs_btc") or {}
    out = []
    line = f"• Preço: {money(reference_price(d))}"
    if m.get("ath_change_pct") is not None:
        ath = m["ath_change_pct"]
        line += f" ({signed(ath, 1 if ath <= -99 else 0)} do topo histórico)"
    out.append(line)
    if m.get("price_btc"):
        line = f"• Par BTC: {m['price_btc']:.8f} BTC"
        if vb.get("available"):
            line += (f" · na máxima de {vb['days']}d contra o BTC" if vb["from_high_pct"] > -1
                     else f" · {signed(vb['from_high_pct'])} da máxima de {vb['days']}d contra o BTC")
        out.append(line)
    if tk.get("fdv_mcap_ratio"):
        line = f"• FDV / Market cap: {decimal_br(tk['fdv_mcap_ratio'])}"
        if tk.get("circulating_pct"):
            line += f" · {tk['circulating_pct']:.0f}% em circulação"
        out.append(line)
    if tk.get("dilution_risk"):
        icon, label = DILUTION[tk["dilution_risk"]]
        if tk.get("dilution_basis") == "unlock_schedule":
            detail = f"unlocks de {decimal_br(tk['unlock_30d_pct'], 1)}% da oferta em 30d"
        else:
            detail = f"oferta {signed(tk['supply_growth_30d'], 1)} em 30d"
        out.append(f"• Diluição: {icon} {label} — {detail}")
    vc = tk.get("value_capture") or {}
    if vc.get("available"):
        out.append(f"• Captura de valor: receita repassada a holders ({usd_short(vc['holders_revenue_30d'])}/30d)")
    if f.get("available") and f.get("revenue_30d"):
        line = f"• Receita 30d: {usd_short(f['revenue_30d'])}"
        if f.get("revenue_growth_30d") is not None:
            line += f" ({signed(f['revenue_growth_30d'])})"
        if f.get("market_cap_to_annual_revenue"):
            line += f" · P/receita {f['market_cap_to_annual_revenue']:.0f}x"
        out.append(line)
    if f.get("available") and f.get("tvl"):
        change = f.get("tvl_change_30d")
        window = "30d" if change is not None else "7d"
        change = change if change is not None else f.get("tvl_change_7d")
        out.append(f"• TVL: {usd_short(f['tvl'])}" + (f" ({signed(change)} em {window})" if change is not None else ""))
    return out

def _technical(d: dict[str, Any]) -> list[str]:
    tr, der = d.get("trend") or {}, d.get("derivatives") or {}
    out = []
    if tr.get("state"):
        text = TREND[tr["state"]]
        if tr.get("parabolic"):
            text += ", parabólico"
        elif tr.get("pullback_in_uptrend"):
            text += ", recuo saudável"
        elif tr.get("extended"):
            text += ", esticado"
        if tr.get("dist_ma50_pct") is not None:
            text += f" · {signed(tr['dist_ma50_pct'])} da MA50"
        out.append(f"• Tendência: {text}")
    if tr.get("ma50_ma200"):
        icon, label = CROSS[tr["ma50_ma200"]]
        out.append(f"• Médias 50/200d: {label} {icon}")
    rsis = [f"{tr[k]:.0f} {name}" for k, name in (("rsi_daily", "diário"), ("rsi_weekly", "semanal"))
            if tr.get(k) is not None]
    if rsis:
        out.append("• RSI: " + " · ".join(rsis))
    if der.get("positioning"):
        icon, label = POSITIONING[der["positioning"]]
        details = []
        if der.get("funding_rate_pct") is not None:
            details.append(f"funding {der['funding_rate_pct']:.3f}%")
        if der.get("oi_change_24h") is not None:
            details.append(f"OI {signed(der['oi_change_24h'])} em 24h")
        out.append(f"• Alavancagem: {icon} {label}" + (f" ({', '.join(details)})" if details else ""))
    return out

def _macro(d: dict[str, Any]) -> list[str]:
    r = d.get("market_regime") or {}
    out = []
    if r.get("status"):
        icon, name = REGIME[r["status"]]
        line = f"• Mercado: {icon} {name}"
        if r.get("btc_7d") is not None and r.get("btc_30d") is not None:
            line += f" — BTC {signed(r['btc_7d'], 1)} em 7d, {signed(r['btc_30d'], 1)} em 30d"
        out.append(line)
    if r.get("fear_greed") is not None:
        out.append(f"• Sentimento: {r['fear_greed_label']} (Fear & Greed {r['fear_greed']})")
    return out

def _where(d: dict[str, Any]) -> str:
    venue = d.get("venue") or {}
    if venue.get("binance") or venue.get("exchange") == "Binance":
        return "Binance"
    parts = []
    if venue.get("cex"):
        parts.append(", ".join(venue["cex"]))
    if venue.get("dex"):
        parts.append("DEX: " + ", ".join(venue["dex"]))
    return " · ".join(parts) or "fora da Binance"

def _listing(d: dict[str, Any]) -> list[str]:
    """Pre-Binance only: listing hints and the contract check."""
    if d.get("mode") != "pre_listing":
        return []
    ls, ct = d.get("listing") or {}, d.get("contract") or {}
    out = []
    if ls.get("binance_alpha"):
        out.append("• Binance Alpha Spotlight ✅")
    if ls.get("binance_perp_without_spot"):
        out.append("• Perpétuo na Binance Futures, ainda sem spot ✅")
    if ls.get("yzi_labs"):
        out.append("• Portfólio YZi Labs (ex-Binance Labs) ✅")
    if ls.get("binance_programs"):
        out.append("• Programas Binance: " + ", ".join(p.replace("Binance ", "") for p in ls["binance_programs"]))
    if not out:
        out.append("• Nenhum sinal oficial da Binance ainda")
    if ls.get("meme"):
        out.append("• 🐸 Memecoin — o que sustenta é o protocolo, não o meme")
    if ls.get("tier1_cex"):
        out.append("• Corretoras tier-1: " + ", ".join(ls["tier1_cex"]))
    elif ls.get("dex_only"):
        out.append("• Só negocia em DEX 🟡")
    if ct.get("available"):
        chain = f" ({ct['chain']})" if ct.get("chain") else ""
        if ct["status"] == "danger":
            out.append(f"• Contrato{chain}: 🔴 " + ", ".join(CONTRACT_LABELS.get(x, x) for x in ct["severe"]))
        elif ct["status"] == "warning":
            items = [CONTRACT_LABELS.get(x, x) for x in ct["warnings"]]
            if "concentrated_holders" in ct["warnings"] and ct.get("top10_holders_pct"):
                items = [i if i != CONTRACT_LABELS["concentrated_holders"]
                         else f"top 10 carteiras com {ct['top10_holders_pct']:.0f}%" for i in items]
            out.append(f"• Contrato{chain}: 🟡 " + ", ".join(items))
        else:
            out.append(f"• Contrato{chain}: 🟢 sem alertas (GoPlus)")
    return out

def _plan(result: dict) -> list[str]:
    d, a = result["dossier"], result.get("ai") or {}
    if a.get("entry_min_usd") is None or a.get("target_usd") is None:
        return []
    price = reference_price(d)
    limit = f"Limite: {brl(result['position_limit_brl'])}"
    if (d.get("market_regime") or {}).get("status") == "risk_off":
        limit += " (reduzido: mercado em risco)"
    out = [
        f"Entrada: {money(a['entry_min_usd'])} – {money(a['entry_max_usd'])} · {_where(d)}",
        f"🎯 Alvo: {money(a['target_usd'])} ({pct_from(price, a['target_usd'])}) · "
        f"🛑 Invalidação: {money(a['invalidation_usd'])} ({pct_from(price, a['invalidation_usd'])})",
        f"Risco: {RISK_LABEL.get(result['risk'], result['risk'])} · {limit}",
    ]
    if a.get("plan"):
        out.append(f"💡 {a['plan']}")
    return out

def research_message(result: dict, header: str, footer: list[str] | None = None) -> str:
    """Sectioned research note: thesis, valuation, technical, macro, plan. Lines without data are omitted."""
    d, a = result["dossier"], result.get("ai") or {}
    asset = d["asset"]
    mode = MODE_LABEL.get(d.get("mode") or "binance", "")
    lines = [f"{header} · {mode} — ${asset['symbol']} ({asset.get('name') or asset['symbol']})"]

    def section(title: str, body: list[str]):
        if body:
            lines.extend(["", title, *body])

    section("📌 TESE E UTILIDADE", [a["thesis"]] if a.get("thesis") else [])
    section("🔎 SINAIS DE LISTAGEM", _listing(d))
    section("📊 VALUATION & SAÚDE", _valuation(d))
    section("📉 TÉCNICO & CICLO", _technical(d))
    section("🌍 CONTEXTO MACRO", _macro(d))
    if a.get("short_reason"):
        section("✅ POR QUE AGORA", [a["short_reason"]])
    section("🎯 PLANO DE REFERÊNCIA", _plan(result))
    if footer:
        lines.extend(["", *footer])
    return "\n".join(lines)

def opportunity_message(result: dict) -> str:
    a = result["ai"]
    return research_message(result, "💎 OPORTUNIDADE", [f"⚠️ Pesquisa, não recomendação. Principal risco: {a['main_risk']}"])

def manual_analysis_message(result: dict) -> str:
    d, a = result["dossier"], result.get("ai")
    vetoes = d.get("vetoes") or []
    header = {
        "alert": "🟢 ALERTA",
        "watch": "👀 OBSERVAR",
        "reject": "🔴 BLOQUEADO" if vetoes else "⚪ NÃO PASSOU",
    }[result["decision"]]
    s = d.get("scores") or {}
    footer = [f"🚫 {VETO_LABELS.get(v, v)}" for v in vetoes]
    if result.get("problems"):
        footer.append(f"🚫 Níveis descartados: {'; '.join(result['problems'])}")
    score = f"Nota {s.get('composite', 0):.0f}/100 · cobertura de dados {s.get('data_coverage', 0):.0%}"
    if result.get("confidence") is not None:
        score += f" · confiança {result['confidence']}%"
    footer.append(score)
    if a:
        footer.append(f"⚠️ Principal risco: {a['main_risk']}")
    return research_message(result, header, footer)

def tracking_message(opp: dict[str, Any]) -> str:
    if opp.get("event") == "listed":
        detected = opp.get("detected_at")
        when = f" (sinal de {detected.astimezone(TZ).strftime('%d/%m')})" if detected else ""
        return f"🚀 ${opp['symbol']} foi listada no spot da Binance!{when}\nPreço no alerta: {money(opp.get('price_usd'))}"
    if opp["status"] == tracker.EXPIRED and not opp.get("entered", True):
        return f"⌛ {opp['symbol']} expirou sem o preço entrar na zona de entrada"
    head = {
        tracker.TARGET_HIT: f"🎯 {opp['symbol']} bateu o alvo",
        tracker.INVALIDATED: f"🛑 {opp['symbol']} foi invalidado",
        tracker.EXPIRED: f"⌛ {opp['symbol']} expirou sem alvo nem invalidação",
    }[opp["status"]]
    move = f"{money(opp.get('price_usd'))} → {money(opp.get('close_price'))}"
    if opp.get("result_pct") is not None:
        move += f" ({opp['result_pct']:+.1f}% desde a entrada)"
    return f"{head}\n{move}"

async def send_text(bot, text: str, attempts: int = 3):
    """
    Sends to the configured chat, retrying what a retry can fix: network blips
    (real case: a TLS ConnectError lost an alert) and flood control (RetryAfter).
    BadRequest subclasses NetworkError in python-telegram-bot and is never retried.
    """
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            return await bot.send_message(chat_id=settings.telegram_chat_id, text=text)
        except RetryAfter as exc:
            if last:
                raise
            wait = exc.retry_after
            await asyncio.sleep((wait.total_seconds() if hasattr(wait, "total_seconds") else float(wait)) + 1)
        except BadRequest:
            raise
        except NetworkError:
            if last:
                raise
            await asyncio.sleep(2 * (attempt + 1))

# ------------------------------------------------------------------ auth + errors
def _is_authorized(update: Update) -> bool:
    return bool(settings.telegram_chat_id) and str(update.effective_chat.id) == settings.telegram_chat_id

def _authorized_filter() -> filters.BaseFilter:
    # Without this, anyone who finds the bot could spend your OpenAI credits.
    if not settings.telegram_chat_id:
        return filters.Chat()  # matches nobody until TELEGRAM_CHAT_ID is set
    try:
        return filters.Chat(chat_id=int(settings.telegram_chat_id))
    except ValueError:
        raise RuntimeError("TELEGRAM_CHAT_ID deve ser numérico. Mande /id ao bot para descobrir.")

def _record_scan(report: dict | None, error: Exception | None):
    STATE["last_scan_at"] = datetime.now(timezone.utc)
    STATE["last_report"] = report
    STATE["last_scan_error"] = str(error) if error else None

async def _notify_error(context: ContextTypes.DEFAULT_TYPE, text: str):
    now = datetime.now(timezone.utc)
    last = STATE["last_error_notice_at"]
    if last and now - last < ERROR_NOTICE_INTERVAL:
        return
    STATE["last_error_notice_at"] = now
    try:
        await send_text(context.bot, f"⚠️ Crypto Radar com problema\n\n{text[:3500]}\n\nDetalhes: /status")
    except Exception:
        log.exception("Falha ao avisar erro no Telegram")

# ------------------------------------------------------------------ scan + tracking
async def send_opportunity(context: ContextTypes.DEFAULT_TYPE, result: dict):
    d, a = result["dossier"], result["ai"]
    symbol = d["asset"]["symbol"]
    if await asyncio.to_thread(db.recent_alert_exists, symbol):
        return
    chat_id = settings.telegram_chat_id
    if not chat_id:
        return

    msg = opportunity_message(result)
    await send_text(context.bot, msg)
    STATE["alerts_sent"] += 1
    log.info("[alerta] enviado: %s (%s)", symbol, d.get("mode") or "binance")

    row = {
        "symbol": symbol,
        "name": d["asset"].get("name"),
        "price_usd": reference_price(d),
        "entry_min_usd": a.get("entry_min_usd"),
        "entry_max_usd": a.get("entry_max_usd"),
        "target_usd": a.get("target_usd"),
        "invalidation_usd": a.get("invalidation_usd"),
        "risk": result.get("risk"),
        "confidence": result.get("confidence"),
        "quantitative_score": d["scores"].get("composite"),
        "market_cap_usd": d["market"].get("market_cap_usd"),
        "daily_volume_usd": d["market"].get("daily_volume_usd"),
        "venue": (d.get("venue") or {}).get("exchange") or "Binance",
        "mode": d.get("mode") or "binance",
        "coin_id": d["asset"].get("id"),
        "reason": a.get("short_reason"),
        "market_regime": (d.get("market_regime") or {}).get("status"),
        "scores": d.get("scores"),
        "dossier": d,
        "ai_payload": a,
    }
    opp_id = await asyncio.to_thread(db.save_opportunity, row)
    await asyncio.to_thread(db.save_alert, opp_id, symbol, chat_id, msg)

async def _run_scan() -> dict:
    watchlist = await asyncio.to_thread(db.watchlist)
    in_cooldown = await asyncio.to_thread(db.recent_alert_symbols)
    return await asyncio.to_thread(evaluate_candidates, in_cooldown.__contains__, watchlist)

async def _deliver(context: ContextTypes.DEFAULT_TYPE, report: dict):
    for result in report["results"]:
        try:
            await send_opportunity(context, result)
        except Exception:
            log.exception("[scanner] falha ao enviar %s", result["dossier"]["asset"]["symbol"])

async def scanner_job(context: ContextTypes.DEFAULT_TYPE):
    lock = _get_scan_lock()
    if lock.locked():
        log.info("[scanner] varredura anterior ainda rodando; pulando este ciclo.")
        return
    async with lock:
        try:
            report = await _run_scan()
        except Exception as exc:
            log.exception("[scanner] erro")
            _record_scan(None, exc)
            await _notify_error(context, f"A varredura automática falhou:\n{exc}")
            return
        _record_scan(report, None)
    log.info("[scanner] %d ativos (%d pré-Binance) · %d a fundo · %d na IA · %d alerta(s) · %d observação · "
             "%d vetados · %d erros", report["universe"], report.get("universe_pre_listing", 0), report["deep"],
             report["candidates"], len(report["results"]), len(report["watch"]), len(report["vetoed"]),
             len(report["errors"]))

    await _deliver(context, report)
    if report["errors"]:
        await _notify_error(
            context,
            f"A IA falhou em {len(report['errors'])} de {report['candidates']} candidatos:\n"
            + "\n".join(report["errors"][:3]),
        )

async def tracker_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        changed = await asyncio.to_thread(tracker.check_open, db, radar.binance, radar.cg)
    except Exception:
        log.exception("[tracker] erro")
        return
    for opp in changed:
        try:
            await send_text(context.bot, tracking_message(opp))
        except Exception:
            log.exception("[tracker] falha ao avisar %s", opp.get("symbol"))

def scan_summary(report: dict) -> str:
    regime = REGIME.get((report.get("regime") or {}).get("status"), ("⚪", "sem dados"))
    lines = [
        "🔎 Varredura concluída", "",
        f"Mercado: {regime[0]} {regime[1]}",
        f"{report['universe']} ativos ({report.get('universe_pre_listing', 0)} pré-Binance) → "
        f"{report['deep']} a fundo → {report['candidates']} na IA",
        f"✅ {len(report['results'])} alerta(s)",
    ]
    if report["watch"]:
        lines.append(f"👀 Em observação: {', '.join(report['watch'])}")
    if report["vetoed"]:
        lines.append(f"🚫 Bloqueados: {len(report['vetoed'])}")
    if report["rejected"]:
        lines.append("Descartados por níveis incoerentes: " + ", ".join(r.split(":")[0] for r in report["rejected"]))
    if report["errors"]:
        lines += ["", "⚠️ Erros da IA:", *report["errors"][:3]]
    return "\n".join(lines)

# ------------------------------------------------------------------ commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        if not settings.telegram_chat_id:
            await update.effective_message.reply_text(
                f"Modo configuração.\n\nSeu chat ID é: {update.effective_chat.id}\n"
                "Coloque em TELEGRAM_CHAT_ID no .env e reinicie o bot."
            )
        else:
            await update.effective_message.reply_text("Bot privado.")
        return
    await update.effective_message.reply_text(
        "Crypto Radar ativo.\n\n"
        "Comandos:\n"
        "/scan — força uma varredura agora\n"
        "/analyze BTC — analisa um ativo\n"
        "/status — placar dos sinais e saúde do radar\n"
        "/watch BTC — coloca na lista de observação\n"
        "/unwatch BTC — tira da lista\n"
        "/id — mostra seu chat ID\n\n"
        "Você também pode escrever: “analisa PENDLE”.\n\n"
        "Nos alertas: 🟢 BINANCE = já listada · 🚀 PRÉ-BINANCE = ainda fora do spot da Binance.\n"
        f"Varredura automática a cada {settings.scan_interval_minutes} min."
    )

async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"Seu TELEGRAM_CHAT_ID é: {update.effective_chat.id}")

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lock = _get_scan_lock()
    if lock.locked():
        await update.effective_message.reply_text("⏳ Já tem uma varredura rodando. Tente de novo em instantes.")
        return
    async with lock:
        await update.effective_message.reply_text("🔎 Rodando o radar agora… (leva alguns minutos)")
        try:
            report = await _run_scan()
        except Exception as exc:
            log.exception("[scan] erro")
            _record_scan(None, exc)
            await update.effective_message.reply_text(f"Erro no scan: {exc}")
            return
        _record_scan(report, None)
    # Same path as the automatic scan: alerts are persisted, tracked and respect cooldown.
    await _deliver(context, report)
    await update.effective_message.reply_text(scan_summary(report))

def _stats_block(st: dict[str, Any]) -> list[str]:
    a, r = st["all"], st["recent"]
    if not a["total"]:
        return ["📊 RADAR", "", "Nenhum sinal ainda."]
    pre = st.get("pre_listing") or {}
    lines = [
        "📊 RADAR", "",
        f"Sinais: {a['total']}",
        f"Alvo atingido: {a['TARGET_HIT']}",
        f"Invalidado: {a['INVALIDATED']}",
        f"Expirado: {a['EXPIRED']}",
        f"Em aberto: {a['OPEN']}", "",
        f"Win rate encerrados: {a['win_rate']}%" if a["win_rate"] is not None else "Win rate: sem sinais encerrados",
        "",
        f"Últimos {st['days']}d:",
        f"{r['total']} sinais · {r['TARGET_HIT']} alvo · {r['INVALIDATED']} invalidados · "
        f"{r['OPEN']} abertos" + (f" · {r['EXPIRED']} expirados" if r["EXPIRED"] else ""),
    ]
    if pre.get("total"):
        lines += ["", f"🚀 Pré-Binance: {pre['total']} sinais · {pre['listed']} listados na Binance depois do alerta"]
    return lines

def _sources_line() -> str:
    def mark(on: bool) -> str:
        return "✅" if on else "⚪"

    def keyed(flag: bool, client) -> str:
        # ⚠️ = key present but the plan has no access (the provider switched itself off).
        return "⚠️ sem acesso no plano" if flag and client.api_key and client.blocked else mark(flag and client.enabled)
    parts = [
        "DefiLlama ✅",
        f"Derivativos {mark(settings.enable_derivatives)}"
        + (" (CoinGlass)" if radar.coinglass.enabled else " (Binance)" if settings.enable_derivatives else ""),
        f"Unlocks {keyed(settings.enable_tokenomics, radar.tokenomist)}",
        f"Social {keyed(settings.enable_social, radar.lunarcrush)}",
        f"Notícias {keyed(settings.enable_news, radar.news)}",
        f"Hacks {mark(settings.enable_news)}",
        f"On-chain {mark('onchain' in radar.enabled_layers())}",
    ]
    return " · ".join(parts)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await asyncio.to_thread(db.signal_rows)
    lines = _stats_block(tracker.stats(rows))

    now = datetime.now(timezone.utc)
    last_at, report = STATE["last_scan_at"], STATE["last_report"]
    lines += ["", "🩺 Saúde"]
    if report and report.get("regime"):
        regime = report["regime"]
        icon, name = REGIME.get(regime.get("status"), ("⚪", "sem dados"))
        lines.append(f"Mercado: {icon} {name} — {regime.get('reason', '')}")
    if not last_at:
        lines.append("Última varredura: ainda não rodou")
    elif STATE["last_scan_error"]:
        lines.append(f"Última varredura: {local_time(last_at)} — ❌ falhou: {STATE['last_scan_error'][:300]}")
    else:
        mins = int((now - last_at).total_seconds() // 60)
        lines.append(
            f"Última varredura: {local_time(last_at)} (há {mins} min) → "
            f"{report['candidates']} na IA · {len(report['results'])} alerta(s) · "
            f"{len(report['watch'])} observação · {len(report['errors'])} erros"
        )
    jobs = context.job_queue.get_jobs_by_name("market_scanner") if context.job_queue else []
    lines.append(f"Próxima: {local_time(jobs[0].next_t)}" if jobs else "Varredura automática: desligada")
    lines.append(f"Fontes: {_sources_line()}")
    if db.enabled:
        lines.append("Supabase: ligado" + (f" (último erro: {db.last_error[:150]})" if db.last_error else ""))
    else:
        lines.append("Supabase: desligado — placar e cooldown só em memória")
    await update.effective_message.reply_text("\n".join(lines))

def _symbol_arg(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if not context.args:
        return None
    m = SYMBOL_RE.match(context.args[0])
    return m.group(1).upper() if m else None

async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = _symbol_arg(context)
    if not symbol:
        await update.effective_message.reply_text("Use: /analyze PENDLE")
        return
    await _run_manual_analysis(update, symbol)

async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = _symbol_arg(context)
    if not symbol:
        current = await asyncio.to_thread(db.watchlist)
        await update.effective_message.reply_text(
            "👀 Em observação: " + (", ".join(current) if current else "nenhum") +
            "\n\nUse /watch PENDLE para adicionar. Ativos observados sempre recebem análise completa na varredura."
        )
        return
    await asyncio.to_thread(db.watch, symbol, True)
    await update.effective_message.reply_text(f"👀 {symbol} na lista de observação. Ele entra na análise completa de toda varredura.")

async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = _symbol_arg(context)
    if not symbol:
        await update.effective_message.reply_text("Use: /unwatch PENDLE")
        return
    await asyncio.to_thread(db.watch, symbol, False)
    await update.effective_message.reply_text(f"{symbol} saiu da lista de observação.")

async def natural_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    m = re.search(r"\b(?:analisa|analisar|analyze|olha|ver)\s+\$?([A-Za-z0-9]{2,12})\b", text, re.I)
    if m:
        await _run_manual_analysis(update, m.group(1).upper())
        return
    await update.effective_message.reply_text(
        "Eu respondo análises de ativos. Ex.: “analisa PENDLE”, ou use /scan e /status."
    )

async def _run_manual_analysis(update: Update, symbol: str):
    await update.effective_message.reply_text(f"Analisando {symbol}…")
    try:
        result = await asyncio.to_thread(analyze_symbol, symbol)
        if not result:
            await update.effective_message.reply_text(
                f"Não encontrei {symbol} entre os {settings.top_coins_to_scan} maiores ativos monitorados."
            )
            return
        await update.effective_message.reply_text(manual_analysis_message(result))
    except Exception as exc:
        log.exception("[analyze] erro em %s", symbol)
        await update.effective_message.reply_text(f"Erro analisando {symbol}: {exc}")

async def _on_startup(app: Application):
    await app.bot.set_my_commands([
        BotCommand("scan", "Força uma varredura agora"),
        BotCommand("analyze", "Analisa um ativo. Ex.: /analyze PENDLE"),
        BotCommand("status", "Placar dos sinais e saúde do radar"),
        BotCommand("watch", "Observa um ativo. Ex.: /watch PENDLE"),
        BotCommand("unwatch", "Para de observar um ativo"),
        BotCommand("id", "Mostra seu chat ID"),
    ])
    if settings.telegram_chat_id:
        # Every restart shows up in the chat, so crash loops are visible. A network blip
        # here must not keep the bot from starting.
        try:
            await send_text(app.bot, f"🟢 Crypto Radar iniciado. Varredura a cada {settings.scan_interval_minutes} min. /status")
        except Exception:
            log.exception("Falha ao avisar o início no Telegram")

def build_app() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado.")

    # Edited messages are ignored: re-running an analysis on every edit is noise.
    authorized = _authorized_filter() & filters.UpdateType.MESSAGE
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        # A long /scan must not block /status or /analyze.
        .concurrent_updates(True)
        .post_init(_on_startup)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("scan", scan, filters=authorized))
    app.add_handler(CommandHandler("analyze", analyze_cmd, filters=authorized))
    app.add_handler(CommandHandler("status", status, filters=authorized))
    app.add_handler(CommandHandler("watch", watch_cmd, filters=authorized))
    app.add_handler(CommandHandler("unwatch", unwatch_cmd, filters=authorized))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & authorized, natural_chat))

    if settings.telegram_chat_id:
        app.job_queue.run_repeating(
            scanner_job,
            interval=settings.scan_interval_minutes * 60,
            first=15,
            name="market_scanner",
        )
        app.job_queue.run_repeating(
            tracker_job,
            interval=settings.tracker_interval_minutes * 60,
            first=60,
            name="signal_tracker",
        )
    else:
        log.warning("TELEGRAM_CHAT_ID vazio: varredura automática desligada. Mande /id ao bot.")
    return app
