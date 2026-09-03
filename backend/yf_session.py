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
  STOCKYA_YF_MIN_INTERVAL   seconds between dispatched calls   (default 2.0)
  STOCKYA_YF_JITTER         max extra random seconds per call  (default 0.5)
  STOCKYA_YF_MAX_RETRIES    attempts per call                  (default 5)
  STOCKYA_YF_BACKOFF_BASE   base backoff s, doubles each retry (default 1.0)
  STOCKYA_YF_BACKOFF_MAX    cap on one backoff sleep           (default 60)
  STOCKYA_YF_429_SLEEP      extra sleep after a detected 429   (default 20)
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
# Global cross-thread throttle + shared cooldown
# --------------------------------------------------------------------------- #
_throttle_lock = threading.Lock()
_last_call = 0.0        # time.monotonic() when the previous call was dispatched
_cooldown_until = 0.0   # time.monotonic() before which NO thread may dispatch


def _register_cooldown(seconds: float) -> None:
    """Push a process-wide dispatch freeze `seconds` into the future.

    Called when ANY thread sees a 429: because Yahoo rate-limits by IP, one
    thread getting throttled means every other worker should back off too. This
    turns a per-call retry into a fleet-wide pause — the key fix for the
    10-worker concurrent case.
    """
    global _cooldown_until
    if seconds <= 0:
        return
    with _throttle_lock:
        target = time.monotonic() + seconds
        if target > _cooldown_until:
            _cooldown_until = target


