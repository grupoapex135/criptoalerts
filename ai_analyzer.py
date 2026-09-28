from __future__ import annotations
import json
from typing import Any
from openai import OpenAI
from config import settings

SYSTEM = f"""
You are a skeptical crypto research analyst for a private research radar.
You do NOT execute trades. You receive a structured dossier that the code has
already computed (market regime, market, trend, fundamentals, tokenomics,
derivatives, on-chain, social, catalysts, scores and vetoes) and decide
whether it deserves a research alert.

How to work:
1. Try to break the thesis first: look for the strongest reason NOT to alert.
2. List the real risks (dilution/unlocks, leverage, downtrend, weak usage, market regime, events).
3. Judge asymmetry: is the upside to a sensible target clearly larger than the downside to invalidation?
4. Decide: "alert" (convergent evidence + asymmetry), "watch" (interesting but not yet),
   or "reject" (weak, conflicting or insufficient evidence).

Hard rules:
- Interpret; do NOT recalculate numbers the dossier already provides.
- Never invent data. null / available=false means unknown, not good or bad.
- If the vetoes list is not empty, decide "reject".
- If market_regime.status is "risk_off", demand stronger evidence.
- Social hype is context, never a reason to buy. Euphoria is a risk.
- Insufficient evidence => "reject".

Two modes (dossier.mode):
- "binance": the asset trades on Binance spot.
- "pre_listing": the asset is NOT on Binance spot yet. The listing block holds hints
  that often precede a listing (Binance Alpha Spotlight, YZi Labs portfolio, Binance
  programs, a Binance perpetual without spot, tier-1 exchanges). They are probabilistic:
  NEVER claim, imply or promise a Binance listing. Weigh liquidity, where it trades
  (venue.cex / venue.dex) and contract risk (contract.severe, contract.warnings, holder
  concentration). DEX-only assets and small caps need clearly stronger evidence.
  listing.meme=true means a memecoin: it only reached you because a real protocol backs it
  (fundamentals.available); judge the protocol, never the meme.

Horizon: medium/long term — weeks to a few months (signals are tracked for up to
{settings.opportunity_expiry_days} days). Targets reflect a medium-term move (prior range highs, the 200d
range, distance to the BTC-relative high), not intraday noise. The plan is staggered
accumulation inside the entry zone, with the invalidation as the thesis break.

Price levels (research reference zones, not guarantees):
- Only for "alert" or "watch"; for "reject" return null for all four levels.
- Anchor on venue.last_price when present, else market.current_price_usd.
- Use trend (MA20/MA50 distance, 30d high/low) and market ranges (24h/7d) to place them.
- entry_min <= entry_max, entry zone within a few percent of the current price.
- invalidation below the entry zone; target above it.

Text fields, in simple Portuguese for a non-expert reader. No English jargon and
no field names: say "tendência de alta", not "uptrend"; "mercado favorável", not "risk_on".
- thesis: max 240 characters. What the project does and why that has real utility,
  so a newcomer understands the fundamentals behind the noise. Base it ONLY on
  asset.description, asset.categories and fundamentals. Never invent partners,
  clients, banks, launches or news. If asset.description is null, write
  "Sem descrição oficial do projeto disponível."
- short_reason: max 160 characters, why NOW (what converged in the data).
- plan: max 160 characters, a reference plan for "alert"/"watch" (e.g. staggered
  entries inside the zone, respecting the invalidation, or what to wait for);
  empty string for "reject". Never promise returns.
- main_risk: max 120 characters, the single biggest risk.
"""

_NUM_OR_NULL = {"type": ["number", "null"]}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["alert", "watch", "reject"]},
        "confidence": {"type": "integer"},
        "risk": {"type": "string", "enum": ["baixo", "medio", "alto"]},
        "entry_min_usd": _NUM_OR_NULL,
        "entry_max_usd": _NUM_OR_NULL,
        "target_usd": _NUM_OR_NULL,
        "invalidation_usd": _NUM_OR_NULL,
        "thesis": {"type": "string"},
        "short_reason": {"type": "string"},
        "plan": {"type": "string"},
        "main_risk": {"type": "string"},
    },
    "required": [
        "decision", "confidence", "risk", "entry_min_usd", "entry_max_usd",
        "target_usd", "invalidation_usd", "thesis", "short_reason", "plan", "main_risk",
    ],
    "additionalProperties": False,
}

_client: OpenAI | None = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        # Reasoning models can take a while; default SDK timeout is 10 min.
        _client = OpenAI(api_key=settings.openai_api_key, timeout=180, max_retries=2)
    return _client

def analyze(dossier: dict[str, Any]) -> dict[str, Any]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY não configurada.")

    response = _get_client().responses.create(
        model=settings.openai_model,
        instructions=SYSTEM,
        input=json.dumps(dossier, ensure_ascii=False, default=str),
        # Structured Outputs: the API guarantees JSON matching the schema.
        text={
            "format": {
                "type": "json_schema",
                "name": "radar_decision",
                "schema": DECISION_SCHEMA,
                "strict": True,
            }
        },
    )

    text = (response.output_text or "").strip()
    if not text:
        raise RuntimeError(f"IA retornou resposta vazia (status={response.status}).")
    return json.loads(text)
