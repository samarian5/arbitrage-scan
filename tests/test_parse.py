"""Parser tests against REAL captures, asserting real values.

Synthetic fixtures prove the parser runs; only a capture proves it reads the
site. If Amazon or eBay changes markup, these break loudly instead of the
dashboard silently going empty.
"""
import gzip
from pathlib import Path

from crawler import parse_amazon, parse_ebay

REAL = Path(__file__).parent / "fixtures" / "real"
AMZ = Path(__file__).parent.parent / "probe" / "amz_search_1.html"


def _amz_html() -> str:
    if AMZ.exists():
        return AMZ.read_text(encoding="utf-8", errors="replace")
    z = REAL / "amz_search_1.html.gz"
    return gzip.decompress(z.read_bytes()).decode("utf-8", "replace")


def test_amazon_capture_parses():
    rows = parse_amazon.parse_search_html(_amz_html())
    assert len(rows) >= 40, f"expected a full page of cards, got {len(rows)}"
    priced = [r for r in rows if r["price_cents"]]
    assert len(priced) / len(rows) > 0.9, "most cards must carry a price"
    first = rows[0]
    assert first["platform_id"] == "B00ET5VMTU"
    assert first["price_cents"] == 11900
    assert first["brand"].upper() == "DEWALT"
    assert "DCD771C2" in first["title"]
    assert first["url"].endswith("/dp/B00ET5VMTU")


def test_amazon_block_detection():
    assert parse_amazon.is_blocked("<html>short</html>", 200).startswith("short_body_")
    assert parse_amazon.is_blocked("x" * 30000, 503) == "http_503"
    assert parse_amazon.is_blocked(_amz_html(), 200) is None


def test_ebay_capture_parses():
    html = gzip.decompress((REAL / "search_active_2026-11-06.html.gz").read_bytes()).decode(
        "utf-8", "replace")
    rows = parse_ebay.parse_search_html(html)
    assert len(rows) > 40, f"got {len(rows)}"
    priced = [r for r in rows if r["price_cents"]]
    assert len(priced) / len(rows) > 0.8
    assert all(r["platform_id"].isdigit() for r in rows)
    assert "123456" not in {r["platform_id"] for r in rows}, "ad card must be dropped"
    assert parse_ebay.result_count(html), "result count should be present"


def test_ebay_block_detection():
    assert parse_ebay.is_blocked("<html>tiny</html>", 200).startswith("short_body_")
    assert parse_ebay.is_blocked("x" * 6000, 403) == "http_403"
