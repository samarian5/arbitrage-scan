"""Seller quality gates — filter out low-quality listings before matching.

eBay: require ≥98% positive feedback, ≥100 transactions, not suspended.
Amazon: prefer "Sold by Amazon" or ≥4.5 star seller with ≥50 reviews.
"""
from __future__ import annotations

import re
from typing import Any

# eBay seller quality thresholds
EBAY_MIN_FEEDBACK_PCT = 98.0
EBAY_MIN_TRANSACTIONS = 100

# Amazon seller quality thresholds
AMAZON_MIN_RATING = 4.5
AMAZON_MIN_REVIEWS = 50


def ebay_seller_ok(listing: dict[str, Any]) -> bool:
    """Check if an eBay listing meets seller quality thresholds."""
    seller = listing.get("seller") or ""
    # Extract feedback percentage and count from seller string
    # Common formats: "99.5% positive (1,234)", "Top Rated Seller"
    fb_match = re.search(r"([\d.]+)%\s*positive", seller, re.I)
    tx_match = re.search(r"\(([\d,]+)\)", seller)
    if fb_match:
        pct = float(fb_match.group(1))
        if pct < EBAY_MIN_FEEDBACK_PCT:
            return False
    if tx_match:
        tx = int(tx_match.group(1).replace(",", ""))
        if tx < EBAY_MIN_TRANSACTIONS:
            return False
    # Suspended/blocked sellers
    if re.search(r"suspended|blocked|not available", seller, re.I):
        return False
    return True


def amazon_seller_ok(listing: dict[str, Any]) -> bool:
    """Check if an Amazon listing meets seller quality thresholds."""
    seller = listing.get("seller") or ""
    ships_from_amazon = listing.get("ships_from_amazon", False)
    rating = listing.get("rating")
    reviews = listing.get("reviews")

    # "Ships from Amazon" or "Sold by Amazon" → always OK
    if ships_from_amazon or "amazon" in seller.lower():
        return True

    # Third-party: check rating
    if rating is not None and rating < AMAZON_MIN_RATING:
        return False
    if reviews is not None:
        try:
            r = int(str(reviews).replace(",", ""))
            if r < AMAZON_MIN_REVIEWS:
                return False
        except (ValueError, TypeError):
            pass
    return True


def filter_listings(listings: list[dict[str, Any]], platform: str) -> list[dict[str, Any]]:
    """Filter listings by seller quality. Returns only qualifying listings."""
    if platform == "ebay":
        return [l for l in listings if ebay_seller_ok(l)]
    elif platform == "amazon":
        return [l for l in listings if amazon_seller_ok(l)]
    return listings


def seller_score(listing: dict[str, Any]) -> float:
    """Return a 0-1 seller quality score for ranking."""
    platform = listing.get("platform", "")
    seller = listing.get("seller") or ""

    if platform == "amazon":
        if listing.get("ships_from_amazon") or "amazon" in seller.lower():
            return 1.0
        rating = listing.get("rating")
        if rating:
            try:
                return float(rating) / 5.0
            except (ValueError, TypeError):
                pass
        return 0.5

    if platform == "ebay":
        fb_match = re.search(r"([\d.]+)%\s*positive", seller, re.I)
        if fb_match:
            return float(fb_match.group(1)) / 100.0
        return 0.5

    return 0.5
PY