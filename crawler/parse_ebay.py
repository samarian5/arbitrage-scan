"""Parse eBay search pages (Active Surface).

Selector semantics verified against a real capture (search_active_2026-11-06):

  li.s-card                  one card; data-listingid is the item number
  .s-card__title             title (+ "Opens in a new window or tab")
  .s-card__attribute-row     price, "Buy It Now", "Free delivery", "63+ sold"
  .s-card__subtitle-row      "Brand New", "Size M"

The `Shop on eBay` ad card (item 123456, $20.00) is dropped before counting.
The sold badge is a floor ("63+ sold"), never an exact figure.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

ITEM_RE = re.compile(r"/itm/(\d{6,16})")
DECORATION_RE = re.compile(r"\s*Opens in a new window or tab\s*$", re.I)
MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
SOLD_RE = re.compile(r"([\d.,]+)\s*\+?\s*sold\b", re.I)
CONDITIONS = ("brand new", "new (other)", "new other", "open box", "used", "refurbished")
SPEC_PAIR_RE = re.compile(r"([A-Za-z][A-Za-z0-9 /]{1,22}?):\s*([A-Za-z0-9./\- ]{1,30})")


def _money(text: str | None) -> int | None:
    if not text:
        return None
    m = MONEY_RE.search(text.replace(",", ""))
    return int(round(float(m.group(1)) * 100)) if m else None


def parse_card(card) -> dict[str, Any] | None:
    title_el = card.select_one(".s-card__title")
    title = DECORATION_RE.sub("", title_el.get_text(" ", strip=True)) if title_el else ""
    if not title or title.strip().lower() == "shop on ebay":
        return None

    item_id = (card.get("data-listingid") or "").strip()
    if not item_id:
        link = card.select_one('a[href*="/itm/"]')
        m = ITEM_RE.search(link.get("href") or "") if link else None
        item_id = m.group(1) if m else ""
    if not item_id or item_id == "123456":
        return None

    rows = [r.get_text(" ", strip=True) for r in
            card.select(".s-card__attribute-row, .s-card__subtitle-row")]
    price_row = next((r for r in rows if "$" in r), "")
    prices = [int(round(float(p.replace(",", "")) * 100)) for p in MONEY_RE.findall(price_row)]
    price = min(prices) if prices else None

    condition = next((r for r in rows if r.lower().startswith(CONDITIONS)), None)
    delivery = next((r for r in rows if re.search(r"delivery|shipping", r, re.I)), None)
    sold = SOLD_RE.search(" | ".join(rows))

    img = card.select_one("img.s-card__image") or card.select_one("img")
    image = ""
    if img:
        image = (img.get("data-defer-load") or img.get("src") or "").replace("/s-l140.", "/s-l500.")

    # eBay prints item specifics only on the item page, but some cards carry a
    # subtitle row with Brand/Model — harvest whatever is there.
    specs = {}
    for row in rows:
        for k, v in SPEC_PAIR_RE.findall(row):
            k = k.strip().lower()
            if k in ("brand", "model", "mpn", "upc", "ean", "type"):
                specs[k] = v.strip()

    return {
        "platform": "ebay",
        "platform_id": item_id,
        "url": f"https://www.ebay.com/itm/{item_id}",
        "title": title,
        "brand": specs.get("brand"),
        "mpn": specs.get("mpn") or specs.get("model"),
        "gtin": specs.get("upc") or specs.get("ean"),
        "price_cents": price,
        "image_url": image,
        "condition": condition,
        "shipping_cents": 0 if (delivery and re.search(r"free", delivery, re.I)) else None,
        "free_shipping": bool(delivery and re.search(r"free", delivery, re.I)),
        "units_badge": int(sold.group(1).replace(",", "")) if sold else None,
        "is_sponsored": bool(card.select_one(".s-card__ad-badge, [data-ad-label]")),
    }


def parse_search_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pos, card in enumerate(soup.select("li.s-card, li.s-item"), start=1):
        row = parse_card(card)
        if not row or row["platform_id"] in seen:
            continue
        row["position"] = pos
        seen.add(row["platform_id"])
        out.append(row)
    return out


def result_count(html: str) -> str | None:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = re.search(r"([\d,]{3,})\s*\+?\s*results?\s+for\b", text, re.I)
    return m.group(1) if m else None


def is_blocked(html: str, status: int | None = None) -> str | None:
    if status in (403, 429, 503):
        return f"http_{status}"
    low = (html or "").lower()
    if len(html or "") < 5000:
        return f"short_body_{len(html or '')}"
    if "pardon our interruption" in low:
        return "interstitial"
    if "splashui/challenge" in low or "/splashui/captcha" in low:
        return "challenge"
    if "signin.ebay.com" in low and "sign in or register" in low:
        return "signin"
    if 'class="s-card' not in html and "<title>error page" in low:
        return "error_page"
    return None
