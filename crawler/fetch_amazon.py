"""Amazon fetcher: a cascade of free lanes, validated by parsed card count.

Measured from GitHub runners, 2026-11-06 (see probe/result4.json):

  home -> search         200, 1.86 MB, 48 cards   <- works from Des Moines AND Chicago
  search only (safari)   200, 1.76 MB, 48 cards   <- works with no warm-up at all
  cold chrome search     503, 2.7 KB
  home -> bestsellers -> search  200 but 2.3 KB  <- an extra hop POISONS the session
  home -> dp -> search           200 but 2.3 KB  <- same
  edge101                503

So there is no single magic request. There are three lanes that each work
somewhere, and the only reliable strategy is to try them in order and *validate
the payload* — a 200 with 2.3 KB is a block, and accepting it by status code is
how a crawler reports success while writing zero rows.

The lane that worked is promoted to the front and its session is reused for the
rest of the slice, so steady-state cost is one request per page.
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

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# (name, impersonate, warm homepage first)
LANES = [("chrome_warm", "chrome", True),
         ("safari_direct", "safari", False),
         ("chrome_direct", "chrome", False)]


class AmazonFetcher:
    def __init__(self) -> None:
        self.session = None
        self.lane = None
        self.lanes = list(LANES)
        self.blocks = 0
        self.tier_used: list[str] = []
        self.warmed_at = 0.0

    def _new_session(self, imp: str, warm: bool):
        from curl_cffi import requests as creq
        s = creq.Session(impersonate=imp)
        s.headers.update(BASE_H)
        if warm:
            s.get(HOME, timeout=45, allow_redirects=True)
            time.sleep(1.5)          # cookie write needs a beat before the next hit
            self.warmed_at = time.time()
        return s

    def _cards(self, html: str) -> int:
        return html.count('data-component-type="s-search-result"')

    def search_page(self, keyword: str, page: int = 1) -> tuple[str, str]:
        from .parse_amazon import is_blocked
        url = SEARCH.format(q=keyword.replace(" ", "+"), page=page)
        last = "no lane tried"

        # 1. reuse the session that already worked
        if self.session is not None:
            try:
                r = self.session.get(url, timeout=45, headers={"Referer": HOME})
                why = is_blocked(r.text, r.status_code)
                if not why and self._cards(r.text) >= 5:
                    self.tier_used.append(self.lane)
                    return r.text, self.lane
                last = why or f"cards_{self._cards(r.text)}"
            except Exception as e:  # noqa: BLE001
                last = f"exception:{type(e).__name__}"
            self.session = None

        # 2. walk the lanes, promoting whatever works
        for name, imp, warm in list(self.lanes):
            try:
                s = self._new_session(imp, warm)
                r = s.get(url, timeout=45, headers={"Referer": HOME})
                why = is_blocked(r.text, r.status_code)
                cards = self._cards(r.text)
                if not why and cards >= 5:
                    self.session, self.lane = s, name
                    self.lanes.remove((name, imp, warm))
                    self.lanes.insert(0, (name, imp, warm))
                    self.tier_used.append(name)
                    return r.text, name
                last = f"{name}:{why or f'cards_{cards}'}"
                print(f"[amazon] lane {name} failed ({last})", flush=True)
            except Exception as e:  # noqa: BLE001
                last = f"{name}:exception:{type(e).__name__}"
                print(f"[amazon] {last}", flush=True)
            time.sleep(1.5 + random.random())

        self.blocks += 1
        raise RuntimeError(f"amazon blocked on page {page} ({last})")

    def detail(self, asin: str) -> str:
        for name, imp, warm in list(self.lanes):
            try:
                s = self.session or self._new_session(imp, warm)
                r = s.get(DP.format(asin=asin), timeout=45, headers={"Referer": HOME})
                if 'id="productTitle"' in r.text or "a-price" in r.text:
                    return r.text
            except Exception:  # noqa: BLE001
                continue
            time.sleep(1.5)
        return ""


def pace() -> None:
    time.sleep(2.0 + random.random() * 2.0)
