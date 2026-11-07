"""Parse Amazon product detail pages (/dp/{ASIN}).

Extracts: ASIN, UPC/EAN, brand, exact title, price, seller, stock status,
variants, and rating. Used to enrich search-result listings with canonical
product identifiers for identifier-based matching.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

MONEY_RE = re.compile(r"\$?\s*([\d,]+\.\d{2})")


def _cents(text: str | None) -> int | None:
    if not text:
        return None
    m = MONEY_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return int(round(float(m.group(1)) * 100))
    except ValueError:
        return None


def parse_detail_html(html: str, asin: str | None = None) -> dict[str, Any] | None:
    """Parse an Amazon /dp/{ASIN} page. Returns enriched product dict or None."""
    soup = BeautifulSoup(html, "html.parser")
    low = html.lower()

    # Detect blocked/captcha
    if "captcha" in low and "add to cart" not in low:
        return None
    if len(html) < 5000:
        return None

    # ASIN
    if not asin:
        asin_el = soup.select_one('input#ASIN, input[name="ASIN"]')
        asin = (asin_el.get("value") if asin_el else "").strip()
    if not asin or len(asin) != 10:
        # Try from URL in page
        m = re.search(r"/dp/([A-Z0-9]{10})", html)
        asin = m.group(1) if m else None
    if not asin:
        return None

    # Title
    title_el = soup.select_one("#productTitle, #title span")
    title = title_el.get_text(" ", strip=True) if title_el else None

    # Brand
    brand = None
    brand_el = soup.select_one("#bylineInfo, .po-brand .po-break-word")
    if brand_el:
        brand = brand_el.get_text(" ", strip=True)
        brand = re.sub(r"^(Brand:|Visit the|Store)\s*", "", brand, flags=re.I).strip()
    # Fallback: JSON-LD
    if not brand:
        m = re.search(r'"brand"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
        if m:
            brand = m.group(1)

    # Price
    price = None
    price_el = soup.select_one(
        "#corePriceDisplay_desktop_feature_div .a-offscreen, "
        "#priceblock_ourprice, #priceblock_dealprice, .a-price .a-offscreen")
    if price_el:
        price = _cents(price_el.get_text())
    # Fallback: JSON-LD
    if price is None:
        m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
        if m:
            price = _cents(m.group(1))

    # UPC/EAN/GTIN
    gtin = None
    for label in ("UPC", "EAN", "GTIN", "ISBN"):
        m = re.search(rf"{label}[:\s]*([0-9]{{8,14}})", html, re.I)
        if m:
            gtin = m.group(1)
            break
    # Also check detail bullets
    if not gtin:
        for row in soup.select("#detailBullets_feature_div li, .a-unordered-list li"):
            text = row.get_text(" ", strip=True)
            m = re.search(r"(UPC|EAN|GTIN|ISBN)[:\s]*([0-9]{8,14})", text, re.I)
            if m:
                gtin = m.group(2)
                break

    # MPN
    mpn = None
    m = re.search(r"(?:MPN|Model\s*Number|Part\s*Number)[:\s]*([A-Za-z0-9\-]{4,30})", html, re.I)
    if m:
        mpn = m.group(1).strip()

    # Seller
    seller = None
    seller_el = soup.select_one("#merchant-info, #soldByThirdParty, .tabular-buybox-text")
    if seller_el:
        seller = seller_el.get_text(" ", strip=True)[:120]
    # "Ships from Amazon" / "Sold by Amazon"
    ships_from_amazon = "ships from amazon" in low or "sold by amazon" in low

    # Stock status
    in_stock = True
    if re.search(r"currently unavailable|out of stock|temporarily out of stock", low):
        in_stock = False

    # Rating
    rating = None
    rating_el = soup.select_one("#acrPopover .a-icon-alt, span[data-hook='rating-out-of-text']")
    if rating_el:
        m = re.search(r"([\d.]+)\s*out of", rating_el.get_text())
        rating = float(m.group(1)) if m else None

    # Reviews count
    reviews = None
    reviews_el = soup.select_one("#acrCustomerReviewText")
    if reviews_el:
        m = re.search(r"([\d,]+)", reviews_el.get_text())
        reviews = int(m.group(1).replace(",", "")) if m else None

    # Image
    image = None
    img_el = soup.select_one("#landingImage, #imgBlkFront")
    if img_el:
        image = img_el.get("src") or img_el.get("data-old-hires")

    return {
        "platform": "amazon",
        "platform_id": asin,
        "url": f"https://www.amazon.com/dp/{asin}",
        "title": title,
        "brand": brand,
        "price_cents": price,
        "image_url": image,
        "rating": rating,
        "reviews": reviews,
        "gtin": gtin,
        "mpn": mpn,
        "seller": seller,
        "ships_from_amazon": ships_from_amazon,
        "in_stock": in_stock,
        "condition": "new",
    }


def is_blocked(html: str, status: int | None = None) -> str | None:
    if status in (403, 429, 503):
        return f"http_{status}"
    if len(html or "") < 5000:
        return f"short_body_{len(html or '')}"
    low = (html or "").lower()
    if "captcha" in low and "add to cart" not in low:
        return "captcha"
    if "to discuss automated access" in low:
        return "tos_block"
    return None
PY