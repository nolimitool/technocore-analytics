#!/usr/bin/env python3
"""Hourly snapshot runner for Technocore rooms (cron). Runs the stdlib analytics
tool per room and combines results into one JSON snapshot file."""
import subprocess, json, sys, time
from datetime import datetime, timezone

ROOMS = ["lobby", "technocore", "meta", "ai",
         "did-key-method", "nonce-security", "d-flop-labs-research"]
SNAP = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
ANALYTICS = "/root/technocore/repo/technocore_analytics.py"
OUT = f"/root/technocore/snapshots/snapshot-{SNAP}.json"


def run_room(room):
    try:
        out = subprocess.run(
            [sys.executable, ANALYTICS, room,
             "--samples", "2", "--interval", "5", "--limit", "150"],
            capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    if out.returncode != 0:
        return {"error": (out.stderr or "rc!=0").strip()[:200]}
    rows = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    if not rows:
        return {"error": "no parseable output"}
    agg = next((r for r in rows if r.get("aggregate")), None)
    samples = [r for r in rows if "aggregate" not in r]
    last = samples[-1] if samples else rows[-1]
    mpm = agg.get("msgs_per_minute") if agg and "msgs_per_minute" in agg else last.get("msgs_per_minute")
    return {
        "msgs_per_minute": mpm,
        "unique_did_ratio": last.get("dids_per_msg"),
        "top10_word_share_pct": last.get("top10_word_share_pct"),
        "nonce_epoch_ms_like": last.get("nonce_epoch_ms_like"),
        "nonce_random_like": last.get("nonce_random_like"),
        "last_seq": last.get("last_seq"),
        "botlikeness": (agg.get("botlikeness_note") if agg else None),
    }


result = {}
for i, room in enumerate(ROOMS):
    result[room] = run_room(room)
    if i < len(ROOMS) - 1:
        time.sleep(1)

now = datetime.now(timezone.utc)
result["_meta"] = {
    "snapshot": SNAP,
    "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "samples": 2, "interval": 5, "limit": 150,
    "did": "did:key:z6Mkj54AoaMyzCFZHEr2VGH7RFuNWb9ZELBh3j1394Aer1xe",
    "tool": "technocore_analytics.py",
}
with open(OUT, "w") as f:
    json.dump(result, f, indent=2)
print("WROTE", OUT)
print(json.dumps(result, indent=2))
