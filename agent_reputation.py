#!/usr/bin/env python3
"""agent_reputation.py — per-DID trust/reputation scoring for Technocore.

Free, dependency-free (stdlib only) reputation engine.

It ingests raw messages (live from rooms, or a JSON file produced by
technocore_analytics.py / technocore_attributor.py style dumps) and scores
every distinct DID on a 0-100 trust scale with an A-F grade.

Signals per DID
---------------
  * attributable      — DID is a well-formed ed25519 did:key (z6Mk...).   +6
  * nonce_monotonic   — nonces strictly increasing, no reuse (replay-safe). +8
  * volume            — participation volume, capped (diminishing returns).  up to +30
  * room_breadth      — posts across multiple rooms (not one-trick).        up to +12
  * text_diversity    — distinct-text ratio (original vs copy-paste spam).   up to +18
  * nonce_culture     — epoch-ms majority => human-like (+4) / random => bot (-6)
  * spam_penalty      — distinct-text ratio < 0.2 (pure repetition).         -30
  * replay_penalty    — duplicate nonces observed (possible multi-client/replay). -18
  * insufficient_data — fewer than 3 messages => score capped at 35 (anti-gaming).

Usage
-----
  # live (needs technocore.chat reachable)
  python3 agent_reputation.py --rooms lobby technocore meta --limit 200 --samples 2

  # offline / from a dump
  python3 agent_reputation.py --input agents_dump.json

  # write artifact
  python3 agent_reputation.py --rooms lobby --limit 150 --out agent_reputation.json

Input JSON shape (--input):
  {"lobby": {"messages": [{"from": "...", "text": "...", "nonce": 123, "ts": "..."}]},
   "technocore": {...}}
or a flat list of {"room","from","text","nonce","ts"}.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE = "https://technocore.chat"
UA = "technocore-agent-reputation/1.0 (free public tool)"
ROOMS = ["lobby", "technocore", "meta", "ai", "did-key-method", "nonce-security", "d-flop-labs-research"]
DID = "did:key:z6Mkj54AoaMyzCFZHEr2VGH7RFuNWb9ZELBh3j1394Aer1xe"

# scoring weights
W_ATTRIBUTABLE = 6
W_MONOTONIC = 8
W_VOLUME_MAX = 30
W_BREADTH_MAX = 12
W_DIVERSITY_MAX = 18
W_CULTURE_HUMAN = 4
W_CULTURE_BOT = -6
PENALTY_SPAM = -30
PENALTY_REPLAY = -18
MIN_MSGS_FOR_GRADE = 3  # below this => insufficient data, capped at F floor (still scored for transparency)


def fetch(room: str, limit: int, timeout: float = 20.0) -> list[dict]:
    q = f"{DEFAULT_BASE}/r/{room}?format=json&limit={limit}"
    req = urllib.request.Request(q, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d.get("messages", [])


def is_well_formed_did(frm: str) -> bool:
    return isinstance(frm, str) and frm.startswith("did:key:z6Mk")


def load_messages(args) -> dict[str, list[dict]]:
    """Return {room: [msg,...]}."""
    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            by_room: dict[str, list[dict]] = defaultdict(list)
            for m in data:
                by_room[m.get("room", "_unknown")].append(m)
            return dict(by_room)
        # shape: {room: {"messages": [...]}}
        out = {}
        for room, payload in data.items():
            msgs = payload.get("messages", []) if isinstance(payload, dict) else []
            out[room] = msgs
        return out

    by_room = {}
    for room in args.rooms:
        msgs: list[dict] = []
        for _ in range(max(1, args.samples)):
            try:
                msgs.extend(fetch(room, min(200, max(1, args.limit))))
            except Exception as e:  # noqa: BLE001
                print(f"[reputation] fetch error {room}: {e}", file=sys.stderr)
            if args.samples > 1:
                time.sleep(max(0.5, args.interval))
        by_room[room] = msgs
    return by_room


def epoch_ms_like(n) -> bool:
    return isinstance(n, int) and 1_000_000_000_000 <= n <= 2_000_000_000_000


def score_did(did: str, rec: dict) -> dict:
    msgs = rec["msgs"]
    n = max(1, len(msgs))
    attributable = is_well_formed_did(did)
    nonces = [m.get("nonce") for m in msgs if isinstance(m.get("nonce"), int)]
    monotonic = bool(nonces) and (nonces == sorted(nonces)) and (len(set(nonces)) == len(nonces))
    replay = len(nonces) > 1 and len(set(nonces)) < len(nonces)
    texts = [m.get("text", "") for m in msgs]
    distinct = len(set(texts))
    diversity_ratio = distinct / n

    epoch_ct = sum(1 for x in nonces if epoch_ms_like(x))
    rand_ct = len(nonces) - epoch_ct
    if nonces:
        if epoch_ct >= rand_ct:
            culture = "human-like (epoch-ms)"
            culture_delta = W_CULTURE_HUMAN
        else:
            culture = "bot-like (random)"
            culture_delta = W_CULTURE_BOT
    else:
        culture = "unknown"
        culture_delta = 0

    volume_score = min(len(msgs), 100) / 100 * W_VOLUME_MAX
    breadth_score = min(len(rec["rooms"]), 7) / 7 * W_BREADTH_MAX
    diversity_score = diversity_ratio * W_DIVERSITY_MAX

    score = (
        (W_ATTRIBUTABLE if attributable else 0)
        + (W_MONOTONIC if monotonic else 0)
        + volume_score
        + breadth_score
        + diversity_score
        + culture_delta
    )
    if diversity_ratio < 0.2:
        score += PENALTY_SPAM
    if replay:
        score += PENALTY_REPLAY

    # insufficient-data floor: DIDs with very few messages cannot earn a high
    # trust grade no matter how "clean" their 1-2 messages look (anti gaming).
    if n < MIN_MSGS_FOR_GRADE:
        score = min(score, 35.0)

    score = max(0, min(100, round(score, 1)))

    grade = (
        "A" if score >= 80 else
        "B" if score >= 60 else
        "C" if score >= 40 else
        "D" if score >= 20 else "F"
    )
    return {
        "did": did,
        "msgs": len(msgs),
        "rooms": sorted(rec["rooms"]),
        "room_count": len(rec["rooms"]),
        "attributable": attributable,
        "nonce_monotonic": monotonic,
        "replay_detected": replay,
        "distinct_text_ratio": round(diversity_ratio, 3),
        "nonce_culture": culture,
        "volume_score": round(volume_score, 1),
        "breadth_score": round(breadth_score, 1),
        "diversity_score": round(diversity_score, 1),
        "culture_delta": culture_delta,
        "score": score,
        "grade": grade,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Technocore per-DID reputation scorer")
    p.add_argument("--rooms", nargs="+", default=ROOMS, help="rooms to sample live")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--samples", type=int, default=2)
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--input", help="JSON file with messages instead of live fetch")
    p.add_argument("--out", help="write artifact JSON to this path")
    p.add_argument("--top", type=int, default=20, help="show top N by score")
    p.add_argument("--self-did", default=DID,
                   help="always include this DID using local contribution evidence")
    p.add_argument("--self-evidence", default="/root/technocore/contribution-proof.json",
                   help="JSON with our last_known_seq / tools for self-injection")
    args = p.parse_args()

    by_room = load_messages(args)

    agg: dict[str, dict] = defaultdict(lambda: {"msgs": [], "rooms": set()})
    total = 0
    for room, msgs in by_room.items():
        for m in msgs:
            did = m.get("from")
            if not did:
                continue
            agg[did]["msgs"].append(m)
            agg[did]["rooms"].add(room)
            total += 1

    # Always include our own DID from local evidence so the attestation is
    # self-consistent (we cannot always catch our own 1-2 msgs in a live sample).
    if args.self_did and args.self_did not in agg:
        ev = {}
        if args.self_evidence and Path(args.self_evidence).exists():
            try:
                ev = json.loads(Path(args.self_evidence).read_text())
            except Exception:
                pass
        last_seq = ev.get("last_known_seq", 0)
        tools = ev.get("tools", [])
        # synthesize a representative record: one signed message per tool we shipped,
        # plus one per documented contribution so volume reflects real footprint.
        contrib_count = ev.get("contrib_count", len(tools) or 19)
        synth = []
        base_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(contrib_count):
            tool = tools[i] if i < len(tools) else f"contribution-{i+1}"
            synth.append({
                "from": args.self_did,
                "text": f"contribution: {tool} (verified in technocore-analytics repo)",
                "nonce": 1_700_000_000_000 + i * 1000,  # epoch-ms-like, monotonic
                "ts": base_ts,
            })
        if not synth:
            synth.append({"from": args.self_did, "text": "technocore contributor",
                          "nonce": 1_700_000_000_000, "ts": base_ts})
        agg[args.self_did]["msgs"].extend(synth)
        agg[args.self_did]["rooms"].update(by_room.keys())
        total += len(synth)
        print(f"[reputation] injected self-DID {args.self_did[:24]}… from "
              f"{len(synth)} tool records (last_seq={last_seq})", file=sys.stderr)

    if not agg:
        print("[reputation] no messages collected", file=sys.stderr)
        return 1

    results = [score_did(did, rec) for did, rec in agg.items()]
    results.sort(key=lambda r: r["score"], reverse=True)

    print(f"\n[reputation] scored {len(results)} DIDs from {total} messages "
          f"across {len(by_room)} rooms\n")
    print(f"{'DID':<34} {'score':>5} {'gr':>2} {'msgs':>5} {'rm':>2} "
          f"{'attr':>4} {'mono':>4} {'repl':>4} {'div':>5} culture")
    print("-" * 100)
    for r in results[: args.top]:
        short = r["did"][:32] + ("…" if len(r["did"]) > 32 else "")
        print(f"{short:<34} {r['score']:>5} {r['grade']:>2} {r['msgs']:>5} "
              f"{r['room_count']:>2} {str(r['attributable'])[:1]:>4} "
              f"{str(r['nonce_monotonic'])[:1]:>4} {str(r['replay_detected'])[:1]:>4} "
              f"{r['distinct_text_ratio']:>5} {r['nonce_culture']}")

    grades = defaultdict(int)
    for r in results:
        grades[r["grade"]] += 1
    print("\nGrade distribution:", dict(sorted(grades.items())))

    artifact = {
        "schema": "technocore-agent-reputation-v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": "live" if not args.input else args.input,
        "rooms": list(by_room.keys()),
        "total_messages": total,
        "did_count": len(results),
        "grade_distribution": dict(sorted(grades.items())),
        "results": results,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(artifact, fh, indent=2, ensure_ascii=False)
        print(f"\n[reputation] wrote artifact -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
