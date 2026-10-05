#!/usr/bin/env python3
"""Sync MarketTrace snapshots through signed Supabase Storage URLs.

GitHub Actions authenticates with its short-lived OIDC identity; no storage or
Supabase secret is ever placed in the repository or in the browser.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib, json, os, sys, urllib.request
from pathlib import Path

BASE = "https://rftgfskqvyhdgirhwmvm.supabase.co/functions/v1/market-ingest"
SYNC_STATE = ".markettrace-sync.json"

def oidc_token():
    request = urllib.request.Request(
        os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=markettrace-ingest",
        headers={"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["value"]

def api(action, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{BASE}?action={action}", data=body, method="POST",
        headers={"Authorization": "Bearer " + oidc_token(), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)

def chunks(values, size=80):
    for i in range(0, len(values), size): yield values[i:i + size]

def state_path(root: Path):
    return root / SYNC_STATE

def load_state(root: Path):
    try:
        return json.loads(state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

def save_state(root: Path, state):
    state_path(root).write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")

def data_hash(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def local_hashes(root: Path):
    return {
        path.relative_to(root).as_posix(): data_hash(path)
        for path in root.rglob("*.json")
        if path.name != SYNC_STATE
    }

def upload(root: Path):
    current = local_hashes(root)
    previous = load_state(root)
    paths = sorted(path for path, digest in current.items() if previous.get(path) != digest)
    if not current:
        raise RuntimeError("No market data to upload")
    if not paths:
        print(f"Private Supabase storage unchanged: {len(current)} files")
        return
    for group in chunks(paths):
        signed = api("sign", {"paths": group})["uploads"]
        for path in group:
            url = signed[path]
            if url.startswith("/"):
                url = "https://rftgfskqvyhdgirhwmvm.supabase.co/storage/v1" + url
            request = urllib.request.Request(url, data=(root / path).read_bytes(), method="PUT",
                headers={"Content-Type": "application/json", "x-upsert": "true"})
            with urllib.request.urlopen(request, timeout=120): pass
    save_state(root, current)
    print(f"Private Supabase storage updated: {len(paths)} changed files ({len(current)} total)")

def download(root: Path):
    listing = api("list").get("files", [])
    if not listing:
        print("No private snapshot yet; first run will seed it.")
        return False
    state = {}
    for group in chunks(listing):
        signed = api("download", {"paths": group})["downloads"]
        def fetch_one(path):
            with urllib.request.urlopen(signed[path], timeout=120) as response:
                return path, response.read()
        # Stahování stovek malých JSON souborů po jednom tvořilo většinu běhu.
        # Všechny zápisy míří do odlišných souborů, proto je bezpečné je paralelizovat.
        with ThreadPoolExecutor(max_workers=12) as pool:
            for path, body in pool.map(fetch_one, group):
                target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(body)
                state[path] = hashlib.sha256(body).hexdigest()
    save_state(root, state)
    print(f"Private Supabase storage restored: {len(listing)} files")
    return True

if __name__ == "__main__":
    root = Path(sys.argv[2] if len(sys.argv) > 2 else "data")
    if sys.argv[1] == "upload":
        upload(root)
    elif not download(root):
        sys.exit(2)
