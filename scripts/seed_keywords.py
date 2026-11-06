#!/usr/bin/env python3
"""Seed the keyword queue from Amazon's own autocomplete.

completion.amazon.com is a free, unauthenticated endpoint that returns the
phrases real shoppers type. That is a far better keyword list than anything
generated combinatorially: every entry is a query with demonstrated demand, and
it is the same phrase space the crawler will search on both marketplaces.

Usage:
  python scripts/seed_keywords.py --target 2200 --dry-run
  python scripts/seed_keywords.py --target 2200          # writes to Supabase
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COMPLETION = ("https://completion.amazon.com/api/2017/suggestions"
              "?mid=ATVPDKIKX0DER&alias=aps&prefix={p}")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Brand and product stems, chosen because both marketplaces carry them new and
# branded — which is what makes an exact cross-platform match possible.
SEEDS = """
dewalt milwaukee makita ryobi bosch craftsman rigid ridgid
anker ugreen baseus belkin jackery ecoflow bluetti
apple airpods ipad macbook iphone samsung galaxy pixel oneplus
sony bose jbl beats logitech razer corsair keychron
nike adidas jordan yeezy new balance converse vans
lego funko pokemon marvel star wars hot wheels barbie
dyson shark ninja instant pot keurig cuisinart breville
yeti hydro flask stanley contigo owala
north face patagonia columbia carhartt levis
sephora cerave la roche posay ordinary neutrogena olaplex
peloton bowflex nordictrack garmin fitbit wahoo
canon nikon fujifilm gopro dji insta360
asus msi gigabyte intel amd nvidia
brother epson hp xerox
weber traeger blackstone ooni
yeti rtic igloo coleman
craft beer brewing kit
wireless earbuds noise cancelling headphones
mechanical keyboard gaming mouse 27 inch monitor
usb c hub portable ssd 2tb micro sd card
robot vacuum air purifier dehumidifier
espresso machine milk frother electric kettle
air fryer pressure cooker stand mixer blender
cordless drill impact driver circular saw
socket set wrench set multimeter soldering iron
car dash cam jump starter tire inflator
gaming chair standing desk monitor arm
yoga mat adjustable dumbbell resistance bands
cat litter box dog crate pet camera
backpack carry on luggage packing cubes
water bottle lunch box insulated tumbler
weighted blanket memory foam pillow
acne treatment retinol serum vitamin c serum
hair dryer straightener curling iron
gaming console controller vr headset
drone camera gimbal action camera
printer toner ink cartridge label maker
office chair desk lamp whiteboard planner
""".split()

BAD = re.compile(r"(?i)\b(walmart|target|near me|amazon|coupon|promo|free|"
                 r"cheap|review|vs|recall|lawsuit|download|pdf|app)\b")
CLEAN = re.compile(r"[^a-z0-9 +\-]")


def suggest(prefix: str, timeout: int = 15) -> list[str]:
    url = COMPLETION.format(p=urllib.parse.quote_plus(prefix))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return [s.get("value", "") for s in data.get("suggestions", []) if s.get("value")]


def good(kw: str) -> bool:
    kw = kw.strip().lower()
    if not (2 <= len(kw.split()) <= 7):
        return False
    if len(kw) < 8 or len(kw) > 70:
        return False
    if BAD.search(kw):
        return False
    if not re.fullmatch(r"[a-z0-9 +\-]+", kw):
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=2200)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="data/keywords.txt")
    a = ap.parse_args()

    found: dict[str, str] = {}
    rounds = [(s, 0) for s in SEEDS]
    seen_prefix = set()

    while rounds and len(found) < a.target:
        prefix, depth = rounds.pop(0)
        key = prefix.lower()
        if key in seen_prefix:
            continue
        seen_prefix.add(key)
        try:
            vals = suggest(prefix)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {prefix}: {type(e).__name__}", file=sys.stderr)
            vals = []
        for v in vals:
            v = CLEAN.sub("", v.lower()).strip()
            v = re.sub(r"\s+", " ", v)
            if good(v) and v not in found:
                found[v] = prefix
        # second level: expand the best suggestions that look like product + brand
        if depth == 0 and len(vals) and len(found) < a.target:
            for v in vals[:3]:
                v = CLEAN.sub("", v.lower()).strip()
                if good(v) and len(v.split()) <= 4:
                    rounds.append((v, 1))
        if len(found) % 200 == 0 and found:
            print(f"  … {len(found)} keywords", file=sys.stderr)
        time.sleep(0.12)

    kws = sorted(found)
    print(f"collected {len(kws)} unique keywords")
    print("sample:", kws[:8], "…", kws[-4:])

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        f.write("\n".join(kws) + "\n")
    print("wrote", a.out)

    if a.dry_run:
        return 0

    from crawler.supa import Supa
    db = Supa()
    rows = []
    for i, kw in enumerate(kws):
        # stagger the first pass across ~24h so 2000 keywords do not all become
        # due in the same minute and starve the budget
        rows.append({"keyword": kw, "depth": 2, "interval_hours": 24,
                     "due_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime(time.time() + (i % 1440) * 60))})
    n = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        db._req("ar_keywords?on_conflict=keyword", "POST", chunk)
        n += len(chunk)
        print(f"  inserted {n}/{len(rows)}")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
