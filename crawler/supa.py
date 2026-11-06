"""Minimal PostgREST client for the Supabase project (service key, runner-only)."""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any


class Supa:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.environ["SUPABASE_URL"]).rstrip("/")
        self.key = key or os.environ["SUPABASE_SERVICE_KEY"]

    def _req(self, path: str, method: str = "GET", body: Any = None, retries: int = 3):
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries):
            req = urllib.request.Request(
                f"{self.url}/rest/v1/{path}", data=data, method=method,
                headers={"apikey": self.key, "Authorization": f"Bearer {self.key}",
                         "Content-Type": "application/json",
                         "Accept": "application/json",
                         "Prefer": "return=representation"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read().decode() or "null"
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                detail = e.read().decode()[:400]
                if attempt == retries - 1:
                    raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from e
                time.sleep(2 + attempt * 3)
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(2 + attempt * 3)
        return None

    def rpc(self, fn: str, args: dict) -> Any:
        return self._req(f"rpc/{fn}", "POST", args)

    def claim(self, slot: str, pages: int | None = None) -> dict | None:
        rows = self.rpc("ar_claim_slice", {"p_slot": slot, "p_pages": pages})
        return rows[0] if isinstance(rows, list) and rows else None

    def finish(self, keyword_id: int, pages_done: int, status: str = "queued",
               reason: str | None = None) -> None:
        self.rpc("ar_finish_slice", {"p_keyword_id": keyword_id, "p_pages_done": pages_done,
                                     "p_status": status, "p_reason": reason})

    def ingest(self, run_id: str, rows: list[dict]) -> dict:
        return self.rpc("ar_ingest", {"p_run_id": run_id, "p_payload": rows})

    def upsert_matches(self, matches: list[dict]) -> int:
        return self.rpc("ar_upsert_matches", {"p_matches": matches})

    def insert_run(self, row: dict) -> None:
        self._req("ar_runs", "POST", row)

    def update_run(self, run_id: str, patch: dict) -> None:
        self._req(f"ar_runs?run_id=eq.{run_id}", "PATCH", patch)

    def setting(self, key: str, default: str | None = None) -> str | None:
        rows = self._req(f"ar_settings?key=eq.{key}&select=value")
        return rows[0]["value"] if rows else default
