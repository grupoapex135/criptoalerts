"""
Catalysts: recent hacks (DefiLlama, free) plus headlines from an optional
news provider, classified by keywords — deterministic, no AI involved.
Runs only for finalists.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Any

CRITICAL = re.compile(
    r"\b(hack(ed|s)?|exploit(ed|s)?|drain(ed|s)?|delist(s|ed|ing)?|rug ?pull|insolven(t|cy)|"
    r"bankrupt(cy)?|depeg(ged|s)?|(halt(s|ed)?|paus(e|es|ed)|suspend(s|ed)?) withdrawals?|"
    r"withdrawals? (halted|paused|suspended))\b", re.I)
NEGATIVE = re.compile(
    r"\b(lawsuit|sued|sues|sec|cftc|regulat(or|ors|ory)|investigation|probe|charged|ban(s|ned)?|"
    r"outage|downtime|vulnerabilit(y|ies)|token unlock|unlocks?|sell-?off|layoffs?)\b", re.I)
POSITIVE = re.compile(
    r"\b(listing|lists|listed|will list|mainnet|upgrade[sd]?|launch(es|ed)?|buybacks?|burn(s|ed)?|"
    r"partnership|partners with|acquisition|acquires?|acquired|integrat(es|ed|ion)|approv(al|ed|es)|"
    r"etf|fee switch)\b", re.I)


def classify(title: str) -> str | None:
    if CRITICAL.search(title):
        return "critical"
    if NEGATIVE.search(title):
        return "negative"
    if POSITIVE.search(title):
        return "positive"
    return None


def _date(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return str(ts) if ts else None


def build_catalysts(hacks: list[dict[str, Any]] | None, news: list[dict[str, Any]] | None,
                    sources: list[str]) -> dict[str, Any]:
    positive, negative, critical_reasons = [], [], []

    for h in hacks or []:
        amount = h.get("amount_usd")
        label = f"Hack/exploit: {h.get('name')}" + (f" (US$ {amount / 1e6:.1f}M)" if amount else "")
        negative.append({"title": label, "date": _date(h.get("date")), "source": "defillama_hacks"})
        critical_reasons.append(label)

    for n in news or []:
        title = str(n.get("title") or "").strip()
        kind = classify(title)
        if not kind:
            continue
        item = {"title": title[:160], "date": n.get("published_at"), "source": n.get("source")}
        if kind == "positive":
            positive.append(item)
        else:
            # A headline tagged with the coin can be about another asset ("Binance will
            # delist ABC/BTC" tagged BTC). Only structured hack data vetoes; headlines inform.
            negative.append({**item, "severity": kind})

    return {
        "available": bool(sources),
        "sources": sources,
        "positive": positive[:5],
        "negative": negative[:5],
        "critical_risk": bool(critical_reasons),
        "critical_reasons": critical_reasons[:3],
    }
