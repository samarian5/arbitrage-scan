"""Parse Amazon search pages.

Markup verified against a real 1.7 MB capture (probe/fixtures/amz_search_1.html.gz,
2026-11-06, 48 cards, 0 captcha):

  div[data-component-type="s-search-result"]      one card, data-asin = ASIN
  [data-cy="title-recipe"] h2 span                brand line ("DEWALT")
  [data-cy="title-recipe"] a h2                   product title (aria-label holds it)
  span.a-price > span.a-offscreen                 price ("$119.00")
  img.s-image                                     thumbnail
  .puis-sponsored-label-text                      sponsored

Cards without a price (".a-price" missing) are kept with price=None: a listing we
cannot price is not a listing we can arbitrage, but dropping it silently would
hide a parser break.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

BAD_TITLE = re.compile(r"(?i)^(sponsored|results|related|need help\?|see more)")
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


def parse_card(card) -> dict[str, Any] | None:
    asin = (card.get("data-asin") or "").strip()
    if not asin or len(asin) != 10:
        return None

    recipe = card.select_one('[data-cy="title-recipe"]')
    brand = None
    title = None
    if recipe:
        b = recipe.select_one("h2 span")
        if b:
            brand = b.get_text(" ", strip=True)[:80]
        a = recipe.select_one('a[href*="/dp/"] h2')
        if a:
            title = (a.get("aria-label") or a.get_text(" ", strip=True) or "").strip()
        if not title:
            h2s = recipe.select("h2")
            if len(h2s) > 1:
                title = h2s[1].get_text(" ", strip=True)
    if not title:
        h2 = card.select_one("h2")
        title = h2.get_text(" ", strip=True)[:200] if h2 else None
    if not title or BAD_TITLE.match(title):
        return None

    price_el = card.select_one("span.a-price:not(.a-text-price) span.a-offscreen")
    if price_el is None:
        price_el = card.select_one("span.a-price span.a-offscreen")
    price = _cents(price_el.get_text() if price_el else None)

    # "a-text-price" carries the struck-through list price; only use it as a
    # fallback when there is no current price at all.
    if price is None:
        alt = card.select_one("span.a-text-price span.a-offscreen")
        price = _cents(alt.get_text() if alt else None)

    img = card.select_one("img.s-image")
    image = (img.get("src") if img else None) or ""

    rating_el = card.select_one("span.a-icon-alt")
    reviews_el = card.select_one("span.a-size-base.s-underline-text")
    sponsored = bool(card.select_one(".puis-sponsored-label-text, [aria-label='Sponsored']"))

    return {
        "platform": "amazon",
        "platform_id": asin,
        "url": f"https://www.amazon.com/dp/{asin}",
        "title": title,
        "brand": brand,
        "price_cents": price,
        "image_url": image,
        "rating": rating_el.get_text(strip=True) if rating_el else None,
        "reviews": reviews_el.get_text(strip=True) if reviews_el else None,
        "is_sponsored": sponsored,
        "condition": "new",
        "shipping_cents": 0,
    }


def parse_search_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pos, card in enumerate(soup.select('div[data-component-type="s-search-result"]'), start=1):
        row = parse_card(card)
        if not row or row["platform_id"] in seen:
            continue
        row["position"] = pos
        seen.add(row["platform_id"])
        out.append(row)
    return out


def is_blocked(html: str, status: int | None = None) -> str | None:
    """A 200 with a 2 KB body is Amazon's soft block; name it explicitly."""
    if status in (403, 429, 503):
        return f"http_{status}"
    body = html or ""
    if len(body) < 20000:
        return f"short_body_{len(body)}"
    low = body[:60000].lower()
    if "api-services-support@amazon.com" in low or "to discuss automated access" in low:
        return "tos_block"
    if "captcha" in low and 'data-component-type="s-search-result"' not in body:
        return "captcha"
    if "something went wrong" in low and len(body) < 40000:
        return "error_page"
    return None
