#!/usr/bin/env python3
"""Sync MarketTrace snapshots through signed Supabase Storage URLs.

GitHub Actions authenticates with its short-lived OIDC identity; no storage or
Supabase secret is ever placed in the repository or in the browser.
"""
from __future__ import annotations
import json, os, sys, urllib.request
from pathlib import Path

BASE = "https://rftgfskqvyhdgirhwmvm.supabase.co/functions/v1/market-ingest"

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

def upload(root: Path):
    paths = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.json"))
    if not paths: raise RuntimeError("No market data to upload")
    for group in chunks(paths):
        signed = api("sign", {"paths": group})["uploads"]
        for path in group:
            request = urllib.request.Request(signed[path], data=(root / path).read_bytes(), method="PUT",
                headers={"Content-Type": "application/json", "x-upsert": "true"})
            with urllib.request.urlopen(request, timeout=120): pass
    print(f"Private Supabase storage updated: {len(paths)} files")

def download(root: Path):
    listing = api("list").get("files", [])
    if not listing:
        print("No private snapshot yet; first run will seed it.")
        return False
    for group in chunks(listing):
        signed = api("download", {"paths": group})["downloads"]
        for path in group:
            with urllib.request.urlopen(signed[path], timeout=120) as response:
                target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(response.read())
    print(f"Private Supabase storage restored: {len(listing)} files")
    return True

if __name__ == "__main__":
    root = Path(sys.argv[2] if len(sys.argv) > 2 else "data")
    if sys.argv[1] == "upload":
        upload(root)
    elif not download(root):
        sys.exit(2)
