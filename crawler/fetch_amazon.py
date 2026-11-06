"""Amazon fetcher: the warm-session lane.

Measured 2026-11-06 from GitHub runners (Azure, Des Moines):
  cold request  -> 503 / 2.2 KB "Sorry! Something went wrong!"
  warm session  -> 200 / 1.77 MB, 129 ASINs, 48 result cards, 0 captcha

The warm-up is a plain GET of the homepage, which plants session cookies
(session-id, ubid-main, ...). Without them Amazon's edge refuses the search
endpoint from datacenter IPs. Nothing else is needed — no key, no proxy, no
paid API — so this lane is free and stays inside the ToS posture documented in
docs/ADR-0002.

If the warm-up ever stops working the fetcher does NOT retry in a loop: it
re-warms once in a fresh session, then reports the block so the run is marked
rather than burning the budget against a wall.
"""
from __future__ import annotations

import random
import time

HOME = "https://www.amazon.com/"
SEARCH = "https://www.amazon.com/s?k={q}&page={page}"
DP = "https://www.amazon.com/dp/{asin}"

BASE_H = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class AmazonFetcher:
    def __init__(self) -> None:
        self.session = None
        self.warmed_at = 0.0
        self.blocks = 0
        self.tier_used: list[str] = []

    def _new_session(self):
        from curl_cffi import requests as creq
        s = creq.Session(impersonate="chrome")
        s.headers.update(BASE_H)
        s.get(HOME, timeout=45, allow_redirects=True)   # <- the whole trick
        self.session = s
        self.warmed_at = time.time()
        return s

    def _s(self):
        # re-warm every ~20 minutes so a long slice keeps fresh cookies
        if self.session is None or time.time() - self.warmed_at > 1200:
            return self._new_session()
        return self.session

    def search_page(self, keyword: str, page: int = 1) -> tuple[str, str]:
        from .parse_amazon import is_blocked
        url = SEARCH.format(q=keyword.replace(" ", "+"), page=page)
        html, reason = "", None
        for attempt in range(2):
            try:
                r = self._s().get(url, timeout=45, headers={"Referer": HOME})
                html, reason = r.text, is_blocked(r.text, r.status_code)
            except Exception as e:  # noqa: BLE001
                html, reason = "", f"exception:{type(e).__name__}"
            if not reason:
                self.tier_used.append("http_warm")
                return html, "http_warm"
            if attempt == 0:
                print(f"[amazon] page {page} blocked ({reason}) -> fresh warm session", flush=True)
                self._new_session()
                time.sleep(3 + random.random() * 2)
        self.blocks += 1
        raise RuntimeError(f"amazon blocked on page {page}: {reason}")

    def detail(self, asin: str) -> str:
        from .parse_amazon import is_blocked
        r = self._s().get(DP.format(asin=asin), timeout=45, headers={"Referer": HOME})
        if is_blocked(r.text, r.status_code):
            self._new_session()
            r = self._s().get(DP.format(asin=asin), timeout=45, headers={"Referer": HOME})
        return r.text


def pace() -> None:
    time.sleep(2.0 + random.random() * 2.0)
