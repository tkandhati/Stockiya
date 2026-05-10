"""Stockiya backend (data layer).

Responsibilities:
- Download daily OHLCV data (Yahoo Finance / NSE bhavcopy / demo fixtures)
- Compute volume-strategy signals for every ticker in the universe
- Tag with sector / headwind / valuation context
- Persist prepared signals to disk so the middleware can read without recomputing

This package is offline-first: it should run as a nightly cron job and produce
JSON artifacts the middleware reads. No HTTP, no LLM calls.
"""
