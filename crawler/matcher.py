"""Cross-platform product matching: eBay listing <-> Amazon listing.

The expensive mistakes in arbitrage scanning are not parser bugs, they are
*identity* bugs:

  1. the accessory trap  — eBay sells "Replacement Battery for DEWALT DCD771C2"
     against Amazon's "DEWALT DCD771C2 Drill Kit". Same keywords, different product.
  2. the variant trap    — 2-pack vs single, 128 GB vs 256 GB, 20V vs 12V.
  3. the lot trap        — "Lot of 3" against a single unit.

So matching is gated, not just scored: a hard rejection cannot be outvoted by a
high token overlap. Everything that decided a match is returned as evidence, so
the dashboard can show *why* two listings were paired.
"""
from __future__ import annotations

import re
import unicodedata

# Words that carry no identity. Deliberately does NOT include size/capacity
# words (20v, 5ah, 128gb) — those are exactly what distinguishes two variants.
STOP = {
    "the", "a", "an", "and", "or", "of", "for", "with", "w", "without", "in", "on",
    "new", "sealed", "genuine", "authentic", "oem", "original", "official",
    "fast", "free", "shipping", "ship", "shipped", "delivery", "us", "usa",
    "seller", "sale", "deal", "hot", "best", "top", "quality", "premium",
    "piece", "set", "kit", "pack", "pcs", "pc", "inch", "in.", "cm", "mm",
    "black", "white", "blue", "red", "silver", "grey", "gray",  # colour rarely identifies SKU
    "includes", "include", "including", "plus", "item", "product", "brand",
    "amazon", "ebay", "com", "www", "not", "no", "by", "from",
}

ACCESSORY_RE = re.compile(
    r"(?i)\b(for|fits|fit|compatible\s+with|replacement\s+for|replaces|"
    r"suitable\s+for|works\s+with|designed\s+for|universal)\b")
LOT_RE = re.compile(r"(?i)\b(lot\s+of|bundle|multi[- ]?pack|\d+\s*[x×]\s*\d+|\d+\s*pack\b)")
MULTI_RE = re.compile(r"(?i)\b(?:pack\s+of\s+(\d+)|(\d+)\s*pack|(\d+)\s*[x×]\s*)")
TOOL_ONLY_RE = re.compile(r"(?i)\(?\s*tool\s+only\s*\)?|\bbare\s+tool\b")
MODEL_RE = re.compile(r"^[a-z]{1,6}\d{2,}[a-z0-9]*$|^\d{2,}[a-z]{1,6}\d*$")

SPLIT_RE = re.compile(r"[^a-z0-9/.\-]+")


def norm(text: str | None) -> str:
    if not text:
        return ""
    t = text
    for mark in ("®", "™", "©", "℠"):
        t = t.replace(mark, "")
    t = unicodedata.normalize("NFKD", t)
    t = t.encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"(\d)\s?(gb|tb|mah|ah|ml|oz|lb|kg|w|v)\b", r"\1\2", t)
    t = re.sub(r"(\d)\s*-\s*(pack|pcs|piece)", r"\1pack", t)
    return re.sub(r"\s+", " ", t).strip()


def tokens(text: str | None, drop: set[str] | None = None) -> list[str]:
    t = norm(text)
    raw = [x.strip(".-/") for x in SPLIT_RE.split(t) if x.strip(".-/")]
    out = []
    for tok in raw:
        if tok in STOP or (drop and tok in drop):
            continue
        if len(tok) == 1 and not tok.isdigit():
            continue
        out.append(tok)
    return out


CAPACITY_SUFFIX = ("gb", "tb", "mah", "ah", "ml", "oz", "lb", "kg", "mm", "cm",
                   "v", "w", "in", "ft", "hz", "rpm")


def models(toks: list[str]) -> set[str]:
    """Model-bearing tokens: mix letters and digits, long enough to be a SKU.

    'dcd771c2' yes, '20v' no (too short), '128gb' no (a capacity, not a model).
    """
    out = set()
    for t in toks:
        if len(t) < 5 or not any(c.isdigit() for c in t) or not any(c.isalpha() for c in t):
            continue
        if t.endswith(CAPACITY_SUFFIX):
            continue
        if MODEL_RE.match(t):
            out.add(t)
    return out


