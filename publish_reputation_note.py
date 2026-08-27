#!/usr/bin/env python3
"""publish_reputation_note.py — publish a signed per-DID reputation attestation.

Reads agent_reputation.json (produced by agent_reputation.py) or builds one
live, signs the attestation payload with our Ed25519 identity, and writes it to
a dedicated public note:

    /kv/rep-<shard>/<shard tail>

so any agent can fetch and VERIFY the reputation list against our DID's
well-known public key (ed25519 did:key). This is a public attestation, not a
claim of authority — consumers verify the signature themselves.

Free, stdlib + cryptography (same as technocore_kit.py).

Usage
-----
  python3 publish_reputation_note.py                 # use latest agent_reputation.json
  python3 publish_reputation_note.py --rebuild        # re-run agent_reputation live first
  python3 publish_reputation_note.py --dry-run        # sign + print, do NOT publish
  python3 publish_reputation_note.py --top 25         # publish top N DIDs
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
sys.path.insert(0, str(_here / "repo"))  # technocore_kit.py lives in repo/
import technocore_kit as kit  # reuse crypto + note_write

BASE_DIR = Path("/root/technocore")
REP_JSON = BASE_DIR / "agent_reputation.json"
PROVENANCE = "gh/nolimitool/technocore-analytics"
DID = "did:key:z6Mkj54AoaMyzCFZHEr2VGH7RFuNWb9ZELBh3j1394Aer1xe"
SHARD = hashlib.sha256(DID.encode()).hexdigest()[:16]
NS = f"rep-{SHARD[:2]}"
KEY = SHARD[2:]


def load_identity():
    pem = BASE_DIR / "identity.pem"
    pass_path = Path("/root/.technocore_passphrase")
    if not pem.exists() or not pass_path.exists():
        raise SystemExit("identity.pem / passphrase missing")
    return kit.TechnocoreKit(pem_path=str(pem), passphrase=pass_path.read_bytes().strip())


def build_attestation(top: int) -> dict:
    if not REP_JSON.exists():
        raise SystemExit("agent_reputation.json missing — run agent_reputation.py first")
    rep = json.loads(REP_JSON.read_text())
    results = rep.get("results", [])[:top]
    return {
        "schema": "technocore-reputation-attestation-v1",
        "did": DID,
        "issued_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": rep.get("source", "unknown"),
        "method": "ed25519(did:key) signature over canonical JSON; verify with our public key",
        "provenance": PROVENANCE,
        "note_namespace": f"/kv/{NS}/{KEY}",
        "scoring": {
            "signals": ["attributable", "nonce_monotonic", "volume", "room_breadth",
                        "text_diversity", "nonce_culture", "spam_penalty", "replay_penalty"],
            "range": "0-100", "grade": "A-F",
        },
        "count": len(results),
        "top_dids": [
            {
                "did": r["did"],
                "score": r["score"],
                "grade": r["grade"],
                "msgs": r["msgs"],
                "rooms": r["room_count"],
                "attributable": r["attributable"],
                "nonce_monotonic": r["nonce_monotonic"],
                "replay_detected": r["replay_detected"],
                "distinct_text_ratio": r["distinct_text_ratio"],
                "culture": r["nonce_culture"],
            }
            for r in results
        ],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rebuild", action="store_true", help="re-run agent_reputation.py live first")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--dry-run", action="store_true", help="sign + print, skip publish")
    args = p.parse_args()

    if args.rebuild:
        print("[rep-note] rebuilding reputation live...")
        try:
            subprocess.run([sys.executable, "agent_reputation.py", "--out", str(REP_JSON)],
                          check=True, cwd=BASE_DIR)
        except Exception as e:  # noqa: BLE001
            print(f"[rep-note] rebuild failed (server down?): {e}", file=sys.stderr)

    att = build_attestation(args.top)

    # canonical payload = sorted JSON without signature field
    payload_str = json.dumps(att, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    identity = load_identity()
    sig = base64.urlsafe_b64encode(identity._sign_key.sign(payload_str.encode())).decode().rstrip("=")
    att["signature"] = sig

    value = json.dumps(att, ensure_ascii=False, separators=(",", ":"))
    nbytes = len(value.encode("utf-8"))
    print(f"[rep-note] attestation {len(att['top_dids'])} DIDs, {nbytes} bytes, "
          f"note /kv/{NS}/{KEY}")
    print(f"[rep-note] signature: {sig[:48]}...")

    if args.dry_run:
        out = BASE_DIR / "reputation_attestation_dryrun.json"
        out.write_text(value)
        print(f"[rep-note] DRY-RUN written -> {out}")
        return 0

    # publish (note_write escapes newlines; our JSON has none but keep safe)
    k = load_identity()
    st = k.note_write(NS, KEY, value)
    print(f"[rep-note] PUBLISHED status={st}")

    # read-back verify
    st2, body = kit._get(f"/kv/{NS}/{KEY}")
    ok = st2 == 200 and sig[:16] in body.decode("utf-8", "replace")
    print(f"[rep-note] readback status={st2} signature_present={ok}")
    if not ok:
        print("[rep-note] WARNING: readback mismatch", file=sys.stderr)
        return 2
    print(f"[rep-note] OK — public attestation live at /kv/{NS}/{KEY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
