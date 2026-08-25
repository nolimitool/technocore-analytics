#!/usr/bin/env python3
"""technocore-analytics — live analytics for Technocore rooms.

Free, dependency-free (stdlib only) room analytics CLI:
  - message rate / throughput
  - unique DID counts + bot-likeness score
  - text-pattern stats (random-word spam detection)
  - nonce entropy check (clock vs random nonces)
Usage:
  python technocore_analytics.py lobby --samples 3 --interval 10 --limit 200
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime

DEFAULT_BASE = "https://technocore.chat"
UA = "technocore-analytics/1.0 (free public tool)"

# Heuristic: common English wordlist coverage. Random-word generators draw from a
# small pool; real conversation repeats content words far less uniformly.
COMMON_WORDS = {
    "the", "is", "at", "which", "on", "for", "with", "about", "this", "that",
    "it", "as", "was", "be", "are", "or", "an", "we", "you", "they", "can",
    "will", "my", "your", "what", "how", "why", "not", "but", "and",
}


def fetch(room: str, limit: int, since: int | None = None, timeout: float = 20.0):
    q = f"{DEFAULT_BASE}/r/{room}?format=json&limit={limit}"
    if since is not None:
        q += f"&since={since}"
    req = urllib.request.Request(q, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def sample_once(room: str, limit: int) -> dict:
    d = fetch(room, limit)
    msgs = d.get("messages", [])
    now = time.time()
    ts = [datetime.fromisoformat(m["ts"].replace("Z", "+00:00")).timestamp() for m in msgs]
    span = max(ts[-1] - ts[0], 1e-6)

    dids = [m["from"] for m in msgs]
    uniq = len(set(dids))
    texts = [m.get("text", "") for m in msgs]
    lens = [len(t) for t in texts]

    # word-pool analysis: ratio of messages made only of dictionary-ish short words
    word_re = re.compile(r"[A-Za-z']+")
    dictish = 0
    vocab = Counter()
    for t in texts:
        words = [w.lower() for w in word_re.findall(t)]
        vocab.update(words)
        if words and all(w in COMMON_WORDS or len(w) <= 8 for w in words):
            pass  # weak signal alone; rely on repetition below
        if words and sum(1 for w in words if w in COMMON_WORDS) >= max(1, len(words) // 4):
            dictish += 1
    top_rep = sum(c for _, c in vocab.most_common(10)) / max(1, sum(vocab.values()))

    # nonce patterns: epoch-ms timestamps vs huge random ints
    nonces = [m.get("nonce") for m in msgs if isinstance(m.get("nonce"), int)]
    ms_like = sum(1 for n in nonces if 1_000_000_000_000 <= n <= 2_000_000_000_000)
    rand_like = len(nonces) - ms_like

    return {
        "room": d.get("room"),
        "first_seq": d.get("first_seq"),
        "last_seq": d.get("last_seq"),
        "messages": len(msgs),
        "window_seconds": round(span, 2),
        "msgs_per_minute": round(len(msgs) / span * 60, 1),
        "unique_dids": uniq,
        "dids_per_msg": round(uniq / max(1, len(msgs)), 3),
        "text_len_min_med_max": (min(lens), statistics.median(lens), max(lens)) if lens else (0, 0, 0),
        "dict_style_msgs_pct": round(dictish * 100 / max(1, len(texts)), 1),
        "top10_word_share_pct": round(top_rep * 100, 1),
        "nonce_epoch_ms_like": ms_like,
        "nonce_random_like": rand_like,
        "sample_ts_epoch": round(now),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Technocore live room analytics")
    p.add_argument("room")
    p.add_argument("--limit", type=int, default=200, help="messages per sample (1-200)")
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--interval", type=float, default=10.0)
    args = p.parse_args()

    rows = []
    for i in range(max(1, args.samples)):
        try:
            row = sample_once(args.room, min(200, max(1, args.limit)))
        except Exception as e:  # noqa: BLE001 - CLI boundary
            print(f"error on sample {i+1}: {e}", file=sys.stderr)
            return 1
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        if i < args.samples - 1:
            time.sleep(max(0.5, args.interval))

    # aggregate throughput across samples by seq delta
    if len(rows) >= 2:
        ds = (rows[-1]["last_seq"] - rows[0]["last_seq"])
        dt = rows[-1]["sample_ts_epoch"] - rows[0]["sample_ts_epoch"]
        if dt > 0:
            print(json.dumps({
                "aggregate": True,
                "seq_delta": ds,
                "seconds": dt,
                "msgs_per_second": round(ds / dt, 2),
                "msgs_per_minute": round(ds / dt * 60, 1),
                "botlikeness_note": (
                    "high" if rows[-1]["top10_word_share_pct"] > 25 and rows[-1]["dids_per_msg"] > 0.9
                    else "moderate" if rows[-1]["dids_per_msg"] > 0.7 else "low"
                ),
            }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
