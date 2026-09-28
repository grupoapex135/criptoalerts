from __future__ import annotations
import json
from typing import Any
from openai import OpenAI
from config import settings

SYSTEM = """
You are a conservative crypto research analyst for a private research radar.
You do NOT execute trades. You evaluate a structured market snapshot and decide
whether it is worth sending a research alert.

Never invent data. Use only the supplied fields. Null fields mean the data is
unavailable. If important evidence is absent, state that as a limitation and
reduce confidence.

Output fields:
- alert, confidence (0-100), risk ("baixo"|"medio"|"alto")
- entry_min_usd, entry_max_usd, target_usd, invalidation_usd
- reason: max 180 characters in Portuguese
- warning: max 140 characters in Portuguese

Price levels are research reference zones, not guarantees.
Use conservative rules:
- Avoid alerting assets that already look vertically extended.
- Prefer liquid assets with acceptable FDV/market-cap dilution.
- Protocol/TVL evidence may strengthen a thesis but absence of protocol data is not proof of weakness.
- Anchor price levels on binance_spot.last_price when present, else current_price_usd.
- Use range_7d_low_usd/range_7d_high_usd and low_24h_usd/high_24h_usd to place levels.
- entry_min <= entry_max.
- Keep entry zone near current market price (within a few percent); do not invent a far-away bargain.
- invalidation must be below entry zone for a long thesis.
- target must be above entry zone.
- If evidence is weak, alert=false.
"""

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "alert": {"type": "boolean"},
        "confidence": {"type": "integer"},
        "risk": {"type": "string", "enum": ["baixo", "medio", "alto"]},
        "entry_min_usd": {"type": "number"},
        "entry_max_usd": {"type": "number"},
        "target_usd": {"type": "number"},
        "invalidation_usd": {"type": "number"},
        "reason": {"type": "string"},
        "warning": {"type": "string"},
    },
    "required": [
        "alert", "confidence", "risk", "entry_min_usd", "entry_max_usd",
        "target_usd", "invalidation_usd", "reason", "warning",
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

def analyze(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY não configurada.")

    response = _get_client().responses.create(
        model=settings.openai_model,
        instructions=SYSTEM,
        input=json.dumps(snapshot, ensure_ascii=False),
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
