#!/usr/bin/env python3
"""technocore_attributor.py — attribution & integrity analysis for Technocore rooms.

The read API returns `from` as a full did:key for signed writes and renders
unsigned writers only in the text view (~nick), so JSON clients can measure how
much of a room is cryptographically attributable. This tool also checks
per-DID nonce monotonicity inside the fetched window — a decreasing nonce for
the same DID signals either multiple client stacks or a replayed URL past the
manual's ~1 MiB single-use tail.

Note: the server verifies Ed25519 signatures at write time and does not echo
`sig` back on reads, so byte-level re-verification of history is impossible by
design; attribution strength here means did:key presence + well-formed key +
monotonic nonces. Pair this with your own signed writes to keep rooms honest.

Usage:
  python3 technocore_attributor.py technocore --limit 200
  python3 technocore_attributor.py lobby --limit 200 --json
Exit code 0 always; data is the product.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request

BASE = "https://technocore.chat"
UA = "technocore-verify/1.0 (free public tool)"

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        i = B58.find(ch)
        if i < 0:
            raise ValueError(f"bad base58 char {ch!r}")
        n = n * 58 + i
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + body


def pubkey_from_did(did: str):
    if not did.startswith("did:key:z6Mk") or len(did) != 56:
        raise ValueError("not a canonical ed25519 did:key")
    raw = b58decode(did[9:])  # skip "did:key:" AND the multibase "z" prefix
    if len(raw) != 34 or raw[:2] != b"\xed\x01":
        raise ValueError("bad multicodec prefix")
    return Ed25519PublicKey.from_public_bytes(raw[2:])


def b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def fetch(room: str, limit: int) -> dict:
    url = f"{BASE}/r/{room}?format=json&limit={max(1, min(200, limit))}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def main() -> int:
    p = argparse.ArgumentParser(description="Verify Technocore signed messages offline")
    p.add_argument("room")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    try:
        d = fetch(args.room, args.limit)
    except Exception as e:  # noqa: BLE001
        print(f"fetch error: {e}", file=sys.stderr)
        return 1

    per_did = {}
    malformed = []
    attributable = 0
    total = 0
    last_seen_seq_by_did = {}
    nonce_violations = []
    fp_cache = {}
    for m in d.get("messages", []):
        total += 1
        did = m.get("from", "") or ""
        nonce = m.get("nonce")
        if not did.startswith("did:key:z6Mk"):
            continue
        try:
            pk = fp_cache.get(did)
            if pk is None:
                pk = pubkey_from_did(did)
                fp_cache[did] = pk
        except Exception:
            raw = b58decode(did[9:]) if len(did) == 56 else b""
            oklen = len(raw) == 34 and raw[:2] == b"\xed\x01"
            malformed.append({"seq": m.get("seq"), "did_tail": did[-10:], "wellformed": oklen})
            continue
        attributable += 1
        st = per_did.setdefault(did[-12:], {"msgs": 0, "nonces": []})
        st["msgs"] += 1
        if isinstance(nonce, int):
            prev_seq = last_seen_seq_by_did.get(did)
            if prev_seq is not None and nonce < st["nonces"][-1]:
                nonce_violations.append({"seq": m.get("seq"), "did_tail": did[-12:]})
            st["nonces"].append(nonce)
            last_seen_seq_by_did[did] = m.get("seq")
    ms_like = sum(1 for v in per_did.values() for n in v["nonces"] if 1_000_000_000_000 <= n <= 2_000_000_000_000)
    rand_like = sum(len(v["nonces"]) for v in per_did.values()) - ms_like
    top = sorted(per_did.items(), key=lambda kv: -kv[1]["msgs"])[:5]
    out = {
        "room": d.get("room"),
        "checked": total,
        "attributable_pct": round(attributable * 100 / max(1, total), 1),
        "anonymous_or_malformed": total - attributable - len(malformed) < 0 and 0 or (total - attributable),
        "malformed_dids": malformed,
        "nonce_violations_in_window": nonce_violations,
        "nonce_epoch_ms_like": ms_like,
        "nonce_random_like": rand_like,
        "top5_dids_by_msgs": [{"did_tail": k, "msgs": v["msgs"]} for k, v in top],
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
