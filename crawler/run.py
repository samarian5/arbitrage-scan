"""One slice of work: claim -> fetch both platforms -> parse -> ingest -> match.

A slice is one keyword, N search pages, both marketplaces. Everything is
keyed by run_id, so re-running or retrying a slice is idempotent at the
database (unique constraints on listing identity and on listing x run).

Usage (inside CI):
  python -m crawler.run --slot shard-1
  python -m crawler.run --keyword "dewalt 20v drill" --pages 1 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

from . import matcher
from .fetch_amazon import AmazonFetcher, pace as amz_pace
from .fetch_ebay import EbayFetcher, pace as ebay_pace
from .parse_amazon import parse_search_html as parse_amazon_html, is_blocked as amz_blocked
from .parse_ebay import parse_search_html as parse_ebay_html
from .supa import Supa

OUT = Path("out")


def enrich(rows: list[dict], keyword_id: int | None, page: int) -> list[dict]:
    out = []
    for r in rows:
        r = dict(r)
        r["norm"] = matcher.norm(r.get("title"))
        r["tokens"] = matcher.tokens(r.get("title"))
        r["keyword_id"] = keyword_id
        r["page"] = page
        # eBay's free-shipping flag is real; a null means "not stated", not "paid"
        if r.get("shipping_cents") is None:
            r["shipping_cents"] = 0
        out.append(r)
    return out


def run_slice(slice_: dict, args) -> dict:
    keyword = slice_["keyword"]
    kid = slice_.get("keyword_id")
    run_id = slice_["run_id"]
    page_from = int(slice_.get("page_from") or 1)
    page_to = int(slice_.get("page_to") or page_from)

    started = time.time()
    ebay = EbayFetcher(profile_dir=os.environ.get("EBAY_PROFILE", ".ebay-profile"),
                       allow_browser=not args.no_browser,
                       save_html=args.save_html)
    amazon = AmazonFetcher()

    eb_rows: list[dict] = []
    am_rows: list[dict] = []
    meta = {"run_id": run_id, "keyword": keyword, "keyword_id": kid,
            "page_from": page_from, "page_to": page_to, "tiers": [], "challenges": 0,
            "stopped_reason": "depth", "errors": []}

    try:
        for page in range(page_from, page_to + 1):
            # ---- eBay ----
            try:
                html, tier = ebay.search_page(keyword, page)
                rows = enrich(parse_ebay_html(html), kid, page)
                eb_rows.extend(rows)
                meta["tiers"].append(f"ebay:{tier}")
            except Exception as e:  # noqa: BLE001
                meta["errors"].append(f"ebay p{page}: {e}")
                meta["stopped_reason"] = "ebay_blocked"
                break

            ebay_pace()

            # ---- Amazon ----
            try:
                html, tier = amazon.search_page(keyword, page)
                rows = enrich(parse_amazon_html(html), kid, page)
                am_rows.extend(rows)
                meta["tiers"].append(f"amazon:{tier}")
            except Exception as e:  # noqa: BLE001
                meta["errors"].append(f"amazon p{page}: {e}")
                if meta["stopped_reason"] == "depth":
                    meta["stopped_reason"] = "amazon_blocked"

            if page < page_to:
                amz_pace()

        meta["challenges"] = ebay.challenges + amazon.blocks
        meta["tiers"] = ",".join(sorted(set(meta["tiers"])))
    finally:
        ebay.close()

    meta["ebay_seen"] = len(eb_rows)
    meta["amazon_seen"] = len(am_rows)

    # ---- optional: no database (dry run for CI validation) ----------------
    if args.dry_run:
        best = [r for r in sorted(eb_rows, key=lambda r: -(r.get("price_cents") or 0))][:5]
        meta["sample_ebay"] = [{k: r.get(k) for k in
                               ("platform_id", "title", "price_cents", "brand", "tokens")}
                              for r in best]
        meta["sample_amazon"] = [{k: r.get(k) for k in
                                 ("platform_id", "title", "price_cents", "brand", "tokens")}
                                for r in am_rows[:3]]
        meta["duration_s"] = round(time.time() - started, 1)
        return meta

    # ---- ingest ----------------------------------------------------------
    db = Supa()
    threshold = float(db.setting("match_threshold", "0.72") or 0.72)

    db.update_run(run_id, {"ebay_seen": len(eb_rows), "amazon_seen": len(am_rows),
                           "challenges": meta["challenges"], "tiers": meta["tiers"]})

    written = 0
    if eb_rows or am_rows:
        res = db.ingest(run_id, eb_rows + am_rows)
        written = int(res.get("rows", 0)) if isinstance(res, dict) else 0

    # ---- resolve ids for matching ---------------------------------------
    def ids_for(platform: str, platform_ids: list[str]) -> dict[str, int]:
        if not platform_ids:
            return {}
        out: dict[str, int] = {}
        for i in range(0, len(platform_ids), 50):
            chunk = platform_ids[i:i + 50]
            quoted = ",".join('"' + p.replace('"', "") + '"' for p in chunk)
            rows = db._req(f"ar_listings?platform=eq.{platform}&platform_id=in.({quoted})&select=id,platform_id")
            for r in rows or []:
                out[r["platform_id"]] = r["id"]
        return out

    e_ids = ids_for("ebay", [r["platform_id"] for r in eb_rows])
    a_ids = ids_for("amazon", [r["platform_id"] for r in am_rows])
    for r in eb_rows:
        r["id"] = e_ids.get(r["platform_id"])
    for r in am_rows:
        r["id"] = a_ids.get(r["platform_id"])

    matches = matcher.match_pool(eb_rows, am_rows, threshold)
    for m in matches:
        m["keyword_id"] = kid
        m.pop("ebay_platform_id", None)
        m.pop("amazon_platform_id", None)
    n_matches = db.upsert_matches(matches) if matches else 0

    meta["duration_s"] = round(time.time() - started, 1)
    db.update_run(run_id, {
        "status": "succeeded" if not meta["errors"] else "failed",
        "stopped_reason": meta["stopped_reason"], "rows_written": written,
        "matches": n_matches, "duration_s": meta["duration_s"],
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    pages_done = max(0, min(page_to, page_from + len(meta["tiers"]) // 2) - page_from + 1)
    db.finish(kid, pages_done, "queued" if not meta["errors"] else "blocked",
              meta["stopped_reason"])
    meta["rows_written"] = written
    meta["matches"] = n_matches
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default=os.environ.get("SHARD", "local"))
    ap.add_argument("--pages", type=int, default=None)
    ap.add_argument("--keyword", default=None, help="run one keyword without claiming")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--save-html", type=int, default=0)
    ap.add_argument("--max-slices", type=int, default=1)
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    metas = []

    for _ in range(args.max_slices):
        if args.keyword:
            slice_ = {"keyword": args.keyword, "keyword_id": None,
                      "run_id": f"adhoc-{int(time.time())}",
                      "page_from": 1, "page_to": args.pages or 1}
        elif args.dry_run:
            slice_ = {"keyword": os.environ.get("KEYWORD", "dewalt 20v drill"),
                      "keyword_id": None, "run_id": f"dry-{int(time.time())}",
                      "page_from": 1, "page_to": args.pages or 1}
        else:
            db = Supa()
            slice_ = db.claim(args.slot, args.pages)
            if not slice_:
                print("[run] nothing due")
                break
            db.insert_run({"run_id": slice_["run_id"], "keyword_id": slice_["keyword_id"],
                           "keyword": slice_["keyword"], "shard": args.slot,
                           "status": "running", "page_from": slice_["page_from"],
                           "page_to": slice_["page_to"]})
        try:
            if not args.dry_run and slice_.get("keyword_id") is None:
                # ad-hoc keyword: still record the run so the board shows it
                db = Supa()
                db.insert_run({"run_id": slice_["run_id"], "keyword_id": None,
                               "keyword": slice_["keyword"], "shard": args.slot,
                               "status": "running", "page_from": slice_["page_from"],
                               "page_to": slice_["page_to"]})
            m = run_slice(slice_, args)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            m = {"run_id": slice_.get("run_id"), "keyword": slice_.get("keyword"),
                 "status": "failed", "error": str(e)[:300]}
            # hand the keyword back immediately with backoff; the reaper in
            # ar_claim_slice() is only the safety net for a hard-killed runner
            if slice_.get("keyword_id"):
                try:
                    Supa().finish(slice_["keyword_id"], 0, "blocked", str(e)[:200])
                except Exception:  # noqa: BLE001
                    pass
        print(json.dumps(m))
        metas.append(m)
        if args.keyword or args.dry_run:
            break
        # Pace slices: four workers hitting the same sites in the same minute is
        # what turns a 30% block rate into a 70% one.
        time.sleep(6 + (hash(m.get("keyword") or "") % 7))

    (OUT / "meta.json").write_text(json.dumps(metas, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
