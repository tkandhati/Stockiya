"""Stockiya backend (volume-only pipeline).

Responsibilities:
- Download daily OHLCV data (Yahoo Finance / NSE bhavcopy / demo fixtures)
- Run the modular pipeline (backend/pipeline.py + backend/stages/*) to
  produce a ranked, scored set of 0-3 picks per day.
- Persist outputs as JSON artifacts (data/picks_<date>.json) and per-ticker
  JSONL traces (data/traces/) the middleware reads.

This package is offline-first: it runs as a nightly cron job. No HTTP, no
LLM calls — every decision is deterministic and reproducible from OHLCV.
"""
