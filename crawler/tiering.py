"""Tiered scheduling: adjust keyword interval_hours based on opportunity size.

Hot products (big gaps) get re-checked every 2-4 hours.
Cold products (no gap or small gap) get 24h intervals.
Blocked keywords get exponential backoff.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.supa import Supa


def tier_keyword(keyword_id: int, net_gap_cents: int | None, blocked: bool = False) -> int:
    """Determine the interval_hours for a keyword based on its best gap.

    Returns the new interval_hours value.
    """
    if blocked:
        return 0  # handled separately by backoff
    if net_gap_cents is None:
        return 24  # no match yet, check daily
    gap_dollars = net_gap_cents / 100.0
    if gap_dollars >= 50:
        return 2   # hot: re-check every 2 hours
    if gap_dollars >= 25:
        return 4   # warm: every 4 hours
    if gap_dollars >= 10:
        return 8   # medium: every 8 hours
    if gap_dollars >= 5:
        return 12  # cool: every 12 hours
    return 24      # cold: daily


def update_keyword_interval(db: Supa, keyword_id: int, interval_hours: int) -> None:
    """Update a keyword's interval_hours in the database."""
    db._req(
        f"ar_keywords?id=eq.{keyword_id}",
        "PATCH",
        {"interval_hours": interval_hours},
    )


def backoff_keyword(db: Supa, keyword_id: int, current_fails: int) -> None:
    """Apply exponential backoff for blocked keywords.

    1 fail → 20 min, 2 → 40 min, 3 → 80 min, capped at ~6 hours.
    """
    minutes = min(20 * (2 ** max(0, current_fails - 1)), 360)
    due = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    db._req(
        f"ar_keywords?id=eq.{keyword_id}",
        "PATCH",
        {
            "status": "queued",
            "due_at": due.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fails": current_fails + 1,
        },
    )


def apply_tiering(db: Supa, keyword_id: int, net_gap_cents: int | None,
                  blocked: bool = False, current_fails: int = 0) -> None:
    """Apply tiering logic to a keyword after a run completes."""
    if blocked:
        backoff_keyword(db, keyword_id, current_fails)
        return
    interval = tier_keyword(keyword_id, net_gap_cents, blocked=False)
    update_keyword_interval(db, keyword_id, interval)
    # Reset fails on success
    db._req(f"ar_keywords?id=eq.{keyword_id}", "PATCH", {"fails": 0})
PY