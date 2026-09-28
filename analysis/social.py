"""
Social / narrative context. High sentiment is NOT a buy signal: the point is
to tell an emerging narrative apart from euphoria near a top.
"""
from __future__ import annotations
from typing import Any
from analysis.params import SOCIAL as S


def build_social(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {"available": False, "state": None}
    mentions_change = raw.get("mentions_change_24h")
    engagement_change = raw.get("engagement_change_24h")
    sentiment = raw.get("sentiment")

    if mentions_change is None and engagement_change is None:
        state = None
    elif (mentions_change or 0) >= S["euphoric_mentions_change"] and (
            (sentiment is not None and sentiment >= S["euphoric_sentiment"])
            or (engagement_change or 0) >= S["euphoric_mentions_change"]):
        state = "euphoric"
    elif (mentions_change or 0) >= S["trending_mentions_change"] or raw.get("trending"):
        state = "trending"
    elif (mentions_change or 0) >= S["emerging_mentions_change"]:
        state = "emerging"
    else:
        state = "quiet"

    return {
        "available": state is not None,
        "source": raw.get("source"),
        "mentions_24h": raw.get("mentions_24h"),
        "mentions_change_24h": mentions_change,
        "engagement_24h": raw.get("engagement_24h"),
        "engagement_change_24h": engagement_change,
        "sentiment": sentiment,
        "unique_creators": raw.get("unique_creators"),
        "social_dominance": raw.get("social_dominance"),
        "galaxy_score": raw.get("galaxy_score"),
        "state": state,
    }


def social_score(s: dict[str, Any]) -> float | None:
    state = s.get("state")
    if not state:
        return None
    return {"emerging": 70.0, "quiet": 50.0, "trending": 55.0, "euphoric": 20.0}[state]
