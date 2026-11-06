"""Vantage-point probe: what can a GitHub-hosted runner actually fetch?

Runs the same request from the runner (Azure, usually San Jose) that the real
crawler will make, and writes a JSON verdict plus the first bytes of each
response so a block page is distinguishable from a real page.

Writes probe/result.json and probe/*.html snippets (capped) for the workflow to
commit back, because the log of a run is not always readable through the proxy.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

OUT = Path("probe")
OUT.mkdir(exist_ok=True)
H = {"Accept-Language": "en-US,en;q=0.9"}

TARGETS = {
    "amazon_search": "https://www.amazon.com/s?k=dewalt+20v+drill",
    "amazon_search_mobile": "https://www.amazon.com/s?k=dewalt+20v+drill",
    "amazon_dp": "https://www.amazon.com/dp/B00ET5VMTU",
    "ebay_search": "https://www.ebay.com/sch/i.html?_nkw=dewalt+20v+drill&_ipg=60",
    "ebay_itm": "https://www.ebay.com/itm/285690000000",
}

MARKERS = {
    "amazon_search": [("data-asin=", r'data-asin="'), ("s-result-item", r"s-result-item"),
                      ("captcha", r"(?i)captcha"), ("robot", r"(?i)not a robot")],
    "amazon_dp": [("productTitle", r'id="productTitle"'), ("priceblock", r"(?i)priceblock|a-offscreen"),
                  ("captcha", r"(?i)captcha")],
    "ebay_search": [("s-card", r"s-card"), ("s-item__title", r"s-item__title"),
                    ("signin", r"(?i)signin\.ebay")],
    "ebay_itm": [("binBtn", r"(?i)binBtn|Buy It Now"), ("sold", r"(?i)\d+\s*sold")],
}


def ip_info() -> dict:
    import urllib.request
    try:
        with urllib.request.urlopen("https://ipinfo.io/json", timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


def probe(name: str, url: str, mobile: bool = False) -> dict:
    from curl_cffi import requests as cr
    rec = {"name": name, "url": url, "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    hdrs = dict(H)
    if mobile:
        hdrs["User-Agent"] = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
    t0 = time.time()
    try:
        r = cr.get(url, impersonate="chrome", headers=hdrs, timeout=45, allow_redirects=True)
        body = r.text
        rec.update(status=r.status_code, bytes=len(body), seconds=round(time.time() - t0, 2),
                   final_url=str(r.url))
        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
        rec["title"] = (m.group(1).strip()[:120] if m else None)
        rec["markers"] = {k: len(re.findall(pat, body)) for k, pat in MARKERS.get(name, [])}
        (OUT / f"{name}.txt").write_text(body[:20000])
    except Exception as e:  # noqa: BLE001
        rec.update(status=None, error=f"{type(e).__name__}: {e}"[:300],
                   seconds=round(time.time() - t0, 2))
    print(json.dumps(rec))
    return rec


def main() -> int:
    results = {"ip": ip_info(), "probes": []}
    results["probes"].append(probe("ebay_search", TARGETS["ebay_search"]))
    time.sleep(3)
    results["probes"].append(probe("amazon_search", TARGETS["amazon_search"]))
    time.sleep(3)
    results["probes"].append(probe("amazon_search_mobile", TARGETS["amazon_search_mobile"], mobile=True))
    time.sleep(3)
    results["probes"].append(probe("amazon_dp", TARGETS["amazon_dp"]))
    time.sleep(3)
    results["probes"].append(probe("ebay_itm", TARGETS["ebay_itm"]))
    (OUT / "result.json").write_text(json.dumps(results, indent=2))
    print("\n=== VERDICT ===")
    for p in results["probes"]:
        print(f"{p['name']:22} {p.get('status')} {p.get('bytes')}B title={str(p.get('title'))[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
