"""Centralized, rate-limit-hardened access to yfinance.

Why this module exists
----------------------
Yahoo Finance aggressively rate-limits (HTTP 429) when many requests arrive in
a short burst — and Stockya's per-ticker chain runs 10 worker threads
(`orchestrator.py`). A DATA_SOURCE=yahoo run would otherwise fire ~10
*concurrent* requests × ~750 tickers × 2-3 calls each and get throttled almost
immediately. The old per-call retry in `yahoo.py` backed off within one call
but did nothing about the 10-wide burst.

Every yfinance touch in the codebase now routes through here, so throttling is
enforced in ONE place. All of it is env-tunable and reversible.

What it does
------------
1. GLOBAL CROSS-THREAD THROTTLE — a process-wide lock spaces the *dispatch* of
   any two yfinance HTTP calls by a minimum interval (+ jitter). With 10 worker
   threads this turns a 10-wide burst into a steady trickle. This is the single
   most effective 429 defence and needs zero extra dependencies.
2. 429-AWARE RETRY — exponential backoff that backs off HARD on rate-limit
   errors (429 / "Too Many Requests" / YFRateLimitError), honouring
   ``Retry-After`` when the exception carries it. Empty frames (Yahoo returns
   empty under load) are retried too.
3. ON-DISK DAY CACHE — history frames are cached to ``data/yf_cache`` keyed by
   symbol + request signature + day, so re-running the same day (or repeating a
   backtest window) is nearly free. Dependency-free (pickle). Empty frames are
   never cached.
4. OPTIONAL BROWSER IMPERSONATION — if ``curl_cffi`` is installed, a shared
   impersonating session is attached to every Ticker (Yahoo throttles the
   default python UA hardest). Fully optional and guarded: absent or
   incompatible → falls back to yfinance's own session, still throttled.

Env knobs (safe defaults; unset == default)
--------------------------------------------
  STOCKYA_YF_MIN_INTERVAL   seconds between dispatched calls   (default 1.5)
  STOCKYA_YF_JITTER         max extra random seconds per call  (default 0.5)
  STOCKYA_YF_MAX_RETRIES    attempts per call                  (default 4)
  STOCKYA_YF_BACKOFF_BASE   base backoff s, doubles each retry (default 1.0)
  STOCKYA_YF_BACKOFF_MAX    cap on one backoff sleep           (default 30)
  STOCKYA_YF_429_SLEEP      extra sleep after a detected 429   (default 15)
  STOCKYA_YF_CACHE          "1" on-disk history cache          (default on)
  STOCKYA_YF_CACHE_TTL      history cache TTL seconds          (default 21600)
  STOCKYA_YF_CACHE_KEEP_DAYS prune cache files older than N d  (default 5)
  STOCKYA_YF_IMPERSONATE    use curl_cffi session when present (default on)
  STOCKYA_YF_SKIP_INFO      skip the heavy .info call, use     (default off)
                            fast_info + sector map instead

Nothing here raises: a fully offline / dependency-less environment still works
— it just falls back to plain yfinance with the throttle + retry applied. The
``import yfinance`` is deferred inside functions so merely importing this module
carries no hard yfinance dependency (keeps the bhavcopy/offline default clean).
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

log = logging.getLogger("yf_session")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "yf_cache"


# --------------------------------------------------------------------------- #
# Env helpers — read per-call so the launch environment (and tests) always win.
# --------------------------------------------------------------------------- #
def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _envb(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def skip_info() -> bool:
    """True => snapshot() uses fast_info instead of the heavy .info call."""
    return _envb("STOCKYA_YF_SKIP_INFO", False)


# --------------------------------------------------------------------------- #
# Global cross-thread throttle
# --------------------------------------------------------------------------- #
_throttle_lock = threading.Lock()
_last_call = 0.0  # time.monotonic() when the previous call was dispatched


def _throttle() -> None:
    """Block until it is safe to dispatch the next yfinance HTTP call.

    Serialised across ALL threads: with 10 worker threads this spaces request
    *dispatch* by ``STOCKYA_YF_MIN_INTERVAL`` (+ jitter) instead of letting a
    10-wide burst hit Yahoo at once. Network calls themselves still overlap —
    only dispatch is paced — so throughput is ~1 new request per interval.
    """
    global _last_call
    min_interval = max(0.0, _envf("STOCKYA_YF_MIN_INTERVAL", 1.5))
    jitter = max(0.0, _envf("STOCKYA_YF_JITTER", 0.5))
    with _throttle_lock:
        now = time.monotonic()
        wait = (_last_call + min_interval) - now
        if wait > 0:
            time.sleep(wait)
        if jitter:
            time.sleep(random.uniform(0.0, jitter))
        _last_call = time.monotonic()


# --------------------------------------------------------------------------- #
# 429 detection + retry
# --------------------------------------------------------------------------- #
class _Empty(Exception):
    """Internal: yfinance returned an empty frame — treated as retryable."""


def _is_rate_limited(err: BaseException) -> bool:
    s = f"{type(err).__name__}: {err}".lower()
    return any(k in s for k in ("429", "too many requests", "rate limit", "ratelimit"))


def _retry_after(err: BaseException) -> Optional[float]:
    """Best-effort ``Retry-After`` (seconds) from an exception's HTTP response."""
    resp = getattr(err, "response", None)
    if resp is None:
        return None
    try:
        ra = resp.headers.get("Retry-After")
        return float(ra) if ra else None
    except Exception:
        return None


