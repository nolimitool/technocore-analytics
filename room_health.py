#!/usr/bin/env python3
"""
room_health.py — Compute per-room health/diversity metrics
==========================================================
Metrics:
  - activity score
  - diversity score (unique DIDs / total samples)
  - concentration risk (top-10 word share)
  - nonce entropy mix
  - overall health grade

Requirements: Python stdlib only.
"""

import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

LIVE_URL = "https://technocore.chat/kv/tc-analytics/latest"
OUT = Path("/root/technocore/room_health.json")

def fetch_live() -> dict | None:
    try:
        req = Request(LIVE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", errors="replace")
            if body.startswith("!! UNTRUSTED CONTENT"):
                body = body.split("\n\n", 1)[-1]
            return json.loads(body)
    except Exception as e:
        print(f"[health] fetch error: {e}")
        return None

def grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "F"

def health_for(room: str, data: dict) -> dict | None:
    r = data.get(room)
    if not r:
        return None
    activity = min(r.get("msgs_per_minute", 0) / 50, 100)  # scale: 50 msgs/min = 100
    diversity = r.get("unique_did_ratio", 0) * 100
    concentration = max(0, 100 - r.get("top10_word_share_pct", 0))
    total_nonce = max(r.get("nonce_epoch_ms_like", 0) + r.get("nonce_random_like", 0), 1)
    mix = (min(r.get("nonce_epoch_ms_like", 0), total_nonce) / total_nonce) * 100
    mix_score = 100 - abs(50 - mix) * 2  # best near 50/50
    overall = (activity * 0.3 + diversity * 0.35 + concentration * 0.2 + max(mix_score, 0) * 0.15)
    return {
        "room": room,
        "activity_score": round(activity, 1),
        "diversity_score": round(diversity, 1),
        "concentration_score": round(concentration, 1),
        "nonce_mix_score": round(max(mix_score, 0), 1),
        "overall_score": round(overall, 1),
        "grade": grade(overall),
        "msgs_per_min": r.get("msgs_per_minute", 0),
        "unique_did_ratio": r.get("unique_did_ratio", 0),
        "botlikeness": r.get("botlikeness", "unknown"),
        "last_seq": r.get("last_seq", 0),
    }

def main():
    print("[health] Room health analysis")
    data = fetch_live()
    if not data:
        print("[health] No data")
        sys.exit(1)

    rooms = [k for k in data.keys() if not k.startswith("_")]
    results = []
    for room in rooms:
        h = health_for(room, data)
        if h:
            results.append(h)

    print("\nRoom                  activity  diversity  concentration  nonce_mix  overall  grade")
    print("-" * 95)
    for h in results:
        print(f"{h['room']:<22} {h['activity_score']:>8.1f} {h['diversity_score']:>9.1f} {h['concentration_score']:>13.1f} {h['nonce_mix_score']:>9.1f} {h['overall_score']:>8.1f}  {h['grade']}")

    out = {
        "ts": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        "results": results
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"\n[health] Saved to {OUT}")

if __name__ == "__main__":
    main()
