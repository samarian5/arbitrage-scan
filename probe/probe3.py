"""Probe 3 — free egress options for Amazon, tested from a GitHub runner.

Every candidate is a *free* third-party fetch path that renders the page on
someone else's IP:
  jina        r.jina.ai reader (free tier, no key)
  translate   www-amazon-com.translate.goog (Google's proxy fetches it)
  allorigins  api.allorigins.win CORS relay
  corsproxy   corsproxy.io
  freeproxy   rotating public HTTP proxies against amazon.com directly
  wayback     archive.org snapshot (stale, but free)

Verdict per candidate = did we get a real product page (ASIN list / price /
title), not just a 200.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

OUT = Path("probe")
OUT.mkdir(exist_ok=True)

SEARCH = "https://www.amazon.com/s?k=dewalt+20v+drill"
DP = "https://www.amazon.com/dp/B00ET5VMTU"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def score(name: str, body: str, status, extra: dict | None = None) -> dict:
    d = {
        "name": name, "status": status, "bytes": len(body or ""),
        "asins": len(set(re.findall(r"\bB0[A-Z0-9]{8}\b", body or ""))),
        "prices": len(re.findall(r"\$\s?\d{1,3}(?:,\d{3})*\.\d{2}", body or "")),
        "title_hit": bool(re.search(r"(?i)dewalt", body or "")),
        "blocked_word": bool(re.search(r"(?i)captcha|not a robot|api-services-support|"
                                       r"something went wrong|to discuss automated access", body or "")),
    }
    if extra:
        d.update(extra)
    (OUT / f"p3_{re.sub(r'[^a-z0-9]+', '_', name.lower())}.txt").write_text((body or "")[:20000])
    print(json.dumps(d))
    return d


def try_get(url: str, timeout: int = 60, headers: dict | None = None, proxy: str | None = None):
    import requests
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    kw = {"timeout": timeout, "headers": h, "allow_redirects": True}
    if proxy:
        kw["proxies"] = {"http": proxy, "https": proxy}
    r = requests.get(url, **kw)
    return r.status_code, r.text, str(r.url)


def main() -> int:
    res = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "probes": []}

    def run(name, url, **kw):
        t0 = time.time()
        try:
            st, body, final = try_get(url, **kw)
            res["probes"].append(score(name, body, st, {"final_url": final[:160],
                                                        "s": round(time.time() - t0, 1)}))
        except Exception as e:  # noqa: BLE001
            res["probes"].append({"name": name, "error": f"{type(e).__name__}: {e}"[:200]})
            print(json.dumps(res["probes"][-1]))

    # 1. Jina reader (free, no key)
    run("jina_search", "https://r.jina.ai/" + SEARCH, timeout=90,
        headers={"X-Return-Format": "markdown"})
    time.sleep(5)
    run("jina_dp", "https://r.jina.ai/" + DP, timeout=90)

    # 2. Google translate proxy
    time.sleep(3)
    run("gt_search", "https://www-amazon-com.translate.goog/s?k=dewalt+20v+drill"
                     "&_x_tr_sl=auto&_x_tr_tl=es&_x_tr_hl=en")
    time.sleep(3)
    run("gt_dp", "https://www-amazon-com.translate.goog/dp/B00ET5VMTU"
                 "?_x_tr_sl=auto&_x_tr_tl=es&_x_tr_hl=en")

    # 3. CORS relays
    time.sleep(2)
    run("allorigins_dp", "https://api.allorigins.win/raw?url=" + urllib.parse.quote(DP, safe=""))
    time.sleep(2)
    run("corsproxy_dp", "https://corsproxy.io/?url=" + urllib.parse.quote(DP, safe=""))

    # 4. Wayback (free, stale)
    time.sleep(2)
    run("wayback_dp", "https://archive.org/wayback/available?url=" + urllib.parse.quote(DP, safe=""))

    # 5. Free proxy rotation against the real site
    import requests
    got = []
    for src in ("https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
                "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"):
        try:
            r = requests.get(src, timeout=30, headers={"User-Agent": UA})
            got += [ln.strip() for ln in r.text.splitlines() if ln.strip() and ":" in ln]
        except Exception:  # noqa: BLE001
            pass
    res["free_proxy_count"] = len(got)
    ok, tried = [], 0
    for p in got[:40]:
        tried += 1
        try:
            st, body, _ = try_get(DP, timeout=8, proxy=p)
            if st == 200 and len(body) > 50000 and "productTitle" in body:
                ok.append({"proxy": p, "status": st, "bytes": len(body)})
                res["probes"].append(score(f"freeproxy_ok_{tried}", body, st, {"proxy": p}))
                break
        except Exception:  # noqa: BLE001
            continue
    res["freeproxy_tried"] = tried
    res["freeproxy_ok"] = ok
    if not ok:
        res["probes"].append({"name": "freeproxy", "tried": tried, "result": "none worked"})

    (OUT / "result3.json").write_text(json.dumps(res, indent=2))
    print("\n=== VERDICT ===")
    for p in res["probes"]:
        print(f"{str(p.get('name')):22} st={p.get('status')} bytes={p.get('bytes')} "
              f"asins={p.get('asins')} prices={p.get('prices')} blocked={p.get('blocked_word')}")
    print("free proxies:", res["free_proxy_count"], "tried", tried, "ok", len(ok))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
