"""Stockiya middleware (HTTP API layer).

Responsibilities:
- Serve the day's precomputed picks (from `data/picks_<date>.json`)
- Serve the volume-strategy detail panel for any Nifty 100 ticker
- Cache stock-detail responses in-process for 15 minutes

This is where the HTTP world meets the data world. The pipeline itself
(scoring, ranking, hypothesis, render) lives in `backend/`. No LLM calls.
"""
