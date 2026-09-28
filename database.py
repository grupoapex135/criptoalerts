from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
from supabase import ClientOptions, create_client
from config import settings

log = logging.getLogger(__name__)

TRACK_FIELDS = ("id,symbol,coin_id,mode,detected_at,price_usd,entry_min_usd,entry_max_usd,target_usd,"
                "invalidation_usd,expires_at,status,binance_listed_at")


def parse_ts(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Database:
    """
    Supabase persistence: alerts, opportunities with their outcome, watchlist.

    Everything also lives in memory, so the bot works without Supabase and
    survives a Supabase outage (only a restart loses the in-memory copy).
    Supabase failures are logged and exposed via last_error instead of
    killing the scan.
    """

    def __init__(self):
        self.enabled = bool(settings.supabase_url and settings.supabase_service_role_key)
        # Short timeout: a hung Supabase must not stall the scan (default is 120s per query).
        self.client = (
            create_client(settings.supabase_url, settings.supabase_service_role_key,
                          options=ClientOptions(postgrest_client_timeout=10))
            if self.enabled else None
        )
        self._sent_at: dict[str, datetime] = {}
        self._opps: dict[str, dict[str, Any]] = {}
        self._watch: set[str] = set()
        self.last_error: str | None = None

    def _fail(self, what: str, exc: Exception):
        log.error("Supabase (%s) falhou: %s", what, exc)
        self.last_error = f"{what}: {exc}"

    def _cutoff(self) -> datetime:
        return datetime.now(timezone.utc) - timedelta(hours=settings.alert_cooldown_hours)

    # ------------------------------------------------------------ cooldown
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
            self._fail("cooldown", exc)
            return False
        return bool(res.data)

    def recent_alert_symbols(self) -> set[str]:
        """Every symbol in cooldown, in ONE query (the scan checks 100+ coins)."""
        cutoff = self._cutoff()
        symbols = {s for s, t in self._sent_at.items() if t >= cutoff}
        if self.enabled:
            try:
                res = self.client.table("alerts").select("symbol").gte("sent_at", cutoff.isoformat()).execute()
                symbols |= {r["symbol"] for r in res.data or []}
            except Exception as exc:
                self._fail("cooldown", exc)
        return symbols

    # ------------------------------------------------------------ opportunities
    def save_opportunity(self, row: dict[str, Any]) -> str | None:
        now = datetime.now(timezone.utc)
        row = {**row, "status": "OPEN",
               "expires_at": (now + timedelta(days=settings.opportunity_expiry_days)).isoformat()}
        opp_id = None
        if self.enabled:
            try:
                res = self.client.table("opportunities").insert(row).execute()
                opp_id = res.data[0]["id"] if res.data else None
            except Exception as exc:
                self._fail("opportunities", exc)
        opp_id = opp_id or f"mem-{uuid.uuid4()}"
        self._opps[opp_id] = {"id": opp_id, **{k: row.get(k) for k in TRACK_FIELDS.split(",") if k != "id"},
                              "detected_at": now}
        return opp_id

    def save_alert(self, opportunity_id: str | None, symbol: str, chat_id: str, message: str):
        self._sent_at[symbol.upper()] = datetime.now(timezone.utc)
        if not self.enabled:
            return
        try:
            self.client.table("alerts").insert({
                "opportunity_id": opportunity_id if opportunity_id and not opportunity_id.startswith("mem-") else None,
                "symbol": symbol.upper(),
                "telegram_chat_id": str(chat_id),
                "message": message,
            }).execute()
        except Exception as exc:
            self._fail("alerts", exc)

    def open_opportunities(self) -> list[dict[str, Any]]:
        rows = {k: v for k, v in self._opps.items() if v.get("status") == "OPEN"}
        if self.enabled:
            try:
                res = self.client.table("opportunities").select(TRACK_FIELDS).eq("status", "OPEN").execute()
                for r in res.data or []:
                    # Closed in this process but the DB update failed: don't close (and notify) twice.
                    local = self._opps.get(r["id"], {})
                    if local.get("status", "OPEN") == "OPEN":
                        rows[r["id"]] = {**r, "binance_listed_at": local.get("binance_listed_at") or r.get("binance_listed_at")}
            except Exception as exc:
                self._fail("tracking", exc)
        return [{**r, "detected_at": parse_ts(r.get("detected_at")), "expires_at": parse_ts(r.get("expires_at")),
                 "binance_listed_at": parse_ts(r.get("binance_listed_at"))}
                for r in rows.values()]

    def mark_listed(self, opp_id: str):
        """A pre-Binance signal whose asset got listed on Binance spot (notified once)."""
        now = datetime.now(timezone.utc)
        self._opps.setdefault(opp_id, {"id": opp_id})["binance_listed_at"] = now
        if self.enabled and not opp_id.startswith("mem-"):
            try:
                self.client.table("opportunities").update({"binance_listed_at": now.isoformat()}).eq("id", opp_id).execute()
            except Exception as exc:
                self._fail("tracking", exc)

    def close_opportunity(self, opp_id: str, status: str, close_price: float, result_pct: float):
        now = datetime.now(timezone.utc)
        # Always remembered locally: if the DB update fails, the row must not be
        # closed (and notified) again on the next run.
        self._opps.setdefault(opp_id, {"id": opp_id}).update(status=status, closed_at=now)
        if self.enabled and not opp_id.startswith("mem-"):
            try:
                self.client.table("opportunities").update({
                    "status": status, "closed_at": now.isoformat(),
                    "close_price": close_price, "result_pct": result_pct,
                }).eq("id", opp_id).eq("status", "OPEN").execute()
            except Exception as exc:
                self._fail("tracking", exc)

    def signal_rows(self) -> list[dict[str, Any]]:
        """status + detected_at of every tracked signal, for /status."""
        fields = ("status", "detected_at", "mode", "binance_listed_at")
        rows = {k: {f: v.get(f) for f in fields} for k, v in self._opps.items()}
        if self.enabled:
            try:
                res = self.client.table("opportunities").select("id," + ",".join(fields)).limit(10000).execute()
                for r in res.data or []:
                    rows[r["id"]] = {"status": r.get("status"), "detected_at": parse_ts(r.get("detected_at")),
                                     "mode": r.get("mode"), "binance_listed_at": parse_ts(r.get("binance_listed_at"))}
            except Exception as exc:
                self._fail("stats", exc)
        return list(rows.values())

    # ------------------------------------------------------------ watchlist
    def watchlist(self) -> list[str]:
        symbols = set(self._watch)
        if self.enabled:
            try:
                res = self.client.table("watchlist").select("symbol").eq("active", True).execute()
                symbols |= {r["symbol"] for r in res.data or []}
            except Exception as exc:
                self._fail("watchlist", exc)
        return sorted(symbols)

    def watch(self, symbol: str, active: bool = True):
        symbol = symbol.upper()
        (self._watch.add if active else self._watch.discard)(symbol)
        if self.enabled:
            try:
                self.client.table("watchlist").upsert({"symbol": symbol, "active": active}).execute()
            except Exception as exc:
                self._fail("watchlist", exc)
