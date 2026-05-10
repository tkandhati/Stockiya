"""Stockiya middleware (intelligence + API layer).

Responsibilities:
- Read prepared signals from the backend's disk artifacts
- Optionally call Claude (LLM) to enhance pick rationale text
- Expose HTTP endpoints (/api/picks, /api/stock/{symbol}, /api/health)
- Cache daily picks output

This is where the HTTP world meets the data world. The UI hits this layer.
The backend layer must already have produced the prepared artifacts — this
layer never fetches market data itself.
"""
