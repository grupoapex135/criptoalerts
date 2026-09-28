from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from config import settings
from database import Database
from radar import evaluate_candidates, analyze_symbol, position_limit_brl, reference_price

log = logging.getLogger(__name__)

db = Database()
TZ = ZoneInfo("America/Sao_Paulo")
# At most one error message per window, so an outage doesn't spam the chat.
ERROR_NOTICE_INTERVAL = timedelta(hours=3)
RISK_LABEL = {"baixo": "Baixo", "medio": "Médio", "alto": "Alto"}

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

def reward_risk(a: dict) -> float | None:
    try:
        mid = (float(a["entry_min_usd"]) + float(a["entry_max_usd"])) / 2
        risk = mid - float(a["invalidation_usd"])
        reward = float(a["target_usd"]) - mid
    except (KeyError, TypeError, ValueError):
        return None
    return reward / risk if risk > 0 and reward > 0 else None

def local_time(dt: datetime | None) -> str:
    return dt.astimezone(TZ).strftime("%d/%m %H:%M") if dt else "-"

def opportunity_message(result: dict) -> str:
    s = result["snapshot"]
    a = result["ai"]
    price = reference_price(s)
    rr = reward_risk(a)
    rr_line = f"⚖️ Retorno/risco: {rr:.1f}".replace(".", ",") + "\n" if rr else ""

    return (
        f"🟢 OPORTUNIDADE — {s['symbol']}\n\n"
        f"Entrada: {money(a['entry_min_usd'])} – {money(a['entry_max_usd'])}\n"
        f"Onde: Binance ({s['binance_spot']['symbol']}) · agora {money(price)}\n"
        f"Risco: {RISK_LABEL.get(str(a['risk']), str(a['risk']))}\n"
        f"Limite configurado: {brl(position_limit_brl())}\n\n"
        f"🎯 Referência: {money(a['target_usd'])} ({pct_from(price, a['target_usd'])})\n"
        f"🛑 Invalidação: {money(a['invalidation_usd'])} ({pct_from(price, a['invalidation_usd'])})\n"
        f"{rr_line}\n"
        f"{a['reason']}\n\n"
        f"⚠️ {a['warning']}\n"
        f"Confiança do radar: {int(a['confidence'])}%"
    )

def manual_analysis_message(result: dict) -> str:
    s = result["snapshot"]
    a = result["ai"]
    problems = result.get("problems") or []
    passed = (
        bool(a.get("alert"))
        and int(a.get("confidence", 0)) >= settings.min_confidence_to_alert
        and not problems
    )
    state = "🟢 ALERTA" if passed else "⚪ NÃO PASSOU NO FILTRO"
    venue = f"Binance ({s['binance_spot']['symbol']})" if s.get("binance_spot") else "não confirmado na Binance"
    problems_line = f"🚫 Níveis descartados: {'; '.join(problems)}\n" if problems else ""
    return (
        f"{state} — {s['symbol']}\n\n"
        f"Preço: {money(reference_price(s))}\n"
        f"Entrada ref.: {money(a['entry_min_usd'])} – {money(a['entry_max_usd'])}\n"
        f"Alvo ref.: {money(a['target_usd'])} · Invalidação: {money(a['invalidation_usd'])}\n"
        f"Onde: {venue}\n"
        f"Risco: {RISK_LABEL.get(str(a['risk']), str(a['risk']))}\n"
        f"Confiança: {int(a['confidence'])}% · Score quant: {s['market_score']:.0f}/100\n"
        f"{problems_line}\n"
        f"{a['reason']}\n"
        f"⚠️ {a['warning']}"
    )

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
        await context.bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=f"⚠️ Crypto Radar com problema\n\n{text[:3500]}\n\nDetalhes: /status",
        )
    except Exception:
        log.exception("Falha ao avisar erro no Telegram")

