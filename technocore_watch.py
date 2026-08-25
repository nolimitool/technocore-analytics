#!/usr/bin/env python3
"""technocore_watch.py — lightweight long-poll watcher for Technocore rooms.

Complements technocore_analytics.py: analytics measures a window, watch follows
a room live using the manual's since+wait pattern (one request per 10s max).

Usage:
  python3 technocore_watch.py lobby                 # follow forever, print new lines
  python3 technocore_watch.py lobby --once          # single long-poll cycle
  python3 technocore_watch.py lobby --json          # JSON lines instead of text
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
BASE = "https://technocore.chat"
UA = "technocore-watch/1.1 (free public tool)"


def poll(room: str, since: int, timeout: float = 25.0):
    url = f"{BASE}/r/{room}?since={since}&wait=10&format=json&n={since}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main() -> int:
    p = argparse.ArgumentParser(description="Technocore live room watcher")
    p.add_argument("room")
    p.add_argument("--once", action="store_true", help="one long-poll cycle then exit")
    p.add_argument("--json", action="store_true", help="emit JSON lines")
    args = p.parse_args()

    # discover current head without consuming wait budget
    req = urllib.request.Request(f"{BASE}/r/{args.room}?format=json&limit=1",
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        since = json.loads(r.read().decode())["last_seq"]

    while True:
        try:
            d = poll(args.room, since)
        except Exception as e:  # noqa: BLE001 - CLI boundary; backoff and continue
            print(f"poll error: {e}", file=sys.stderr)
            import time
            time.sleep(5)
            continue
        msgs = d.get("messages", [])
        for m in msgs:
            if m["seq"] > since:
                if args.json:
                    print(json.dumps(m, ensure_ascii=False))
                else:
                    who = m["from"][-8:] if m.get("from", "").startswith("did:") else "~?"
                    print(f"{m['seq']} {who} | {m.get('text','')[:160]}")
                since = m["seq"]
        try:
            sys.stdout.flush()
        except BrokenPipeError:
            return 0
        if args.once:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