def call(fn: Callable[..., Any], *args: Any, _label: str = "yf", **kwargs: Any) -> Any:
    """Run a yfinance callable through the throttle with 429-aware retry.

    Returns ``fn(*args, **kwargs)``. Re-raises the last error once retries are
    exhausted, so callers keep their existing empty/None fallback behaviour.
    """
    attempts = max(1, _envi("STOCKYA_YF_MAX_RETRIES", 4))
    base = max(0.0, _envf("STOCKYA_YF_BACKOFF_BASE", 1.0))
    cap = max(0.0, _envf("STOCKYA_YF_BACKOFF_MAX", 30.0))
    extra_429 = max(0.0, _envf("STOCKYA_YF_429_SLEEP", 15.0))
    last: Optional[BaseException] = None
    for i in range(attempts):
        _throttle()
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            if i >= attempts - 1:
                break
            backoff = min(cap, base * (2 ** i)) if base else 0.0
            if _is_rate_limited(e):
                ra = _retry_after(e)
                sleep_s = ra if (ra and ra > 0) else backoff + extra_429
                log.warning(
                    "%s rate-limited (attempt %d/%d) — backing off %.1fs",
                    _label, i + 1, attempts, sleep_s,
                )
            else:
                sleep_s = backoff
            if sleep_s > 0:
                time.sleep(sleep_s)
    assert last is not None
    raise last


# --------------------------------------------------------------------------- #
# Optional browser-impersonating shared session (curl_cffi)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _session() -> Optional[Any]:
    """One curl_cffi browser-impersonating session, or None.

    Impersonation is the best *passive* 429 defence (Yahoo throttles the default
    python-requests UA hardest). Entirely optional: if curl_cffi is missing or
    STOCKYA_YF_IMPERSONATE=0, returns None and callers use yfinance's own
    session (still throttled + retried by us).
    """
    if not _envb("STOCKYA_YF_IMPERSONATE", True):
        return None
    try:
        from curl_cffi import requests as _cffi  # type: ignore
        return _cffi.Session(impersonate="chrome")
    except Exception as e:  # noqa: BLE001
        log.debug("curl_cffi session unavailable (%s); using yfinance default", e)
        return None


def get_ticker(symbol: str):
    """yfinance.Ticker for ``symbol``, with the shared session when accepted.

    yfinance's ``session=`` kwarg is version-dependent; if it is rejected we
    fall back to a plain Ticker so this never breaks across yfinance versions.
    """
    import yfinance as yf  # deferred: keep offline import dependency-free
    sess = _session()
    if sess is not None:
        try:
            return yf.Ticker(symbol, session=sess)
        except Exception:  # noqa: BLE001 — signature rejects session kwarg
            pass
    return yf.Ticker(symbol)


