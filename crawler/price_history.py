"""Price history — track price changes over time for arbitrage opportunities.

Provides:
  - record_price(): store a price observation
  - price_history(): get price series for a listing
  - gap_trend(): compute gap trend (growing/shrinking/stable)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.supa import Supa


def price_history(db: Supa, listing_id: int, days: int = 7) -> list[dict]:
    """Get price history for a listing over the last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    rows = db._req(
        f"ar_snapshots?listing_id=eq.{listing_id}"
        f"&captured_at=gte.{cutoff}"
        f"&select=price_cents,shipping_cents,captured_at,seller,in_stock"
        f"&order=captured_at.asc",
        "GET",
    )
    return rows or []


def gap_trend(db: Supa, ebay_listing_id: int, amazon_listing_id: int,
              days: int = 7) -> dict:
    """Compute gap trend between an eBay and Amazon listing pair."""
    ebay_hist = price_history(db, ebay_listing_id, days)
    amazon_hist = price_history(db, amazon_listing_id, days)

    if not ebay_hist or not amazon_hist:
        return {"trend": "unknown", "data_points": 0}

    # Align by nearest timestamp
    points = []
    for e in ebay_hist:
        # Find closest Amazon snapshot
        best_a = min(amazon_hist, key=lambda a: abs(
            _parse_ts(a["captured_at"]) - _parse_ts(e["captured_at"])
        ))
        e_price = (e.get("price_cents") or 0) + (e.get("shipping_cents") or 0)
        a_price = (best_a.get("price_cents") or 0) + (best_a.get("shipping_cents") or 0)
        gap = e_price - a_price
        points.append({
            "ts": e["captured_at"],
            "ebay_cents": e_price,
            "amazon_cents": a_price,
            "gap_cents": gap,
        })

    if len(points) < 2:
        return {"trend": "insufficient", "data_points": len(points), "points": points}

    first_gap = points[0]["gap_cents"]
    last_gap = points[-1]["gap_cents"]
    delta = last_gap - first_gap

    if delta > 500:  # $5+ growing
        trend = "growing"
    elif delta < -500:  # $5+ shrinking
        trend = "shrinking"
    else:
        trend = "stable"

    return {
        "trend": trend,
        "delta_cents": delta,
        "first_gap_cents": first_gap,
        "last_gap_cents": last_gap,
        "data_points": len(points),
        "points": points,
    }


def _parse_ts(ts: str) -> float:
    """Parse ISO timestamp to epoch seconds."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return 0.0
PY