def capacities(toks: list[str]) -> set[str]:
    """Size/capacity tokens that must not contradict across a match."""
    return {t for t in toks if re.search(r"\d", t) and len(t) <= 6
            and re.search(r"(gb|tb|mah|ah|ml|oz|v|w|mm|cm|in)$", t)}


def quantity(text: str) -> int:
    m = MULTI_RE.search(text or "")
    if not m:
        return 1
    for g in m.groups():
        if g and g.isdigit():
            return int(g)
    return 1


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_one(ebay: dict, amazon: dict, threshold: float = 0.72) -> dict | None:
    """Return a match record, or None. Hard rejections happen before scoring."""
    et, at = ebay.get("title") or "", amazon.get("title") or ""
    e_brand = (ebay.get("brand") or "").strip().lower()
    a_brand = (amazon.get("brand") or "").strip().lower()

    e_tok = set(tokens(et))
    a_tok = set(tokens(at))
    if not e_tok or not a_tok:
        return None

    # --- hard gates ------------------------------------------------------
    if e_brand and a_brand and e_brand != a_brand and e_brand not in at.lower() \
            and a_brand not in et.lower():
        return None

    # accessory: ebay sells a part FOR the product, amazon sells the product
    if ACCESSORY_RE.search(et) and not ACCESSORY_RE.search(at):
        return None

    # tool-only vs kit is a different SKU and a very different price
    if TOOL_ONLY_RE.search(at) != TOOL_ONLY_RE.search(et):
        return None

    # quantity: "2 pack" on one side only
    if quantity(et) != quantity(at):
        return None
    if bool(LOT_RE.search(et)) != bool(LOT_RE.search(at)):
        return None

    # capacities must not contradict (5ah vs 2ah)
    e_cap, a_cap = capacities(e_tok), capacities(a_tok)
    if e_cap and a_cap and not (e_cap & a_cap):
        return None

    # --- score -----------------------------------------------------------
    e_models, a_models = models(e_tok), models(a_tok)
    model_hit = bool(e_models & a_models)
    score_vec = jaccard(e_tok, a_tok)
    brand_hit = bool(e_brand and (e_brand == a_brand or e_brand in at.lower()
                                  or a_brand in et.lower()))

    score = 0.75 * score_vec + (0.35 if model_hit else 0.0) + (0.10 if brand_hit else 0.0)
    score = min(score, 1.0)

    # a model on the Amazon side that eBay never mentions means a different SKU
    if a_models and not model_hit and score_vec < 0.9:
        return None

    method = "model" if model_hit else "title"
    if not model_hit and score_vec < 0.62:
        return None
    if score < threshold:
        return None

    confidence = "high" if model_hit else ("medium" if score_vec >= 0.85 else "low")
    return {
        "ebay_id": ebay.get("id"),
        "amazon_id": amazon.get("id"),
        "ebay_platform_id": ebay.get("platform_id"),
        "amazon_platform_id": amazon.get("platform_id"),
        "method": method,
        "score": round(score, 3),
        "confidence": confidence,
        "evidence": {
            "matched_models": sorted(e_models & a_models),
            "jaccard": round(score_vec, 3),
            "brand": e_brand or a_brand or None,
            "shared_tokens": sorted(e_tok & a_tok)[:12],
        },
    }


def match_pool(ebay_rows: list[dict], amazon_rows: list[dict],
               threshold: float = 0.72) -> list[dict]:
    """Match every eBay row against the Amazon pool from the SAME keyword.

    Both sides came back for one search phrase, so the comparison is already
    domain-limited; matching globally is both slower and much less accurate.
    """
    out = []
    for e in ebay_rows:
        if not e.get("id") or not e.get("price_cents"):
            continue
        best = None
        for a in amazon_rows:
            if not a.get("id") or not a.get("price_cents"):
                continue
            m = match_one(e, a, threshold)
            if not m:
                continue
            # prefer the cheapest Amazon offer among equally good matches
            key = (m["score"], -(a.get("price_cents") or 0))
            if best is None or key > (best[0]["score"], -(best[1].get("price_cents") or 0)):
                best = (m, a)
        if best:
            out.append(best[0])
    return out