async def send_opportunity(context: ContextTypes.DEFAULT_TYPE, result: dict):
    s = result["snapshot"]
    symbol = s["symbol"]
    if await asyncio.to_thread(db.recent_alert_exists, symbol):
        return

    msg = opportunity_message(result)
    chat_id = settings.telegram_chat_id
    if not chat_id:
        return

    await context.bot.send_message(chat_id=chat_id, text=msg)
    STATE["alerts_sent"] += 1

    row = {
        "symbol": symbol,
        "name": s.get("name"),
        "price_usd": reference_price(s),
        "entry_min_usd": result["ai"].get("entry_min_usd"),
        "entry_max_usd": result["ai"].get("entry_max_usd"),
        "target_usd": result["ai"].get("target_usd"),
        "invalidation_usd": result["ai"].get("invalidation_usd"),
        "risk": result["ai"].get("risk"),
        "confidence": result["ai"].get("confidence"),
        "quantitative_score": s.get("market_score"),
        "market_cap_usd": s.get("market_cap_usd"),
        "daily_volume_usd": s.get("daily_volume_usd"),
        "venue": "Binance",
        "reason": result["ai"].get("reason"),
        "ai_payload": result["ai"],
        "raw_snapshot": s,
    }
    opp_id = await asyncio.to_thread(db.save_opportunity, row)
    await asyncio.to_thread(db.save_alert, opp_id, symbol, chat_id, msg)

