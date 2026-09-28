"""
Shared plumbing for every provider: HTTP with timeout + limited retry, and a
small thread-safe TTL cache so the same data is never fetched twice per window.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Any, Callable
import requests

log = logging.getLogger(__name__)

TIMEOUT = 20
RETRY_STATUS = {429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    """A provider could not deliver data. Callers treat the layer as unavailable."""

    def __init__(self, message: str, body: Any = None):
        super().__init__(message)
        self.body = body  # parsed error payload, when the API sent one


def http_get_json(url: str, *, params: dict | None = None, headers: dict | None = None,
                  timeout: float = TIMEOUT, retries: int = 2, backoff: float = 1.5) -> Any:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code in RETRY_STATUS and attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as exc:
            # 4xx other than 429 will not get better with a retry.
            try:
                body = exc.response.json()
            except ValueError:
                body = None
            raise ProviderError(f"{url} -> HTTP {exc.response.status_code}", body=body) from exc
        except (requests.ConnectionError, requests.Timeout, ValueError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise ProviderError(f"{url} -> {last_exc}")


MISS = object()


class TTLCache:
    def __init__(self):
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def get(self, key: str, ttl: float) -> Any:
        with self._lock:
            hit = self._data.get(key)
        return hit[1] if hit and time.time() - hit[0] < ttl else MISS

    def set(self, key: str, value: Any):
        with self._lock:
            self._data[key] = (time.time(), value)

    def get_or_set(self, key: str, ttl: float, fn: Callable[[], Any]) -> Any:
        value = self.get(key, ttl)
        if value is not MISS:
            return value
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        # One fetch per key: parallel deep-stage workers must not pay twice for
        # the same (possibly metered) request. Different keys still run in parallel.
        with key_lock:
            value = self.get(key, ttl)
            if value is MISS:
                value = fn()
                self.set(key, value)
        return value

    def clear(self):
        with self._lock:
            self._data.clear()
            self._key_locks.clear()


cache = TTLCache()


class RateLimiter:
    """Spaces out calls to plans with tight per-minute limits (thread-safe)."""

    def __init__(self, min_interval_s: float):
        self.min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            delay = self._last + self.min_interval_s - time.time()
            if delay > 0:
                time.sleep(delay)
            self._last = time.time()


ACCESS_DENIED = ("HTTP 401", "HTTP 402", "HTTP 403")


class KeyedProvider:
    """
    Paid API with an optional key. A 401/402/403 means the key or its plan has no
    access to the endpoint: the provider switches itself off for this run instead
    of failing (and waiting on its rate limiter) for every asset, and its layer stops
    counting against data coverage.
    """
    name = "provider"
    api_key = ""
    blocked: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) and not self.blocked

    def guard(self, fn: Callable[[], Any]) -> Any:
        if self.blocked:
            # Calls already in flight in other threads stop here, without waiting on the rate limiter.
            raise ProviderError(f"{self.name} desligado: {self.blocked}")
        try:
            return fn()
        except ProviderError as exc:
            if any(code in str(exc) for code in ACCESS_DENIED) and not self.blocked:
                self.blocked = str(exc)
                log.warning("%s desligado nesta execução: chave ou plano sem acesso (%s)", self.name, exc)
            raise


def safe_call(label: str, fn: Callable[[], Any], default: Any = None) -> Any:
    """Runs an optional provider call; failures are logged and become `default`."""
    try:
        return fn()
    except Exception as exc:
        log.warning("%s indisponível: %s", label, exc)
        return default


def pct_change(now: float | None, before: float | None) -> float | None:
    if now is None or before is None or before <= 0:
        return None
    return round((now / before - 1) * 100, 2)


def to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
