#!/usr/bin/env python3
"""Publish the scanner to GitHub through Composio's authenticated proxy.

No PAT, no git binary: every call goes through `composio proxy --toolkit github`,
which signs with the connected GitHub account.

  1. creates the public repo (unmetered Actions minutes)
  2. lands every file in one commit on main
  3. writes SUPABASE_URL / SUPABASE_SERVICE_KEY as Actions secrets
  4. enables Pages from /docs
  5. optionally dispatches a validation run for one keyword

  python scripts/deploy.py --repo arbitrage-scan --dispatch-keyword "dewalt 20v drill"
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import gh  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "out", "__pycache__", ".venv", "node_modules", "cache",
             ".pytest_cache", "downloaded_files", ".mypy_cache"}
SKIP_EXT = {".pyc", ".log", ".lock", ".zip"}
SKIP_GLOBS = ["probe/*.html", "probe/*.txt", "probe/amz_*.html", "data/*.json"]


def env() -> dict[str, str]:
    out = {}
    for path in (ROOT / ".env", Path.home() / "etsy-scraper" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="arbitrage-scan")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--skip-pages", action="store_true")
    ap.add_argument("--dispatch-keyword", default="")
    a = ap.parse_args()

    cfg = env()
    anon = cfg.get("SUPABASE_ANON_KEY", "")
    url = cfg.get("SUPABASE_URL", "")
    if not anon or not url:
        print("!! SUPABASE_URL / SUPABASE_ANON_KEY missing from env files")
        return 1

    # the dashboard reads this at runtime; the anon key is public by design (RLS)
    (ROOT / "docs" / "config.js").write_text(
        "// Filled at deploy time. The anon key is public by design: RLS grants\n"
        "// SELECT only, so this file cannot write anything.\n"
        "window.CFG = {\n"
        f'  SUPABASE_URL: "{url}",\n'
        f'  SUPABASE_ANON_KEY: "{anon}",\n'
        "};\n")

    who = gh.owner()
    print("owner:", who)

    st, repo = gh.proxy("POST", "/user/repos", {
        "name": a.repo, "private": a.private, "auto_init": True,
        "description": "eBay vs Amazon arbitrage scanner — free-stack crawler, matcher, dashboard"})
    print("create repo:", st, (repo.get("html_url") if isinstance(repo, dict) else "")[:80])

    files = []
    for p in gh.collect(ROOT):
        rel = p.relative_to(ROOT).as_posix()
        if any(rel.startswith(g.split("*")[0]) and rel.endswith(g.split("*")[-1])
               for g in SKIP_GLOBS):
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        if p.suffix in SKIP_EXT:
            continue
        files.append(rel)
    print(f"{len(files)} files")

    sha = gh.put_files(a.repo, files, "arbitrage scanner: crawler, matcher, schema, dashboard",
                       root=ROOT, owner_login=who)
    print("commit:", sha[:10])

    # ---- secrets ---------------------------------------------------------
    st, pk = gh.proxy("GET", f"/repos/{who}/{a.repo}/actions/secrets/public-key")
    if isinstance(pk, dict) and pk.get("key"):
        from nacl import encoding, public
        box = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder()))
        for name, value in (("SUPABASE_URL", url),
                            ("SUPABASE_SERVICE_KEY", cfg.get("SUPABASE_SERVICE_KEY", ""))):
            if not value:
                print(f"  !! {name} missing")
                continue
            enc = base64.b64encode(box.encrypt(value.encode())).decode()
            s2, _ = gh.proxy("PUT", f"/repos/{who}/{a.repo}/actions/secrets/{name}",
                             {"encrypted_value": enc, "key_id": pk["key_id"]})
            print(f"  secret {name}: {s2}")

    if not a.skip_pages:
        s3, r3 = gh.proxy("POST", f"/repos/{who}/{a.repo}/pages",
                          {"source": {"branch": "main", "path": "/docs"}})
        print("pages:", s3, str(r3)[:120])

    if a.dispatch_keyword:
        inputs = {"keyword": a.dispatch_keyword, "slices": "1", "slot": "validate"}
        st = gh.dispatch(a.repo, "crawl-slice.yml", "main", inputs, owner_login=who)
        print("validation dispatch:", st)

    print(f"\nrepo: https://github.com/{who}/{a.repo}")
    print(f"site: https://{who}.github.io/{a.repo}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
