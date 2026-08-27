#!/usr/bin/env python3
"""
nonce_fingerprint.py — Analyze nonce patterns to fingerprint client types
==========================================================================
Input:  JSON from technocore_analytics.py or raw message samples
Output: Per-room fingerprint showing epoch-ms vs random-nonce ratio,
        likely client culture (human vs bot vs mixed), and trend over time.

Requirements: Python stdlib only.
"""

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

try:
    from urllib.request import Request, urlopen
except ImportError:
    print("Fatal: urllib not available")
    sys.exit(1)

ROOMS = ["lobby", "technocore", "meta", "ai", "did-key-method", "nonce-security", "d-flop-labs-research"]
LIVE_URL = "https://technocore.chat/kv/tc-analytics/latest"

def fetch_live() -> dict | None:
    try:
        req = Request(LIVE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", errors="replace")
            # Strip untrusted-content prefix if present
            if body.startswith("!! UNTRUSTED CONTENT"):
                body = body.split("\n\n", 1)[-1]
            return json.loads(body)
    except Exception as e:
        print(f"[fingerprint] live fetch error: {e}")
        return None

def classify(epoch_pct: float, random_pct: float) -> str:
    """Heuristic client-culture classification from nonce mix."""
    if epoch_pct > 70 and random_pct < 20:
        return "human-like (clock-synced clients)"
    if random_pct > 70 and epoch_pct < 20:
        return "bot-like (random nonce generators)"
    if epoch_pct > 40 and random_pct > 30:
        return "mixed (human + bot)"
    if epoch_pct < 10 and random_pct < 10:
        return "static/low-activity"
    return "mixed/unknown"

def analyze_room(data: dict, room: str) -> dict | None:
    r = data.get(room)
    if not r:
        return None
    epoch = r.get("nonce_epoch_ms_like", 0)
    rand = r.get("nonce_random_like", 0)
    total = max(epoch + rand, 1)
    epoch_pct = round(epoch / total * 100, 1)
    random_pct = round(rand / total * 100, 1)
    return {
        "room": room,
        "msgs_per_min": r.get("msgs_per_minute", 0),
        "unique_did_ratio": r.get("unique_did_ratio", 0),
        "top10_word_share_pct": r.get("top10_word_share_pct", 0),
        "nonce_epoch_ms_like": epoch,
        "nonce_random_like": rand,
        "epoch_pct": epoch_pct,
        "random_pct": random_pct,
        "classification": classify(epoch_pct, random_pct),
        "last_seq": r.get("last_seq", 0),
        "botlikeness": r.get("botlikeness", "unknown"),
    }

def main():
    print("[fingerprint] Nonce fingerprint analysis")
    data = fetch_live()
    if not data:
        print("[fingerprint] No live data available")
        sys.exit(1)

    results = []
    for room in ROOMS:
        analysis = analyze_room(data, room)
        if analysis:
            results.append(analysis)

    # Print summary table
    print("\nRoom                    msgs/min  uniq%  top10%  epoch%  rand%  classification")
    print("-" * 100)
    for r in results:
        print(f"{r['room']:<24} {r['msgs_per_min']:>8} {r['unique_did_ratio']*100:>6.1f} {r['top10_word_share_pct']:>7.1f} {r['epoch_pct']:>7.1f} {r['random_pct']:>7.1f}  {r['classification']}")

    # Save artifact
    out = Path("/root/technocore/nonce_fingerprint.json")
    out.write_text(json.dumps({"ts": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), "results": results}, indent=2))
    print(f"\n[fingerprint] Saved to {out}")

if __name__ == "__main__":
    main()
