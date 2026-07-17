"""Stage 5 server — Molly history product (same as main.py).

Multi-source /ask with provenance + conflict guardrails and cost readout.

Run: uvicorn serve_stage5:app --port 8000 --reload
     or: uvicorn main:app --port 8000 --reload

Home UI: http://127.0.0.1:8000/  (provenance build · multi-source)
"""

from main import app  # noqa: F401
