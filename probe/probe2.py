"""Probe v2 — find the lane that actually returns product HTML from a runner.

eBay: cold vs warmed session (the production fetcher warms cookies on the
homepage first). Amazon: five variants, because a 200 with a 2 KB body is a soft
block and tells us nothing about which header set matters.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

OUT = Path("probe")
OUT.mkdir(exist_ok=True)

BASE_H = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

AMZ_SEARCH = "https://www.amazon.com/s?k=dewalt+20v+drill"
AMZ_MOBILE = "https://www.amazon.com/gp/aw/s?k=dewalt+20v+drill"
EBAY_SEARCH = "https://www.ebay.com/sch/i.html?_nkw=dewalt+20v+drill&_ipg=60"


def rec(name, r, extra=None):
    body = r.text if r is not None else ""
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    d = {"name": name, "status": getattr(r, "status_code", None), "bytes": len(body),
         "final_url": str(getattr(r, "url", "")),
         "title": (m.group(1).strip()[:100] if m else None),
         "markers": {
             "data-asin": body.count('data-asin="'),
             "s-card": body.count("s-card"),
             "captcha": len(re.findall(r"(?i)captcha|not a robot|api-services-support", body)),
             "price_offscreen": body.count("a-offscreen"),
             "productTitle": body.count('id="productTitle"'),
         }}
    if extra:
        d.update(extra)
    (OUT / f"{re.sub(r'[^a-z0-9]+', '_', name.lower())}.txt").write_text(body[:30000])
    print(json.dumps(d))
    return d


def main() -> int:
    from curl_cffi import requests as cr

    results = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "probes": []}

    # ---- eBay: cold then warmed -------------------------------------------
    s = cr.Session(impersonate="chrome")
    s.headers.update(BASE_H)
    r = s.get(EBAY_SEARCH, timeout=45)
    results["probes"].append(rec("ebay_cold", r))

    s2 = cr.Session(impersonate="chrome")
    s2.headers.update(BASE_H)
    try:
        home = s2.get("https://www.ebay.com/", timeout=45)
        results["probes"].append(rec("ebay_home", home))
    except Exception as e:  # noqa: BLE001
        results["probes"].append({"name": "ebay_home", "error": str(e)[:200]})
    time.sleep(2)
    r = s2.get(EBAY_SEARCH, timeout=45, headers={"Referer": "https://www.ebay.com/"})
    results["probes"].append(rec("ebay_warm_search", r))

    # ---- Amazon variants ---------------------------------------------------
    variants = [
        ("amz_cold_chrome", "chrome", AMZ_SEARCH, None),
        ("amz_cold_safari", "safari", AMZ_SEARCH, None),
        ("amz_cold_edge", "edge101", AMZ_SEARCH, None),
        ("amz_warm_chrome", "chrome", AMZ_SEARCH, "https://www.amazon.com/"),
        ("amz_mobile_warm", "chrome", AMZ_MOBILE, "https://www.amazon.com/"),
    ]
    for name, imp, url, warm in variants:
        try:
            sess = cr.Session(impersonate=imp)
            sess.headers.update(BASE_H)
            if warm:
                try:
                    sess.get(warm, timeout=30)
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(2)
            r = sess.get(url, timeout=45, headers={"Referer": warm} if warm else None)
            results["probes"].append(rec(name, r, {"impersonate": imp}))
        except Exception as e:  # noqa: BLE001
            results["probes"].append({"name": name, "error": str(e)[:200]})
        time.sleep(4)

    (OUT / "result2.json").write_text(json.dumps(results, indent=2))
    print("\n=== VERDICT ===")
    for p in results["probes"]:
        print(f"{p.get('name'):20} {p.get('status')} {p.get('bytes')}B "
              f"asin={p.get('markers', {}).get('data-asin')} cards={p.get('markers', {}).get('s-card')} "
              f"cap={p.get('markers', {}).get('captcha')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
