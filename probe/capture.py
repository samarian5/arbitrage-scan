"""Capture full, real pages from the runner for parser development.

Writes gzipped HTML to probe/fixtures/ so the workflow can commit them back and
the parser can be written against real markup instead of guesses.
"""
from __future__ import annotations

import gzip
import json
import re
import time
from pathlib import Path

OUT = Path("probe/fixtures")
OUT.mkdir(parents=True, exist_ok=True)

BASE_H = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


def save(name: str, html: str) -> dict:
    p = OUT / f"{name}.html.gz"
    p.write_bytes(gzip.compress(html.encode("utf-8", "replace"), 6))
    return {"name": name, "bytes": len(html), "gz": p.stat().st_size,
            "asins": len(set(re.findall(r'data-asin="(B0[A-Z0-9]{8})"', html))),
            "cards": html.count('data-component-type="s-search-result"'),
            "captcha": bool(re.search(r"(?i)captcha|api-services-support", html))}


def main() -> int:
    from curl_cffi import requests as cr
    out = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pages": []}

    s = cr.Session(impersonate="chrome")
    s.headers.update(BASE_H)
    s.get("https://www.amazon.com/", timeout=45)          # the warm-up that unblocks /s
    time.sleep(2)

    kws = ["dewalt 20v drill", "anker power bank", "lego star wars"]
    first_asin = None
    for i, kw in enumerate(kws, 1):
        url = f"https://www.amazon.com/s?k={kw.replace(' ', '+')}"
        r = s.get(url, timeout=45, headers={"Referer": "https://www.amazon.com/"})
        out["pages"].append(save(f"amz_search_{i}", r.text))
        print(json.dumps(out["pages"][-1]))
        if i == 1:
            m = re.search(r'data-asin="(B0[A-Z0-9]{8})"', r.text)
            first_asin = m.group(1) if m else None
        time.sleep(4)

    # eBay through the browser lane is proven elsewhere; capture with the same warm trick first
    try:
        e = cr.Session(impersonate="chrome")
        e.headers.update(BASE_H)
        e.get("https://www.ebay.com/", timeout=40)
        time.sleep(2)
        r = e.get("https://www.ebay.com/sch/i.html?_nkw=dewalt+20v+drill&_ipg=60",
                  timeout=45, headers={"Referer": "https://www.ebay.com/"})
        out["pages"].append(save("ebay_search_1", r.text))
        print(json.dumps(out["pages"][-1]))
    except Exception as ex:  # noqa: BLE001
        out["ebay_error"] = str(ex)[:200]

    if first_asin:
        time.sleep(3)
        r = s.get(f"https://www.amazon.com/dp/{first_asin}", timeout=45,
                  headers={"Referer": "https://www.amazon.com/"})
        out["pages"].append(save("amz_dp_1", r.text))
        out["dp_asin"] = first_asin
        print(json.dumps(out["pages"][-1]))

    (OUT / "capture.json").write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
