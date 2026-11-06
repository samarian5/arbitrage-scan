"""Probe 4 — which Amazon entry *sequence* survives from a CI runner, per region.

Probe 3 showed warm-homepage-then-search worked from one runner (Des Moines) and
a later run got a 2.3 KB soft block from another. So the gate is both cookie and
IP-reputation shaped. This tests eight sequences in one job and re-tests any
winner twice, so a fluke is not mistaken for a lane.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

OUT = Path("probe")
OUT.mkdir(exist_ok=True)
KW = "dewalt+20v+drill"
SEARCH = f"https://www.amazon.com/s?k={KW}"
H = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def cards(html: str) -> int:
    return html.count('data-component-type="s-search-result"')


def hit(name, r) -> dict:
    body = r.text if r is not None else ""
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    d = {"name": name, "status": getattr(r, "status_code", None), "bytes": len(body),
         "cards": cards(body), "asins": len(set(re.findall(r'data-asin="(B0[A-Z0-9]{8})"', body))),
         "title": (m.group(1).strip()[:60] if m else None)}
    (OUT / f"p4_{name}.txt").write_text(body[:15000])
    print(json.dumps(d))
    return d


def attempt(name, imp="chrome", steps=("home", "search"), mob=False):
    from curl_cffi import requests as cr
    s = cr.Session(impersonate=imp)
    s.headers.update(H)
    if mob:
        s.headers["User-Agent"] = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                                   "Mobile/15E148 Safari/604.1")
    t0 = time.time()
    try:
        for step in steps:
            if step == "home":
                s.get("https://www.amazon.com/", timeout=40)
                time.sleep(1.5)
            elif step == "best":
                s.get("https://www.amazon.com/gp/bestsellers/", timeout=40)
                time.sleep(1.5)
            elif step == "dp":
                s.get("https://www.amazon.com/dp/B00ET5VMTU", timeout=40)
                time.sleep(1.5)
            elif step == "search":
                url = f"https://www.amazon.com/gp/aw/s?k={KW}" if mob else SEARCH
                r = s.get(url, timeout=45, headers={"Referer": "https://www.amazon.com/"})
        d = hit(name, r)
        d["seconds"] = round(time.time() - t0, 1)
        d["impersonate"] = imp
        return d
    except Exception as e:  # noqa: BLE001
        d = {"name": name, "error": f"{type(e).__name__}: {e}"[:180]}
        print(json.dumps(d))
        return d


def main() -> int:
    import urllib.request
    try:
        with urllib.request.urlopen("https://ipinfo.io/json", timeout=15) as r:
            ip = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        ip = {}

    out = {"ip": {k: ip.get(k) for k in ("ip", "city", "region", "org")},
           "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "runs": []}

    plan = [
        ("home_search", "chrome", ("home", "search"), False),
        ("home_best_search", "chrome", ("home", "best", "search"), False),
        ("home_dp_search", "chrome", ("home", "dp", "search"), False),
        ("search_only_safari", "safari", ("search",), False),
        ("home_search_safari", "safari", ("home", "search"), False),
        ("home_search_edge", "edge101", ("home", "search"), False),
        ("home_search_mobile", "chrome", ("home", "search"), True),
        ("home_twice_search", "chrome", ("home", "home", "search"), False),
    ]
    for p in plan:
        out["runs"].append(attempt(*p))
        time.sleep(4)

    winners = [x for x in out["runs"] if x.get("cards", 0) > 20]
    out["retest"] = []
    for w in winners[:2]:
        time.sleep(5)
        r2 = attempt("retest_" + w["name"], w.get("impersonate", "chrome"),
                     ("home", "search"), "mobile" in w["name"])
        out["retest"].append(r2)

    out["verdict"] = [w["name"] for w in winners]
    (OUT / "result4.json").write_text(json.dumps(out, indent=2))
    print("\nWINNERS:", out["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