async def scanner_job(context: ContextTypes.DEFAULT_TYPE):
    lock = _get_scan_lock()
    if lock.locked():
        log.info("[scanner] varredura anterior ainda rodando; pulando este ciclo.")
        return
    async with lock:
        try:
            report = await asyncio.to_thread(evaluate_candidates, db.recent_alert_exists)
        except Exception as exc:
            log.exception("[scanner] erro")
            _record_scan(None, exc)
            await _notify_error(context, f"A varredura automática falhou:\n{exc}")
            return
        _record_scan(report, None)

    for result in report["results"]:
        try:
            await send_opportunity(context, result)
        except Exception:
            log.exception("[scanner] falha ao enviar %s", result["snapshot"]["symbol"])
    if report["errors"]:
        await _notify_error(
            context,
            f"A IA falhou em {len(report['errors'])} de {report['candidates']} candidatos:\n"
            + "\n".join(report["errors"][:3]),
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_authorized(update):
        if not settings.telegram_chat_id:
            await update.message.reply_text(
                f"Modo configuração.\n\nSeu chat ID é: {update.effective_chat.id}\n"
                "Coloque em TELEGRAM_CHAT_ID no .env e reinicie o bot."
            )
        else:
            await update.message.reply_text("Bot privado.")
        return
    await update.message.reply_text(
        "Crypto Radar ativo.\n\n"
        "Comandos:\n"
        "/scan — força uma varredura agora\n"
        "/analyze BTC — analisa um ativo\n"
        "/status — saúde do radar e última varredura\n"
        "/id — mostra seu chat ID\n\n"
        "Você também pode escrever: “analisa PENDLE”.\n\n"
        f"Varredura automática a cada {settings.scan_interval_minutes} min."
    )

async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Seu TELEGRAM_CHAT_ID é: {update.effective_chat.id}")

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lock = _get_scan_lock()
    if lock.locked():
        await update.message.reply_text("⏳ Já tem uma varredura rodando. Tente de novo em instantes.")
        return
    async with lock:
        await update.message.reply_text("🔎 Rodando o radar agora…")
        try:
            report = await asyncio.to_thread(evaluate_candidates)
        except Exception as exc:
            log.exception("[scan] erro")
            _record_scan(None, exc)
            await update.message.reply_text(f"Erro no scan: {exc}")
            return
        _record_scan(report, None)

    results = report["results"]
    for result in results[:3]:
        await update.message.reply_text(opportunity_message(result))

    summary = (
        f"{report['candidates']} candidato(s) avaliado(s) pela IA · {len(results)} passaram."
        if results else
        f"Nenhuma oportunidade passou nos critérios neste momento "
        f"({report['candidates']} candidato(s) avaliado(s) pela IA)."
    )
    if report["rejected"]:
        summary += "\n\n🚫 Descartados por níveis incoerentes:\n" + "\n".join(report["rejected"][:5])
    if report["errors"]:
        summary += "\n\n⚠️ Erros da IA:\n" + "\n".join(report["errors"][:3])
    await update.message.reply_text(summary)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(timezone.utc)
    last_at = STATE["last_scan_at"]
    report = STATE["last_report"]

    if not last_at:
        last_line = "Última varredura: ainda não rodou"
    elif STATE["last_scan_error"]:
        last_line = f"Última varredura: {local_time(last_at)} — ❌ falhou: {STATE['last_scan_error'][:300]}"
    else:
        mins = int((now - last_at).total_seconds() // 60)
        last_line = (
            f"Última varredura: {local_time(last_at)} (há {mins} min)\n"
            f"  → {report['candidates']} na IA · {len(report['results'])} passaram · "
            f"{len(report['rejected'])} descartados · {len(report['errors'])} erros"
        )

    jobs = context.job_queue.get_jobs_by_name("market_scanner") if context.job_queue else []
    next_line = f"Próxima automática: {local_time(jobs[0].next_t)}" if jobs else "Varredura automática: desligada"

    if db.enabled:
        supa = "ligado" + (f" (último erro: {db.last_error[:200]})" if db.last_error else "")
    else:
        supa = "desligado (cooldown só em memória)"

    await update.message.reply_text(
        "📡 Crypto Radar — status\n\n"
        f"Rodando desde: {local_time(STATE['started_at'])}\n"
        f"{last_line}\n"
        f"{next_line}\n"
        f"Alertas enviados desde o início: {STATE['alerts_sent']}\n\n"
        f"Modelo IA: {settings.openai_model}\n"
        f"Supabase: {supa}\n"
        f"Filtros: mcap ≥ ${settings.min_market_cap_usd / 1e6:,.0f}M · "
        f"volume ≥ ${settings.min_daily_volume_usd / 1e6:,.0f}M · "
        f"FDV/MC ≤ {settings.max_fdv_to_mcap_ratio:g} · score ≥ {settings.min_score_to_ai:g} · "
        f"confiança ≥ {settings.min_confidence_to_alert} · cooldown {settings.alert_cooldown_hours}h"
    )

async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /analyze PENDLE")
        return
    symbol = context.args[0].lstrip("$").upper()
    await _run_manual_analysis(update, symbol)

async def natural_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = re.search(r"\b(?:analisa|analisar|analyze|olha|ver)\s+\$?([A-Za-z0-9]{2,12})\b", text, re.I)
    if m:
        await _run_manual_analysis(update, m.group(1).upper())
        return
    await update.message.reply_text(
        "Para o MVP, eu respondo análises de ativos. Ex.: “analisa PENDLE” ou use /scan."
    )

async def _run_manual_analysis(update: Update, symbol: str):
    await update.message.reply_text(f"Analisando {symbol}…")
    try:
        result = await asyncio.to_thread(analyze_symbol, symbol)
        if not result:
            await update.message.reply_text(
                f"Não encontrei {symbol} entre os {settings.top_coins_to_scan} maiores ativos monitorados."
            )
            return
        await update.message.reply_text(manual_analysis_message(result))
    except Exception as exc:
        log.exception("[analyze] erro em %s", symbol)
        await update.message.reply_text(f"Erro analisando {symbol}: {exc}")

async def _on_startup(app: Application):
    await app.bot.set_my_commands([
        BotCommand("scan", "Força uma varredura agora"),
        BotCommand("analyze", "Analisa um ativo. Ex.: /analyze PENDLE"),
        BotCommand("status", "Saúde do radar e última varredura"),
        BotCommand("id", "Mostra seu chat ID"),
    ])
    if settings.telegram_chat_id:
        # Every restart shows up in the chat, so crash loops are visible.
        await app.bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=f"🟢 Crypto Radar iniciado. Varredura a cada {settings.scan_interval_minutes} min. /status",
        )

def build_app() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado.")

    authorized = _authorized_filter()
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & authorized, natural_chat))

    if settings.telegram_chat_id:
        app.job_queue.run_repeating(
            scanner_job,
            interval=settings.scan_interval_minutes * 60,
            first=15,
            name="market_scanner",
        )
    else:
        log.warning("TELEGRAM_CHAT_ID vazio: varredura automática desligada. Mande /id ao bot.")
    return app
