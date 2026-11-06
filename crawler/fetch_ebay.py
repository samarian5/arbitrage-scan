"""eBay fetcher: cheap HTTP lane, browser lane on block.

Measured 2026-11-06:
  curl_cffi from a Des Moines Azure runner -> 403 on every eBay page (cold or warm)
  SeleniumBase CDP browser from the same runner -> real results after solving
  SplashUI (~17 s/page, 208 listings over 4 pages in 67 s)

So the browser is not a fallback of last resort here, it is the normal lane on
CI, and the HTTP lane is the lucky path. Both are free; no proxy is used.
"""
from __future__ import annotations

import os
import random
import time

HOME = "https://www.ebay.com/"
SEARCH = ("https://www.ebay.com/sch/i.html?_nkw={q}&_ipg=60&_pgn={page}"
          "&LH_BIN=1&LH_ItemCondition=1000")
UA_LANG = "en-US,en;q=0.9"


class EbayFetcher:
    def __init__(self, profile_dir: str = ".ebay-profile", allow_browser: bool = True,
                 save_html: int = 0):
        self.profile_dir = profile_dir
        self.allow_browser = allow_browser
        self.save_html = save_html
        self.session = None
        self.browser = None
        self.challenges = 0
        self.tier_used: list[str] = []
        self.saved = 0

    # ---- lane 1: HTTP ----------------------------------------------------
    def _session(self):
        if self.session is None:
            from curl_cffi import requests as creq
            self.session = creq.Session(impersonate="chrome")
            self.session.headers.update({
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                          "image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": UA_LANG,
                "Upgrade-Insecure-Requests": "1",
            })
            try:
                self.session.get(HOME, timeout=30)   # cookie warm-up; 403 here is normal
            except Exception:  # noqa: BLE001
                pass
        return self.session

    def http_get(self, url: str):
        r = self._session().get(url, timeout=45, allow_redirects=True,
                                headers={"Referer": HOME})
        return r.status_code, r.text

    # ---- lane 2: browser -------------------------------------------------
    def _browser(self):
        if self.browser is None:
            from seleniumbase import sb_cdp
            kwargs = dict(user_data_dir=self.profile_dir, incognito=False,
                          headless=False, ad_block=True, lang="en-US",
                          tzone="America/New_York", sandbox=bool(os.environ.get("CI")))
            if os.environ.get("BROWSER_BIN"):
                kwargs["browser_executable_path"] = os.environ["BROWSER_BIN"]
            self.browser = sb_cdp.Chrome(**kwargs)
        return self.browser

    def browser_get(self, url: str) -> str:
        from .parse_ebay import is_blocked
        b = self._browser()
        # Same finding as the Amazon lane: the search URL is judged harder than
        # the homepage. One homepage visit plants the cookie set that makes the
        # following /sch/ request look like a browse session rather than a hit.
        if not getattr(self, "_warmed", False):
            try:
                b.goto("https://www.ebay.com/", timeout=60)
                time.sleep(2.0)
            except Exception:  # noqa: BLE001
                pass
            self._warmed = True
        b.goto(url, timeout=90)
        html = ""
        # SplashUI often clears itself once its JS has run, so patience beats
        # escalation: reload, wait, and look for real cards rather than giving up
        # on the first challenge-looking body.
        for attempt in range(6):
            time.sleep(3 if attempt < 3 else 5)
            html = b.evaluate("document.documentElement.outerHTML") or ""
            if not is_blocked(html):
                return html
            if attempt in (1, 3):
                try:
                    b.goto(url, timeout=90)
                except Exception:  # noqa: BLE001
                    pass
        return html

    def browser_restart(self) -> None:
        """Drop the profile in memory and start a clean browser (new fingerprint)."""
        try:
            if self.browser is not None:
                self.browser.quit()
        except Exception:  # noqa: BLE001
            pass
        self.browser = None
        self._warmed = False

    # ---- public ----------------------------------------------------------
    def search_page(self, keyword: str, page: int) -> tuple[str, str]:
        from .parse_ebay import is_blocked
        url = SEARCH.format(q=keyword.replace(" ", "+"), page=page)
        reason = None
        try:
            status, html = self.http_get(url)
            reason = is_blocked(html, status)
        except Exception as e:  # noqa: BLE001
            html, reason = "", f"exception:{type(e).__name__}"

        if not reason:
            self.tier_used.append("http")
            self._maybe_save(keyword, page, html)
            return html, "http"

        if not self.allow_browser:
            raise RuntimeError(f"ebay blocked on page {page}: {reason}")

        self.challenges += 1
        print(f"[ebay] page {page} blocked ({reason}) -> browser lane", flush=True)
        html = self.browser_get(url)
        still = is_blocked(html)
        if still:
            # a block is session-bound, not page-bound: a cold browser sometimes
            # clears it when reloading the same profile cannot
            print(f"[ebay] page {page} still blocked ({still}) -> cold browser retry", flush=True)
            self.browser_restart()
            html = self.browser_get(url)
            still = is_blocked(html)
        if still:
            raise RuntimeError(f"ebay browser lane also blocked on page {page}: {still}")
        self.tier_used.append("browser")
        self._maybe_save(keyword, page, html)
        return html, "browser"

    def _maybe_save(self, keyword: str, page: int, html: str) -> None:
        if self.save_html and self.saved < self.save_html:
            import gzip
            from pathlib import Path
            out = Path("out/raw")
            out.mkdir(parents=True, exist_ok=True)
            slug = "".join(c if c.isalnum() else "-" for c in keyword)[:40]
            (out / f"{slug}_p{page}.html.gz").write_bytes(gzip.compress(html.encode("utf-8", "replace")))
            self.saved += 1

    def close(self) -> None:
        if self.browser is not None:
            try:
                self.browser.quit()
            except Exception:  # noqa: BLE001
                pass


def pace(base: float = 3.0, jitter: float = 2.0) -> None:
    time.sleep(base + random.random() * jitter)
