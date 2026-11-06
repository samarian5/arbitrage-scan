#!/usr/bin/env python3
"""Load balancer: decide how many workers to start, then start exactly that many.

The queue itself is the source of truth (ar_claim_slice hands out disjoint
slices), so this script only answers one question — how much work is there, and
how many free slots are there to do it in:

    dispatch = min(free_slots, due_keywords, budget_left // pages_per_slice)

Launching workers when any term is zero is pure waste: an idle tick should cost
one query, not a browser boot. Run it from cron, or by hand for a burst.

  python scripts/balance.py --workers 6 --pages 2 --dry-run
  python scripts/balance.py --workers 6 --pages 2
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.supa import Supa  # noqa: E402


def plan(db: Supa, workers: int, pages: int) -> dict:
    s = db._req("ar_queue_stats?select=*")[0]
    budget_left = max(0, int(s["page_budget"]) - int(s["pages_today"]))
    due = int(s["due_now"])
    running = int(s["running"])
    free_slots = max(0, workers - running)
    affordable = budget_left // max(pages, 1)
    dispatch = min(free_slots, due, affordable)
    return {"due": due, "running": running, "free_slots": free_slots,
            "budget_left": budget_left, "affordable_slices": affordable,
            "dispatch": max(0, dispatch)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6, help="total slots across repos")
    ap.add_argument("--pages", type=int, default=2, help="pages per keyword slice")
    ap.add_argument("--slices", type=int, default=10, help="slices each started worker takes")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repos", default="", help="comma-separated shard repos (owner/repo)")
    a = ap.parse_args()

    db = Supa()
    p = plan(db, a.workers, a.pages)
    print("plan:", p)

    if p["dispatch"] == 0 or a.dry_run:
        print("nothing to dispatch" if p["dispatch"] == 0 else "dry run")
        return 0

    repos = [r.strip() for r in a.repos.split(",") if r.strip()]
    if not repos:
        print("no --repos given: workers are self-scheduling via their own cron, "
              "the queue will hand them disjoint slices")
        return 0

    from gh import dispatch
    jobs = repos[: p["dispatch"]]
    with ThreadPoolExecutor(max_workers=len(jobs) or 1) as ex:
        futs = [ex.submit(dispatch, repo, "crawl.yml", "main",
                          {"slot": repo.split("/")[-1], "slices": str(a.slices)})
                for repo in jobs]
        for repo, f in zip(jobs, futs):
            print(f"  {repo}: {f.result()}")
    print(f"dispatched {len(jobs)} workers at {time.strftime('%H:%M:%S')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