def _throttle() -> None:
    """Block until it is safe to dispatch the next yfinance HTTP call.

    Two gates, both cross-thread: (1) space dispatch by ``STOCKYA_YF_MIN_INTERVAL``
    (+ jitter) so 10 workers can't burst Yahoo at once; (2) honour any active
    shared cooldown set by a recent 429. Network calls still overlap — only
    dispatch is paced.
    """
    global _last_call
    min_interval = max(0.0, _envf("STOCKYA_YF_MIN_INTERVAL", 2.0))
    jitter = max(0.0, _envf("STOCKYA_YF_JITTER", 0.5))
    while True:
        with _throttle_lock:
            now = time.monotonic()
            target = max(_last_call + min_interval, _cooldown_until)
            wait = target - now
            if wait <= 0:
                if jitter:
                    time.sleep(random.uniform(0.0, jitter))
                _last_call = time.monotonic()
                return
        # Sleep OUTSIDE the lock so a concurrent _register_cooldown can extend
        # the freeze while we wait; then re-check.
        time.sleep(min(wait, 1.0))


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
    attempts = max(1, _envi("STOCKYA_YF_MAX_RETRIES", 5))
    base = max(0.0, _envf("STOCKYA_YF_BACKOFF_BASE", 1.0))
    cap = max(0.0, _envf("STOCKYA_YF_BACKOFF_MAX", 60.0))
    extra_429 = max(0.0, _envf("STOCKYA_YF_429_SLEEP", 20.0))
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
                # Freeze the whole fleet, not just this thread — Yahoo limits by IP.
                _register_cooldown(sleep_s)
                log.warning(
                    "%s rate-limited (attempt %d/%d) — fleet cooldown %.1fs%s",
                    _label, i + 1, attempts, sleep_s,
                    "" if _session() is not None else "  [curl_cffi NOT installed — see logs]",
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

    Impersonation is the best *passive* 429 defence — Yahoo throttles the default
    python-requests user-agent hardest and often blocks its crumb/cookie fetch
    outright, so WITHOUT this you get "Too Many Requests" even for a single call
    no matter how much you throttle. Entirely optional: if curl_cffi is missing
    or STOCKYA_YF_IMPERSONATE=0, returns None (callers fall back to yfinance's
    own session, still throttled + retried), and we log a LOUD one-time warning
    because this is the usual root cause of persistent rate limiting.
    """
    if not _envb("STOCKYA_YF_IMPERSONATE", True):
        log.warning("yf_session: impersonation disabled (STOCKYA_YF_IMPERSONATE=0) "
                    "— Yahoo will rate-limit aggressively.")
        return None
    try:
        from curl_cffi import requests as _cffi  # type: ignore
        sess = _cffi.Session(impersonate="chrome")
        log.info("yf_session: browser impersonation ACTIVE (curl_cffi) — best 429 defence.")
        return sess
    except Exception as e:  # noqa: BLE001
        log.warning(
            "yf_session: curl_cffi NOT available (%s). Yahoo will rate-limit hard. "
            "FIX: pip install curl_cffi  (this is the #1 cause of persistent 429s).",
            e,
        )
        return None


# One-time flag: warn if yfinance ends up NOT using our impersonating session.
_session_applied_checked = False


def get_ticker(symbol: str):
    """yfinance.Ticker for ``symbol``, with the shared session when accepted.

    yfinance's ``session=`` kwarg is version-dependent; if it is rejected we fall
    back to a plain Ticker so this never breaks across yfinance versions. Newer
    yfinance uses curl_cffi internally and will pick it up automatically once the
    package is installed even if it ignores our explicit session.
    """
    global _session_applied_checked
    import yfinance as yf  # deferred: keep offline import dependency-free
    sess = _session()
    if sess is not None:
        try:
            tk = yf.Ticker(symbol, session=sess)
            if not _session_applied_checked:
                _session_applied_checked = True
                applied = getattr(tk, "session", None) is sess
                if not applied:
                    log.info("yf_session: yfinance ignored the explicit session "
                             "(version uses curl_cffi internally — that's fine).")
            return tk
        except Exception:  # noqa: BLE001 — signature rejects session kwarg
            if not _session_applied_checked:
                _session_applied_checked = True
                log.info("yf_session: this yfinance rejects session=; relying on its "
                         "internal curl_cffi (install curl_cffi to enable it).")
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


# --------------------------------------------------------------------------- #
# Self-test / diagnostics —  python -m backend.yf_session [SYMBOL] [--info]
# --------------------------------------------------------------------------- #
def selftest(symbol: str = "AAPL", with_info: bool = False) -> dict:
    """Report the yfinance environment + do ONE throttled fetch. Never raises.

    Run this on the machine that shows rate-limit errors — its output tells you
    (and me) exactly why: whether curl_cffi impersonation is active, whether a
    single fetch succeeds, and the precise error text otherwise.
    """
    report: dict = {"symbol": symbol}
    try:
        import importlib.metadata as _md
        report["yfinance_version"] = _md.version("yfinance")
    except Exception as e:  # noqa: BLE001
        report["yfinance_version"] = f"MISSING ({e})"
    try:
        import importlib.metadata as _md
        report["curl_cffi_version"] = _md.version("curl_cffi")
    except Exception:  # noqa: BLE001
        report["curl_cffi_version"] = "NOT INSTALLED"
    report["impersonation_active"] = _session() is not None
    report["env"] = {
        "STOCKYA_YF_MIN_INTERVAL": _envf("STOCKYA_YF_MIN_INTERVAL", 2.0),
        "STOCKYA_YF_MAX_RETRIES": _envi("STOCKYA_YF_MAX_RETRIES", 5),
        "STOCKYA_YF_429_SLEEP": _envf("STOCKYA_YF_429_SLEEP", 20.0),
        "STOCKYA_YF_CACHE": _envb("STOCKYA_YF_CACHE", True),
        "STOCKYA_YF_IMPERSONATE": _envb("STOCKYA_YF_IMPERSONATE", True),
        "STOCKYA_YF_SKIP_INFO": _envb("STOCKYA_YF_SKIP_INFO", False),
    }

    # One real fetch (cache bypassed so it's a true network probe).
    prev_cache = os.environ.get("STOCKYA_YF_CACHE")
    os.environ["STOCKYA_YF_CACHE"] = "0"
    try:
        tk = get_ticker(symbol)
        df = history(tk, symbol, "selftest", period="5d", auto_adjust=True)
        report["history_rows"] = int(len(df))
        report["history_ok"] = not df.empty
        fi = fast_info(tk, symbol)
        report["fast_info_last_price"] = fi.get("last_price")
        if with_info:
            report["info_keys"] = len(info(tk, symbol))
    except Exception as e:  # noqa: BLE001
        report["error"] = f"{type(e).__name__}: {e}"
    finally:
        if prev_cache is None:
            os.environ.pop("STOCKYA_YF_CACHE", None)
        else:
            os.environ["STOCKYA_YF_CACHE"] = prev_cache
    return report


def _main() -> None:
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = [a for a in sys.argv[1:]]
    with_info = "--info" in args
    syms = [a for a in args if not a.startswith("--")] or ["AAPL"]

    print("=" * 60)
    print("  yf_session self-test")
    print("=" * 60)
    for sym in syms:
        rep = selftest(sym, with_info=with_info)
        print(json.dumps(rep, indent=2, default=str))
    print("-" * 60)
    print("Hints:")
    print("  * impersonation_active=false  -> run: pip install curl_cffi")
    print("  * history_ok=false with a 429  -> IP is throttled; wait, raise")
    print("    STOCKYA_YF_MIN_INTERVAL (e.g. 4), and prefer DATA_SOURCE=bhavcopy.")
    print("  * Try both a US symbol (AAPL) and an NSE one (RELIANCE.NS) to tell")
    print("    an IP-wide block apart from a symbol/exchange issue.")


if __name__ == "__main__":
    _main()
