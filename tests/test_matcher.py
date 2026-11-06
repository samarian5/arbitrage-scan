"""Matcher tests: the cases that decide whether the board is trustworthy.

A scanner that pairs an accessory with a drill reports a 90% "margin" that does
not exist. Each hard gate below exists because of a specific real-world listing
pattern, so these tests assert *rejection*, not just acceptance.
"""
from crawler import matcher


def E(title, price=5000, brand=None, pid="1"):
    return {"title": title, "price_cents": price, "brand": brand, "platform_id": pid, "id": 1}


def A(title, price=4000, brand=None, pid="B0TESTTEST"):
    return {"title": title, "price_cents": price, "brand": brand, "platform_id": pid, "id": 2}


def test_exact_model_match_accepted():
    e = E("DEWALT DCD771C2 20V Max Cordless Drill Driver Kit", 11900, "dewalt")
    a = A("20V Max XR Drill Driver, Battery and Charger (DCD771C2)", 9900, "DEWALT")
    m = matcher.match_one(e, a)
    assert m, "same model number must match"
    assert m["method"] == "model"
    assert "dcd771c2" in m["evidence"]["matched_models"]


def test_accessory_for_product_rejected():
    """The #1 false positive: a battery FOR the drill vs the drill."""
    e = E("Replacement Battery for DEWALT DCD771C2 20V Max 3.0Ah", 4500, "dewalt")
    a = A("DEWALT 20V Max Cordless Drill Driver Kit DCD771C2", 11900, "DEWALT")
    assert matcher.match_one(e, a) is None


def test_compatible_with_rejected():
    e = E("Charger Compatible with Dewalt 20V DCD771C2", 2500)
    a = A("DEWALT DCD771C2 20V Max Drill Driver Kit", 11900, "DEWALT")
    assert matcher.match_one(e, a) is None


def test_lot_vs_single_rejected():
    e = E("Lot of 3 DEWALT DCD771C2 20V Drills", 30000, "dewalt")
    a = A("DEWALT DCD771C2 20V Max Drill Driver", 11900, "DEWALT")
    assert matcher.match_one(e, a) is None


def test_two_pack_vs_single_rejected():
    e = E("DEWALT DCB205 20V 5.0Ah Battery 2 Pack", 15000, "dewalt")
    a = A("DEWALT DCB205 20V MAX 5.0Ah Battery", 8900, "DEWALT")
    assert matcher.match_one(e, a) is None


def test_capacity_mismatch_rejected():
    e = E("DEWALT DCB205 20V MAX 5.0Ah Battery Pack", 8900, "dewalt")
    a = A("DEWALT DCB203 20V MAX 2.0Ah Battery Pack", 5900, "DEWALT")
    assert matcher.match_one(e, a) is None


def test_tool_only_vs_kit_rejected():
    e = E("DEWALT DCD801B 20V MAX XR Brushless Drill Tool Only", 9900, "dewalt")
    a = A("DEWALT 20V MAX XR Drill Driver Kit with 2 Batteries DCD801B", 17900, "DEWALT")
    assert matcher.match_one(e, a) is None


def test_different_brand_rejected():
    e = E("Makita XFD12Z 18V LXT Cordless Drill", 9900, "makita")
    a = A("DEWALT 20V MAX Cordless Drill DCD771C2", 11900, "DEWALT")
    assert matcher.match_one(e, a) is None


def test_amazon_model_absent_on_ebay_rejected():
    """A model on the Amazon side that eBay never mentions = different SKU."""
    e = E("Cordless Drill Driver 20V Max with Battery and Charger", 8900)
    a = A("DEWALT 20V MAX Cordless Drill DCD771C2 with Battery", 11900, "DEWALT")
    assert matcher.match_one(e, a) is None


def test_no_model_high_overlap_accepted_as_title():
    e = E("Anker 20000mAh Portable Charger Power Bank USB C", 4599, "anker")
    a = A("Anker Portable Charger 20000mAh Power Bank USB C", 3999, "Anker")
    m = matcher.match_one(e, a)
    assert m and m["method"] == "title"


def test_quantity_word_variants():
    assert matcher.quantity("2 pack of batteries") == 2
    assert matcher.quantity("batteries 3x") == 3
    assert matcher.quantity("single battery") == 1


def test_norm_strips_marketing_noise():
    assert matcher.norm("NEW SEALED® DEWALT™ 20V MAX") == "new sealed dewalt 20v max"
    toks = matcher.tokens("NEW SEALED DEWALT 20V DCD771C2 Drill")
    assert "sealed" not in toks and "dcd771c2" in toks and "20v" in toks


def test_models_extraction():
    assert matcher.models(["dcd771c2", "20v", "drill"]) == {"dcd771c2"}
    assert matcher.models(["b00et5vmtu"]) == {"b00et5vmtu"}
    assert matcher.models(["128gb"]) == set()


def test_pool_prefers_cheapest_equivalent_amazon_offer():
    e = E("DEWALT DCD771C2 20V Max Cordless Drill Driver Kit", 11900, "dewalt")
    a1 = A("DEWALT 20V Max Drill Driver Kit DCD771C2", 9900, "DEWALT", "B0AAAAAAAA")
    a2 = A("DEWALT 20V Max Drill Driver Kit DCD771C2", 8900, "DEWALT", "B0BBBBBBBB")
    ms = matcher.match_pool([e], [a1, a2])
    assert len(ms) == 1
    assert ms[0]["amazon_platform_id"] == "B0BBBBBBBB"