# --------------------------------------------------------------------------- #
# On-disk day cache for history frames (dependency-free pickle)
# --------------------------------------------------------------------------- #
_pruned_once = False


def _cache_path(symbol: str, sig: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in f"{symbol}__{sig}")
    return _CACHE_DIR / f"{safe}.pkl"


def _read_cache(path: Path, ttl_s: float) -> Optional[pd.DataFrame]:
    try:
        if not path.exists():
            return None
        if ttl_s > 0 and (time.time() - path.stat().st_mtime) > ttl_s:
            return None
        df = pd.read_pickle(path)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    except Exception:  # noqa: BLE001 — a bad cache file must never break a fetch
        return None
    return None


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    if df is None or df.empty:  # never cache an empty/failed fetch
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_pickle(path)
    except Exception:  # noqa: BLE001
        pass


def _maybe_prune() -> None:
    """Delete cache files older than STOCKYA_YF_CACHE_KEEP_DAYS. Once per process."""
    global _pruned_once
    if _pruned_once:
        return
    _pruned_once = True
    keep = _envi("STOCKYA_YF_CACHE_KEEP_DAYS", 5)
    if keep <= 0:
        return
    cutoff = time.time() - keep * 86400
    try:
        for p in _CACHE_DIR.glob("*.pkl"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Public yfinance operations (throttled + retried + cached)
# --------------------------------------------------------------------------- #
def history(ticker: Any, symbol: str, sig: str, **kwargs: Any) -> pd.DataFrame:
    """Throttled + retried ``Ticker.history``; empty result retried too.

    ``sig`` is a stable per-request signature (period+day, or start+end window)
    used as the cache key. Returns an empty DataFrame on failure — never raises.
    """
    use_cache = _envb("STOCKYA_YF_CACHE", True)
    path = _cache_path(symbol, sig) if use_cache else None
    if path is not None:
        cached = _read_cache(path, _envf("STOCKYA_YF_CACHE_TTL", 21600.0))
        if cached is not None:
            return cached

    def _once() -> pd.DataFrame:
        h = ticker.history(**kwargs)
        if h is None or getattr(h, "empty", True):
            raise _Empty()  # retryable: Yahoo returns empty under load
        return h

    try:
        df = call(_once, _label=f"history[{symbol}]")
    except _Empty:
        return pd.DataFrame()
    except Exception as e:  # noqa: BLE001
        log.warning("history(%s, %s) failed after retries: %s", symbol, sig, e)
        return pd.DataFrame()

    if path is not None:
        _write_cache(path, df)
        _maybe_prune()
    return df


def info(ticker: Any, symbol: str) -> dict:
    """Throttled + retried ``Ticker.info``. ``{}`` on failure (never raises)."""
    try:
        return call(lambda: ticker.info or {}, _label=f"info[{symbol}]") or {}
    except Exception as e:  # noqa: BLE001
        log.warning("info(%s) failed after retries: %s", symbol, e)
        return {}


_FAST_INFO_KEYS = (
    "last_price", "previous_close", "year_high", "year_low",
    "fifty_day_average", "two_hundred_day_average",
    "day_high", "day_low", "open", "currency",
)


def fast_info(ticker: Any, symbol: str) -> dict:
    """Throttled + retried ``Ticker.fast_info`` as a plain dict. ``{}`` on failure.

    fast_info is far lighter on Yahoo's side than ``.info`` (no quoteSummary
    call), so it 429s much less. Used when STOCKYA_YF_SKIP_INFO=1 to drop the
    heaviest per-ticker call on full-universe yahoo runs.
    """
    try:
        fi = call(lambda: ticker.fast_info, _label=f"fast_info[{symbol}]")
    except Exception as e:  # noqa: BLE001
        log.warning("fast_info(%s) failed after retries: %s", symbol, e)
        return {}
    out: dict = {}
    for k in _FAST_INFO_KEYS:
        try:
            out[k] = getattr(fi, k)
        except Exception:  # noqa: BLE001 — some builds expose mapping access only
            try:
                out[k] = fi[k]
            except Exception:  # noqa: BLE001
                out[k] = None
    return out
