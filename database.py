from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Any
from supabase import create_client
from config import settings

log = logging.getLogger(__name__)

class Database:
    """
    Supabase persistence + alert cooldown.

    The cooldown is also kept in memory, so it works without Supabase and
    survives a Supabase outage (only a restart loses it). Supabase failures
    are logged and exposed via last_error instead of killing the scan.
    """

    def __init__(self):
        self.enabled = bool(settings.supabase_url and settings.supabase_service_role_key)
        self.client = (
            create_client(settings.supabase_url, settings.supabase_service_role_key)
            if self.enabled else None
        )
        self._sent_at: dict[str, datetime] = {}
        self.last_error: str | None = None

    def _cutoff(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(hours=settings.alert_cooldown_hours)

    def recent_alert_exists(self, symbol: str) -> bool:
        symbol = symbol.upper()
        last = self._sent_at.get(symbol)
        if last and last >= self._cutoff():
            return True
        if not self.enabled:
            return False
        try:
            res = (
                self.client.table("alerts")
                .select("id")
                .eq("symbol", symbol)
                .gte("sent_at", self._cutoff().isoformat())
                .limit(1)
                .execute()
            )
        except Exception as exc:
            log.error("Supabase (cooldown) falhou: %s", exc)
            self.last_error = f"cooldown: {exc}"
            return False
        return bool(res.data)

    def save_opportunity(self, row: dict[str, Any]) -> str | None:
        if not self.enabled:
            return None
        try:
            res = self.client.table("opportunities").insert(row).execute()
        except Exception as exc:
            log.error("Supabase (opportunities) falhou: %s", exc)
            self.last_error = f"opportunities: {exc}"
            return None
        if not res.data:
            return None
        return res.data[0]["id"]

    def save_alert(self, opportunity_id: str | None, symbol: str, chat_id: str, message: str):
        self._sent_at[symbol.upper()] = datetime.now(timezone.utc)
        if not self.enabled:
            return
        try:
            self.client.table("alerts").insert({
                "opportunity_id": opportunity_id,
                "symbol": symbol.upper(),
                "telegram_chat_id": str(chat_id),
                "message": message,
            }).execute()
        except Exception as exc:
            log.error("Supabase (alerts) falhou: %s", exc)
            self.last_error = f"alerts: {exc}"
