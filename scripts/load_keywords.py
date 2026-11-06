#!/usr/bin/env python3
"""Load a keyword list into the queue (idempotent on the keyword text).

Staggers due_at across 24 h so 2 000 keywords do not all become due in the same
minute and then starve the daily page budget: the queue drains in a rolling wave.

  python scripts/load_keywords.py data/keywords.txt
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.supa import Supa  # noqa: E402


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "data/keywords.txt"
    depth = int(os.environ.get("DEPTH", "2"))
    interval = int(os.environ.get("INTERVAL_HOURS", "24"))
    kws = [ln.strip() for ln in open(path) if ln.strip() and not ln.startswith("#")]
    print(f"{len(kws)} keywords from {path}")

    db = Supa()
    # Timed by the database: a skewed client clock must never place work in the
    # future. ar_seed_keywords staggers with now() and repairs mis-timed rows.
    res = db.rpc("ar_seed_keywords", {"p_keywords": kws, "p_depth": depth,
                                      "p_interval_hours": interval,
                                      "p_spread_minutes": 1440})
    print("queue:", res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
