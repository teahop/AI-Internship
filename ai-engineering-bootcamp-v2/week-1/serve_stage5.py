"""Stage 5 server — Molly history product (same as main.py).

Staged pipeline: /extract → /conflicts → /draft.
/ask runs that pipeline under the course-assignment contract.
/ingest classifies raw documents for user confirmation.

Run: uvicorn serve_stage5:app --port 8000 --reload
     or: uvicorn main:app --port 8000 --reload

Home UI: http://127.0.0.1:8000/  (staged pipeline · extract → conflicts → draft)
"""

from main import app  # noqa: F401
