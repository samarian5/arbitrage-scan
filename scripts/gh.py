#!/usr/bin/env python3
"""GitHub transport through Composio's authenticated proxy (no PAT, no git binary).

Every call shells out to `composio proxy ... --toolkit github`, which signs the
request with the connected GitHub account. Large bodies go in on stdin because
argv has a hard length limit.

  gh.py whoami
  gh.py user-repos
  gh.py runs samarian5 ebay-probe
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "out", "__pycache__", ".venv", "node_modules", "cache",
             "downloaded_files", ".pytest_cache", ".mypy_cache", "data"}
SKIP_EXT = {".pyc", ".log", ".lock", ".gz", ".zip"}
SKIP_FILES = {".env"}


def proxy(method: str, path: str, body=None, timeout: int = 180):
    cmd = ["composio", "proxy", path, "--toolkit", "github", "-X", method]
    payload = None
    if body is not None:
        cmd += ["-d", "-"]
        payload = json.dumps(body)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=payload)
    raw = (p.stdout or "").strip()
    if not raw:
        return p.returncode, (p.stderr or "").strip()[:500]
    try:
        outer = json.loads(raw)
    except json.JSONDecodeError:
        return p.returncode, raw[:500]
    data = outer.get("data", outer) if isinstance(outer, dict) else outer
    if isinstance(data, dict) and "response_data" in data:
        data = data["response_data"]
    status = 200
    if isinstance(outer, dict):
        status = outer.get("status", outer.get("status_code", 200)) or 200
    return (int(status) if str(status).isdigit() else 200), data


def owner() -> str:
    st, me = proxy("GET", "/user")
    if not isinstance(me, dict) or "login" not in me:
        raise SystemExit(f"GET /user failed: {st} {str(me)[:300]}")
    return me["login"]


def collect(root: Path = ROOT, only: list[str] | None = None) -> list[Path]:
    files = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix in SKIP_EXT or p.name in SKIP_FILES:
            continue
        if only and rel.as_posix() not in only:
            continue
        files.append(p)
    return sorted(files)


def put_files(repo: str, paths: list[str], message: str, root: Path = ROOT,
              owner_login: str | None = None, branch: str = "main") -> str:
    """Create blobs for `paths` (relative to root) and land ONE commit on `branch`.

    Uses base_tree so files not mentioned are preserved.
    """
    who = owner_login or owner()
    st, ref = proxy("GET", f"/repos/{who}/{repo}/git/ref/heads/{branch}")
    parent = ref.get("object", {}).get("sha") if isinstance(ref, dict) else None
    st, bref = proxy("GET", f"/repos/{who}/{repo}/git/refs/heads/{branch}")
    if not parent and isinstance(bref, list) and bref:
        parent = bref[0].get("object", {}).get("sha")
    st, hc = proxy("GET", f"/repos/{who}/{repo}/commits/{branch}")
    base_tree = hc.get("commit", {}).get("tree", {}).get("sha") if isinstance(hc, dict) else None
    if not parent and isinstance(hc, dict):
        parent = hc.get("sha")

    tree = []
    for rel in paths:
        p = root / rel
        if not p.exists():
            print(f"  !! missing {rel}")
            continue
        content = base64.b64encode(p.read_bytes()).decode()
        st, blob = proxy("POST", f"/repos/{who}/{repo}/git/blobs",
                         {"content": content, "encoding": "base64"})
        sha = blob.get("sha") if isinstance(blob, dict) else None
        if not sha:
            print(f"  !! blob failed {rel}: {st} {str(blob)[:200]}")
            continue
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": sha})
        print(f"  blob {rel} -> {sha[:8]}")

    body = {"tree": tree, "message": message}
    if base_tree:
        body["base_tree"] = base_tree
    st, nt = proxy("POST", f"/repos/{who}/{repo}/git/trees", body)
    tree_sha = nt.get("sha") if isinstance(nt, dict) else None
    if not tree_sha:
        raise SystemExit(f"tree failed: {st} {str(nt)[:400]}")

    commit = {"message": message, "tree": tree_sha}
    if parent:
        commit["parents"] = [parent]
    st, c = proxy("POST", f"/repos/{who}/{repo}/git/commits", commit)
    sha = c.get("sha") if isinstance(c, dict) else None
    if not sha:
        raise SystemExit(f"commit failed: {st} {str(c)[:400]}")
    st, _ = proxy("PATCH", f"/repos/{who}/{repo}/git/refs/heads/{branch}",
                  {"sha": sha, "force": True})
    print(f"  commit {sha[:10]} ref->{st}")
    return sha


def dispatch(repo: str, workflow: str, ref: str = "main", inputs: dict | None = None,
             owner_login: str | None = None) -> int:
    who = owner_login or owner()
    st, r = proxy("POST", f"/repos/{who}/{repo}/actions/workflows/{workflow}/dispatches",
                  {"ref": ref, "inputs": inputs or {}})
    print("dispatch:", st, str(r)[:200])
    return st


def runs(repo: str, owner_login: str | None = None, limit: int = 8) -> list[dict]:
    who = owner_login or owner()
    st, r = proxy("GET", f"/repos/{who}/{repo}/actions/runs?per_page={limit}")
    if isinstance(r, dict) and "workflow_runs" in r:
        return r["workflow_runs"]
    print("runs:", st, str(r)[:300])
    return []


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "whoami"
    if cmd == "whoami":
        print(owner())
    elif cmd == "user-repos":
        st, d = proxy("GET", "/user/repos?per_page=100&sort=updated")
        for r in d or []:
            print(r["name"], r["private"], r["pushed_at"])
    elif cmd == "runs":
        for r in runs(sys.argv[2]):
            print(r["id"], r["name"], r["status"], r["conclusion"], r["head_sha"][:8],
                  r["created_at"], r["event"])
